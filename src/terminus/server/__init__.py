"""The desktop app's sidecar: a local HTTP API the Electron shell starts and stops.

    python -m terminus.server [--dev]

Binds 127.0.0.1 on a free port and prints one JSON line to stdout,
``{"port": N, "token": "..."}``. Every request must carry ``Authorization:
Bearer <token>``, a ``Host`` of 127.0.0.1/localhost on that port, and no
``Origin`` header. The only legitimate callers are the Electron main process and
a developer's curl, neither of which sends ``Origin``. A web page in the user's
browser always does, and DNS rebinding cannot forge ``Host``. That matters because
this port will eventually move a telescope.

The sidecar exits when its stdin closes, and parks the scope on the way out. The
parent holds the other end of the pipe, so a crash of the window still parks. On
Windows a kill is TerminateProcess and runs no handler, so stdin is the shutdown
path that works on every OS.

Routes:
  GET  /health      {"ok": true, "version": ...}, the UI/sidecar version handshake
  GET  /state       the app state the UI renders
  POST /state/tab   {"tab": name} -> the new state
  GET  /dev/state   state plus recent log lines; exists only with --dev
"""

import argparse
import collections
import hmac
import json
import logging
import secrets
import signal
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from .. import __version__

log = logging.getLogger("terminus.server")
log.setLevel(logging.INFO)

TABS = ("connect", "panorama", "horizon", "orient", "fit", "export")
MAX_BODY = 4096


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


class Sidecar(HTTPServer):
    def __init__(self, dev=False, token=None, scope=None):
        super().__init__(("127.0.0.1", 0), _Handler)
        self.dev = dev
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
        }

    @property
    def port(self):
        return self.server_address[1]

    def server_close(self):
        log.removeHandler(self.recent)
        super().server_close()


class _Handler(BaseHTTPRequestHandler):
    server: Sidecar

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

    def do_GET(self):
        if err := self._refused():
            return self._send(*err)
        st = self.server.state
        if self.path == "/health":
            return self._send(200, {"ok": True, "version": __version__})
        if self.path == "/state":
            return self._send(200, st)
        if self.path == "/dev/state" and self.server.dev:
            return self._send(200, {**st, "log": list(self.server.recent.lines)})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        if err := self._refused():
            return self._send(*err)
        if self.path != "/state/tab":
            return self._send(404, {"error": "not found"})
        try:
            n = int(self.headers.get("Content-Length", "0"))
            if not 0 < n <= MAX_BODY:
                raise ValueError("body size")
            tab = json.loads(self.rfile.read(n))["tab"]
        except (ValueError, KeyError, TypeError):
            return self._send(400, {"error": 'expected {"tab": name}'})
        if tab not in TABS:
            return self._send(400, {"error": "unknown tab"})
        self.server.state["tab"] = tab
        log.info("tab -> %s", tab)
        self._send(200, self.server.state)


def _stop_on_eof(server):
    sys.stdin.read()
    log.info("stdin closed, shutting down")
    server.shutdown()


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m terminus.server", description=__doc__)
    p.add_argument("--dev", action="store_true", help="enable /dev/state")
    args = p.parse_args(argv)
    logging.basicConfig(
        stream=sys.stderr, level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    # A POSIX kill (SIGTERM) must still reach the park in `finally`.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    server = Sidecar(dev=args.dev)
    try:
        print(json.dumps({"port": server.port, "token": server.token}), flush=True)
        log.info("terminus sidecar %s on 127.0.0.1:%d dev=%s", __version__, server.port, args.dev)
        threading.Thread(target=_stop_on_eof, args=(server,), daemon=True).start()
        server.serve_forever()
    finally:
        server.scope.park()
        server.server_close()
