"""The app sidecar's HTTP API: auth, Host/Origin checks, routes, and park-on-exit."""

import http.client
import json
import os
import pathlib
import subprocess
import sys
import threading
import types

import pytest

import terminus
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
        ({"Origin": "http://evil.example"}, True, 403),  # a page in the user's browser
        ({"Origin": "null"}, True, 403),
    ],
)
@pytest.mark.parametrize("method,path", [("GET", "/state"), ("POST", "/state/tab")])
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


def test_process_prints_port_and_token_and_parks_when_stdin_closes():
    p = subprocess.Popen(
        [sys.executable, "-m", "terminus.server", "--dev"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
    )
    with p:
        try:
            hello = json.loads(p.stdout.readline())
            s = types.SimpleNamespace(**hello)  # just enough for call()
            assert call(s, "GET", "/health")[0] == 200
            p.stdin.close()
            assert p.wait(timeout=20) == 0
        finally:
            p.kill()
        assert "park: no scope linked" in p.stderr.read()
