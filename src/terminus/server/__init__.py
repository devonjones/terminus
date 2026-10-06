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
  GET  /dev/state         state plus recent log lines; exists only with --dev
"""

import argparse
import collections
import hmac
import json
import logging
import os
import secrets
import signal
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .. import __version__
from ..export import MaskError
from . import sites, views

log = logging.getLogger("terminus.server")
log.setLevel(logging.INFO)

TABS = ("connect", "panorama", "horizon", "orient", "fit", "export")
MAX_BODY = 256 * 1024  # a drop of a few hundred photo paths


class NoScope:
    """The scope link before one exists. Stage 4 replaces it with the Alpaca driver."""

    link = "none"

    def park(self):
        log.info("park: no scope linked, nothing to park")


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
    # SO_REUSEADDR on Windows lets another process bind the same port.
    allow_reuse_address = False

    def __init__(self, dev=False, token=None, scope=None, sites_root=None, jobs=None):
        super().__init__(("127.0.0.1", 0), _Handler)
        self.dev = dev
        self.sites_root = sites_root
        self.jobs = jobs or sites.Jobs()
        self.token = token or secrets.token_hex(32)
        self.scope = scope or NoScope()
        self.recent = _RecentLog()
        log.addHandler(self.recent)
        self.state = {
            "version": __version__,
            "site": None,
            "tab": TABS[0],
            "tabs": list(TABS),
            "scope": {"link": self.scope.link},
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
                self.slug = None
        return {**self.state, "site": site, "job": self.jobs.state}

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
        """Send a view of the open site; 404 when it does not have one yet."""
        out = make(self.server.site_dir())
        if out is None:
            return self._send(404, {"error": "this site has nothing to show here yet"})
        if isinstance(out, bytes):
            return self._send_bytes(out, "image/jpeg")
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
        }
        self._dispatch(routes)

    def _dispatch(self, routes):
        route = routes.get(self.path)
        if route is None:
            return self._send(404, {"error": "not found"})
        try:
            route()
        except sites.SiteError as e:
            self._send(400, {"error": str(e)})
        except MaskError as e:
            # The mask is the app's own file, but its path is nobody's business
            # in an error; the reason is.
            self._send(422, {"error": str(e).replace(str(self.server.sites_root), "<sites>")})

    def _set_tab(self, tab):
        if tab not in TABS:
            return self._send(400, {"error": "unknown tab"})
        self.server.state["tab"] = tab
        log.info("tab -> %s", tab)
        self._send(200, self.server.snapshot())

    def _new_site(self, photos):
        sv = self.server
        slug = sites.create_site(sv.sites_root, photos)
        log.info("site %s: %d photos, building", slug, len(photos))
        sv.slug, sv.state["spin"] = slug, 0.0
        sv.jobs.start(slug, sites.site_dir(sv.sites_root, slug))
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
        self.server.state["spin"] = float(deg) % 360.0
        self._send(200, self.server.snapshot())


def _stop_on_eof(server):
    try:
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
    p.add_argument("--sites", required=True, help="the folder that holds the app's sites")
    p.add_argument("--hugin", help="the bundled Hugin tools; default: look them up on PATH")
    args = p.parse_args(argv)
    if args.hugin:
        from .. import mosaic

        mosaic.HUGIN_BIN = args.hugin
    logging.basicConfig(
        stream=sys.stderr, level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    # A POSIX kill (SIGTERM) must still reach the park in `finally`.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    os.makedirs(args.sites, exist_ok=True)
    server = Sidecar(dev=args.dev, sites_root=args.sites)
    try:
        print(json.dumps({"port": server.port, "token": server.token}), flush=True)
        log.info("terminus sidecar %s on 127.0.0.1:%d dev=%s", __version__, server.port, args.dev)
        threading.Thread(target=_stop_on_eof, args=(server,), daemon=True).start()
        server.serve_forever()
    finally:
        server.scope.park()
        server.server_close()
