"""The app sidecar's HTTP API: auth, Host/Origin checks, routes, and park-on-exit."""

import http.client
import json
import os
import pathlib
import socket
import subprocess
import sys
import threading
import time
import types

import pytest

import terminus
import terminus.server as server_module
from terminus.server import TABS, Sidecar

ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture
def serve():
    servers = []

    def start(dev=True, scope=None):
        s = Sidecar(dev=dev, scope=scope)
        threading.Thread(target=s.serve_forever, args=(0.05,), daemon=True).start()
        servers.append(s)
        return s

    yield start
    for s in servers:
        s.shutdown()
        s.server_close()


def call(s, method, path, body=None, headers=None, token=True):
    h = {"Host": f"127.0.0.1:{s.port}"}
    if token:
        h["Authorization"] = f"Bearer {s.token}"
    h.update(headers or {})
    c = http.client.HTTPConnection("127.0.0.1", s.port, timeout=5)
    try:
        c.request(method, path, body=None if body is None else json.dumps(body), headers=h)
        r = c.getresponse()
        return r.status, json.loads(r.read())
    finally:
        c.close()


def test_health_reports_version(serve):
    s = serve()
    assert call(s, "GET", "/health") == (200, {"ok": True, "version": terminus.__version__})


def test_app_and_sidecar_versions_match():
    # The Electron main process refuses a sidecar whose /health version differs
    # from its own, so the two must be bumped together.
    pkg = json.loads((ROOT / "app" / "package.json").read_text())
    assert pkg["version"] == terminus.__version__


@pytest.mark.parametrize(
    "headers,token,code",
    [
        ({}, False, 401),
        ({"Authorization": "Bearer wrong"}, False, 401),
        ({"Host": "evil.example:80"}, True, 403),  # DNS rebinding
        ({"Host": "127.0.0.1"}, True, 403),
        ({"Host": "127.0.0.1:1"}, True, 403),
        ({"Origin": "http://evil.example"}, True, 403),  # a page in the user's browser
        ({"Origin": "null"}, True, 403),
    ],
)
@pytest.mark.parametrize(
    "method,path",
    [("GET", "/health"), ("GET", "/state"), ("GET", "/dev/state"), ("POST", "/state/tab")],
)
def test_refuses_without_token_host_or_with_origin(serve, method, path, headers, token, code):
    s = serve()
    status, _ = call(s, method, path, {"tab": "fit"}, headers=headers, token=token)
    assert status == code
    assert s.state["tab"] == "connect"


def test_localhost_host_is_accepted(serve):
    s = serve()
    assert call(s, "GET", "/health", headers={"Host": f"localhost:{s.port}"})[0] == 200


def test_state_defaults(serve):
    s = serve()
    code, st = call(s, "GET", "/state")
    assert code == 200
    assert st == {
        "version": terminus.__version__,
        "site": None,
        "tab": "connect",
        "tabs": list(TABS),
        "scope": {"link": "none"},
        "sun_mode": "sun",
    }


def test_dev_state_only_with_dev(serve):
    assert call(serve(dev=False), "GET", "/dev/state")[0] == 404
    s = serve(dev=True)
    call(s, "POST", "/state/tab", {"tab": "orient"})
    code, st = call(s, "GET", "/dev/state")
    assert code == 200
    assert st["tab"] == "orient"
    assert any("tab -> orient" in line for line in st["log"])


def test_set_tab(serve):
    s = serve()
    code, st = call(s, "POST", "/state/tab", {"tab": "export"})
    assert (code, st["tab"]) == (200, "export")
    assert call(s, "GET", "/state")[1]["tab"] == "export"


@pytest.mark.parametrize("body", [{"tab": "nope"}, {"tab": 3}, {"x": 1}, [1], "fit"])
def test_set_tab_rejects_bad_bodies(serve, body):
    s = serve()
    assert call(s, "POST", "/state/tab", body)[0] == 400
    assert s.state["tab"] == "connect"


def test_set_tab_rejects_empty_and_oversized_bodies(serve):
    s = serve()
    for size in ("0", str(10**6)):
        assert call(s, "POST", "/state/tab", headers={"Content-Length": size})[0] == 400


def test_sun_mode_cannot_be_set_through_the_api(serve):
    s = serve()
    for path in ("/state/sun_mode", "/state"):
        assert call(s, "POST", path, {"sun_mode": "shade"})[0] == 404
    assert s.state["sun_mode"] == "sun"


def test_unknown_route_404(serve):
    assert call(serve(), "GET", "/nope")[0] == 404


def test_errors_do_not_echo_the_token(serve):
    s = serve()
    _, body = call(s, "GET", "/state", headers={"Authorization": "Bearer guess"}, token=False)
    assert s.token not in json.dumps(body)


def test_an_idle_connection_does_not_block_shutdown(serve):
    s = serve()
    with socket.create_connection(("127.0.0.1", s.port)):  # connects, sends nothing
        time.sleep(0.2)
        done = threading.Thread(target=s.shutdown)
        done.start()
        done.join(timeout=3)
        assert not done.is_alive()


def test_unusable_stdin_shuts_down_instead_of_running_forever(serve, monkeypatch, caplog):
    s = serve()
    monkeypatch.setattr(sys, "stdin", None)  # e.g. launched with no stdin at all
    t = threading.Thread(target=server_module._stop_on_eof, args=(s,), daemon=True)
    t.start()
    t.join(timeout=3)
    assert not t.is_alive()  # shutdown() returned, so serve_forever stopped
    assert "cannot read stdin, shutting down" in caplog.text


def _sidecar_process():
    p = subprocess.Popen(
        [sys.executable, "-m", "terminus.server", "--dev"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
    )
    watchdog = threading.Timer(30, p.kill)  # readline() has no timeout of its own
    watchdog.daemon = True  # never hold up pytest's exit
    watchdog.start()
    hello = json.loads(p.stdout.readline() or "null")
    assert hello, "sidecar printed no startup line"
    return p, types.SimpleNamespace(**hello), watchdog  # namespace: just enough for call()


@pytest.mark.parametrize("stop", ["stdin", "sigterm"])
def test_process_prints_port_and_token_and_parks_on_exit(stop):
    if stop == "sigterm" and sys.platform == "win32":
        pytest.skip("Windows has no SIGTERM handler path; stdin is how it stops")
    p, s, watchdog = _sidecar_process()
    with p:
        try:
            assert call(s, "GET", "/health")[0] == 200
            if stop == "stdin":
                p.stdin.close()
            else:
                p.terminate()
            assert p.wait(timeout=20) == 0
        finally:
            watchdog.cancel()
            p.kill()
        assert "park: no scope linked" in p.stderr.read()
