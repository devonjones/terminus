"""The desktop app's sidecar: a local HTTP API the Electron shell starts and stops.

    python -m terminus.server [--dev]

Binds 127.0.0.1 on a free port and prints one JSON line to stdout,
``{"port": N, "token": "..."}``. Every request must carry ``Authorization:
Bearer <token>``, a ``Host`` of 127.0.0.1/localhost on that port, and no
``Origin`` header. The only legitimate callers are the Electron main process and
a developer's curl, neither of which sends ``Origin``; browsers send it on
cross-origin POSTs. A page cannot know the token, and the ``Host`` check stops
DNS rebinding. That matters because this port will eventually move a telescope.

The sidecar exits when its stdin closes, and parks the scope on the way out. The
parent holds the other end of the pipe, so if the Electron main process dies the
sidecar still parks. On Windows a kill is TerminateProcess and runs no handler,
so stdin is the shutdown path that works on every OS.

Routes (payload shapes: schema.json beside this file):
  GET  /health            {"ok": true, "version": ...}, the UI/sidecar version handshake
  GET  /state             the app state the UI renders
  POST /state/tab         {"tab": name} -> the new state
  GET  /sites             {"sites": [summary, ...]}
  POST /sites             {"photos": [path, ...]}: copy them into a new site, build it
  POST /site/open         {"slug": name} -> the new state
  POST /site/spin         {"deg": number}: the rough yaw of an unoriented site
  GET  /site/horizon      the open site's columns, orientation and fit record
  GET  /site/disc         the polar disc's overlays, in disc pixels
  GET  /site/panorama.jpg, /site/disc.jpg   the photographs
  GET  /site/frames       the photos: where each sits on the panorama, which are off
  GET  /site/frame/footprint.png?name=, /site/frame/thumb.jpg?name=   one photo
  GET  /site/disagree.png where the photos outvoted each other on sky
  GET  /site/outline.png  the actual horizon: the sky's outline, as a disc overlay
  GET  /site/progress.png the horizon so far, while a build is judging
  GET  /site/frame/layer.webp?layer=, /site/frame/verdict.png?layer=   a build's
                          remapped photo and its sky verdict, as they are made
  POST /site/rename       {"slug": name, "name": text}: the site's display name
  POST /site/delete       {"slug": name}: remove the site (not while it is building)
  POST /site/frames       {"off": [name, ...], "restitch": bool}: rebuild without those
                          photos, re-blending the stitched ones or stitching afresh
  GET  /scope/discover    {"scopes": [{"host", "port"}, ...]}: Alpaca servers on the network
  GET  /scope/status      the linked telescope: pointing (from RA/Dec), arm, Sun
  POST /scope/connect     {"host": name}: link a Seestar over Alpaca -> the new state
  POST /scope/park        Sun-guarded park; the scope's status after it
  POST /scope/disconnect  park, then unlink -> the new state
  GET  /dev/state         state plus recent log lines; exists only with --dev
"""

import argparse
import collections
import hmac
import json
import logging
import os
import re
import secrets
import shutil
import signal
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import yaml

from .. import __version__
from ..client import SeestarError
from ..export import MaskError
from ..sweep import PointingError, SunGuard
from . import scope as scopes
from . import sites, views

log = logging.getLogger("terminus.server")
log.setLevel(logging.INFO)

TABS = ("connect", "panorama", "horizon", "orient", "fit", "export")
MAX_BODY = 256 * 1024  # a drop of a few hundred photo paths
HOST = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?")  # no scheme, port or path


class _RecentLog(logging.Handler):
    """Keep the last lines for /dev/state, so a developer sees what the sidecar did."""

    def __init__(self, n=200):
        super().__init__()
        self.lines = collections.deque(maxlen=n)

    def emit(self, record):
        self.lines.append(self.format(record))


class Sidecar(ThreadingHTTPServer):
    # Threads, so an idle or slow connection cannot hold up shutdown (and the
    # park behind it); daemon threads, so shutdown does not wait for them.
    daemon_threads = True
    # The listen backlog. The default of 5 refuses connections on Windows when the
    # app fetches a site's photos in parallel (Linux absorbs the burst instead).
    request_queue_size = 64
    # SO_REUSEADDR on Windows lets another process bind the same port.
    allow_reuse_address = False

    def __init__(self, dev=False, token=None, scope=None, sites_root=None, jobs=None):
        super().__init__(("127.0.0.1", 0), _Handler)
        self.dev = dev
        self.sites_root = sites_root
        self.jobs = jobs or sites.Jobs()
        self.token = token or secrets.token_hex(32)
        self.scope = scope or scopes.NoScope()
        self.linking = threading.Lock()  # connect, disconnect and park take turns
        self.recent = _RecentLog()
        log.addHandler(self.recent)
        self.state = {
            "version": __version__,
            "site": None,
            "tab": TABS[0],
            "tabs": list(TABS),
            # The in-sun / in-shade switch. Defaults to SUN (the cautious answer)
            # and only the human sets it; no route here changes it.
            "sun_mode": "sun",
            # Rough yaw of an unoriented site: how far the user spun the disc.
            "spin": 0.0,
        }
        self.slug = None

    def snapshot(self):
        """The state with the live parts (open site, pipeline job) filled in."""
        site = None
        if self.slug is not None:
            try:
                site = sites.summary(self.sites_root, self.slug)
            except sites.SiteError:
                log.warning("site %s is gone from disk; closing it", self.slug)
                self.slug = None
        scope = {"link": self.scope.link, "host": self.scope.host}
        return {**self.state, "scope": scope, "site": site, "job": self.jobs.state}

    def site_dir(self):
        if self.slug is None:
            raise sites.SiteError("no site is open")
        return sites.site_dir(self.sites_root, self.slug)

    @property
    def port(self):
        return self.server_address[1]

    def server_close(self):
        log.removeHandler(self.recent)
        super().server_close()


class _Handler(BaseHTTPRequestHandler):
    server: Sidecar
    timeout = 10  # seconds a connection may sit idle

    def log_message(self, fmt, *args):
        log.info("%s %s", self.address_string(), fmt % args)

    def _send(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _refused(self):
        """Return the error to send, or None if the caller is allowed in."""
        port = self.server.port
        if self.headers.get("Host") not in (f"127.0.0.1:{port}", f"localhost:{port}"):
            return 403, {"error": "bad host"}
        if "Origin" in self.headers:
            return 403, {"error": "browser origins are not allowed"}
        auth = self.headers.get("Authorization", "")
        if not hmac.compare_digest(auth.encode(), f"Bearer {self.server.token}".encode()):
            return 401, {"error": "missing or wrong token"}
        return None

    def _send_bytes(self, data, kind):
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get("Content-Length", "0"))
        if not 0 < n <= MAX_BODY:
            raise ValueError("body size")
        body = json.loads(self.rfile.read(n))
        if not isinstance(body, dict):
            raise ValueError("body must be an object")
        return body

    def _site_view(self, make):
        """Send a view of the open site; 204 when it has nothing there yet (so a
        404 always means a wrong route, never an empty site)."""
        out = make(self.server.site_dir())
        if out is None:
            self.send_response(204)
            self.end_headers()
            return
        if isinstance(out, bytes):
            kind = "image/jpeg"
            if out[:4] == b"\x89PNG":
                kind = "image/png"
            elif out[8:12] == b"WEBP":
                kind = "image/webp"
            return self._send_bytes(out, kind)
        return self._send(200, out)

    def do_GET(self):
        if err := self._refused():
            return self._send(*err)
        sv = self.server
        routes = {
            "/health": lambda: self._send(200, {"ok": True, "version": __version__}),
            "/state": lambda: self._send(200, sv.snapshot()),
            "/sites": lambda: self._send(
                200,
                {
                    "sites": [
                        sites.summary(sv.sites_root, s) for s in sites.list_sites(sv.sites_root)
                    ]
                },
            ),
            "/site/horizon": lambda: self._site_view(views.horizon),
            "/site/disc": lambda: self._site_view(views.disc),
            "/site/panorama.jpg": lambda: self._site_view(views.panorama_jpeg),
            "/site/disc.jpg": lambda: self._site_view(views.disc_jpeg),
            "/site/frames": lambda: self._site_view(views.frames),
            "/site/frame/footprint.png": lambda: self._site_view(
                lambda d: views.footprint_png(d, self._name())
            ),
            "/site/frame/thumb.jpg": lambda: self._site_view(
                lambda d: views.thumb_jpg(d, self._name())
            ),
            "/site/disagree.png": lambda: self._site_view(views.disagree_png),
            "/site/outline.png": lambda: self._site_view(views.outline_png),
            "/site/progress.png": lambda: self._site_view(views.progress_png),
            "/site/frame/layer.webp": lambda: self._site_view(
                lambda d: views.layer_webp(d, self._query("layer"))
            ),
            "/site/frame/verdict.png": lambda: self._site_view(
                lambda d: views.verdict_png(d, self._query("layer"))
            ),
            "/scope/discover": lambda: self._send(200, {"scopes": scopes.discover()}),
            "/scope/status": lambda: self._send(200, sv.scope.status()),
        }
        if sv.dev:
            routes["/dev/state"] = lambda: self._send(
                200, {**sv.snapshot(), "log": list(sv.recent.lines)}
            )
        self._dispatch(routes)

    def do_POST(self):
        if err := self._refused():
            return self._send(*err)
        try:
            body = self._body()
        except (ValueError, TypeError):
            return self._send(400, {"error": "expected a JSON object body"})
        routes = {
            "/state/tab": lambda: self._set_tab(body.get("tab")),
            "/sites": lambda: self._new_site(body.get("photos")),
            "/site/open": lambda: self._open_site(body.get("slug")),
            "/site/spin": lambda: self._set_spin(body.get("deg")),
            "/site/frames": lambda: self._curate(body.get("off"), body.get("restitch", False)),
            "/site/rename": lambda: self._rename(body.get("slug"), body.get("name")),
            "/site/delete": lambda: self._delete(body.get("slug")),
            "/scope/connect": lambda: self._connect(body.get("host")),
            "/scope/park": self._park,
            "/scope/disconnect": self._disconnect,
        }
        self._dispatch(routes)

    def _query(self, key):
        values = parse_qs(urlsplit(self.path).query).get(key, [])
        if len(values) != 1:
            raise sites.SiteError(f"expected one ?{key}=")
        return values[0]

    def _name(self):
        return self._query("name")

    def _dispatch(self, routes):
        route = routes.get(urlsplit(self.path).path)
        if route is None:
            return self._send(404, {"error": "not found"})
        try:
            route()
        except sites.SiteError as e:
            self._send(400, {"error": str(e)})
        except (SeestarError, SunGuard, PointingError) as e:
            # The scope said no, or could not be reached: the reason is the message.
            log.warning("%s: %s", self.path, e)
            self._send(502, {"error": str(e)})
        except MaskError as e:
            # The mask is the app's own file, but its path is nobody's business
            # in an error; the reason is.
            self._send(422, {"error": str(e).replace(str(self.server.sites_root), "<sites>")})
        except (OSError, ValueError, yaml.YAMLError) as e:
            log.exception("%s: a site file could not be read", self.path)
            self._send(422, {"error": f"this site's files could not be read ({type(e).__name__})"})
        except Exception:
            log.exception("%s failed", self.path)
            self._send(500, {"error": "internal error; see sidecar.log"})

    def _connect(self, host):
        if not isinstance(host, str) or not HOST.fullmatch(host):
            return self._send(400, {"error": "host must be an IP address or host name"})
        sv = self.server
        with sv.linking:
            if sv.scope.link != "none":
                return self._send(409, {"error": "a telescope is already linked; disconnect first"})
            sv.scope = scopes.AlpacaScope(host)
        log.info("scope linked: %s", host)
        self._send(200, sv.snapshot())

    def _park(self):
        with self.server.linking:
            self.server.scope.park()
        self._send(200, self.server.scope.status())

    def _disconnect(self):
        """Park, then let go. A park that fails keeps the link: dropping it would
        leave the scope open with nothing able to close it."""
        sv = self.server
        with sv.linking:
            sv.scope.park()
            sv.scope.close()
            sv.scope = scopes.NoScope()
        log.info("scope unlinked")
        self._send(200, sv.snapshot())

    def _set_tab(self, tab):
        if tab not in TABS:
            return self._send(400, {"error": "unknown tab"})
        self.server.state["tab"] = tab
        log.info("tab -> %s", tab)
        self._send(200, self.server.snapshot())

    def _new_site(self, photos):
        sv = self.server
        if sv.jobs.busy():  # refuse before copying anything or changing the open site
            raise sites.SiteError("a site is already being built")
        slug = sites.create_site(sv.sites_root, photos)
        d = sites.site_dir(sv.sites_root, slug)
        log.info("site %s: %d photos, building", slug, len(photos))
        try:
            sv.jobs.start(slug, d)
        except sites.SiteError:  # another drop won the race to build
            shutil.rmtree(d, ignore_errors=True)
            raise
        sv.slug, sv.state["spin"] = slug, 0.0
        self._send(200, sv.snapshot())

    def _curate(self, off, restitch):
        """Turn photos off (or back on) and rebuild the open site without them. A
        photo the last stitch left out has no layer to re-blend: bringing it back
        takes a re-stitch."""
        sv = self.server
        d = sv.site_dir()
        listed = {f["name"] for f in (views.frames(d) or {}).get("frames", ())}
        if not isinstance(off, list) or not all(isinstance(n, str) and n in listed for n in off):
            raise sites.SiteError("off must list photos of this site")
        if not isinstance(restitch, bool):
            raise sites.SiteError("restitch must be true or false")
        if sv.jobs.busy():
            raise sites.SiteError("a site is already being built")
        sites.set_frames_off(d, off)
        kind = "build" if restitch else "reblend"
        log.info("site %s: %d photos off, %s", sv.slug, len(off), kind)
        sv.jobs.start(sv.slug, d, kind)
        self._send(200, sv.snapshot())

    def _rename(self, slug, name):
        sites.rename_site(self.server.sites_root, slug, name)
        log.info("site %s renamed", slug)
        self._send(200, self.server.snapshot())

    def _delete(self, slug):
        sv = self.server
        sites.site_dir(sv.sites_root, slug)  # refuses an unknown or malformed name
        job = sv.jobs.state
        if sv.jobs.busy() and job and job["site"] == slug:
            raise sites.SiteError("this site is being built; wait for it to finish")
        sites.delete_site(sv.sites_root, slug)
        if sv.slug == slug:
            sv.slug, sv.state["spin"] = None, 0.0
        log.info("site %s deleted", slug)
        self._send(200, sv.snapshot())

    def _open_site(self, slug):
        sv = self.server
        sites.site_dir(sv.sites_root, slug)  # refuses an unknown or malformed name
        sv.slug, sv.state["spin"] = slug, 0.0
        log.info("site %s opened", slug)
        self._send(200, sv.snapshot())

    def _set_spin(self, deg):
        if isinstance(deg, bool) or not isinstance(deg, (int, float)) or not -720 <= deg <= 720:
            return self._send(400, {"error": "deg must be a number of degrees"})
        spin = float(deg) % 360.0
        self.server.state["spin"] = spin if spin < 360.0 else 0.0  # -1e-20 % 360 == 360.0
        self._send(200, self.server.snapshot())


def _stop_on_eof(server):
    try:
        if os.name == "nt":
            sites._poll_until_closed()  # never block in a stdin read on Windows (E-19)
        else:
            sys.stdin.read()
        log.info("stdin closed, shutting down")
    except Exception:
        # No usable stdin means no way to be told to stop: stop now, not never.
        log.exception("cannot read stdin, shutting down")
    finally:
        server.shutdown()


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m terminus.server", description=__doc__)
    p.add_argument("--dev", action="store_true", help="enable /dev/state")
    p.add_argument("--sites", help="the folder that holds the app's sites (required to serve)")
    p.add_argument("--hugin", help="the bundled Hugin tools; default: look them up on PATH")
    p.add_argument("--pipeline", metavar="SITE", help=argparse.SUPPRESS)  # the build child
    p.add_argument("--kind", choices=sites.KINDS, default="build", help=argparse.SUPPRESS)
    args = p.parse_args(argv)
    if args.hugin:
        from .. import mosaic

        mosaic.HUGIN_BIN = args.hugin
    if args.pipeline:
        threading.Thread(target=sites.exit_on_eof, daemon=True).start()
        return sites.run_pipeline(args.pipeline, args.kind)
    if not args.sites:
        p.error("--sites is required")
    logging.basicConfig(
        stream=sys.stderr, level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    # A POSIX kill (SIGTERM) must still reach the park in `finally`.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    os.makedirs(args.sites, exist_ok=True)
    server = Sidecar(dev=args.dev, sites_root=args.sites, jobs=sites.Jobs(hugin=args.hugin))
    try:
        print(json.dumps({"port": server.port, "token": server.token}), flush=True)
        log.info("terminus sidecar %s on 127.0.0.1:%d dev=%s", __version__, server.port, args.dev)
        threading.Thread(target=_stop_on_eof, args=(server,), daemon=True).start()
        server.serve_forever()
    finally:
        _shutdown(server)


def _shutdown(server):
    """Park first: the telescope outranks a half-done build. Each later step
    still runs if the one before it fails."""
    try:
        try:
            server.scope.park()
        finally:
            server.scope.close()
    finally:
        try:
            server.jobs.stop()
        finally:
            server.server_close()
