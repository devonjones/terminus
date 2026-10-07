"""The app's telescope link: sidecar routes, status, discovery and park. No hardware."""

import json
import socket
import threading
import time

import pytest
from test_server import call, conforms, serve  # noqa: F401 (serve is a fixture)

from terminus.client import SeestarError
from terminus.server import scope as scopes
from terminus.sweep import Pointer, PointingError, SunGuard


class FakeMount:
    """The client.Seestar subset AlpacaScope uses: south at 30 deg, arm open."""

    def __init__(self, host="h", port=0):
        self.host, self.rd, self.closed, self.parks = host, (0.0, 0.0), False, 0

    def location(self):
        return (-104.894, 39.791)  # (lon, lat)

    def equ_coord(self):
        return self.rd

    def is_eq_mode(self):
        return True

    def moving(self):
        return False

    def park(self):
        self.parks += 1
        self.rd = (0.0, -90.0)

    def close(self):
        self.closed = True


class FakeLink:
    """What the sidecar holds once linked; park and status are recorded."""

    link = "alpaca"

    def __init__(self, host, fail=None):
        self.host, self.fail, self.calls = host, fail, []

    def status(self):
        return {"link": "alpaca", "host": self.host, "eq": True, "az": 180.0, "alt": 30.0,
                "stowed": False, "moving": False, "sun": {"az": 290.0, "alt": -30.0}, "cone": 30.0}  # fmt: skip

    def park(self):
        self.calls.append("park")
        if self.fail:
            raise self.fail

    def close(self):
        self.calls.append("close")


@pytest.fixture
def linked(serve, monkeypatch):  # noqa: F811
    made = []

    def make(host):
        made.append(FakeLink(host))
        return made[-1]

    monkeypatch.setattr(scopes, "AlpacaScope", make)
    return serve(), made


@pytest.mark.parametrize("host", ["", "http://10.5.2.65", "10.5.2.65:32323", "a b", 7, None])
def test_connect_refuses_anything_but_a_host(linked, host):
    s, made = linked
    code, body = call(s, "POST", "/scope/connect", {"host": host})
    assert code == 400 and not made


def test_connect_links_once_and_the_state_says_where(linked):
    s, made = linked
    code, st = call(s, "POST", "/scope/connect", {"host": "10.5.2.65"})
    assert code == 200 and st["scope"] == {"link": "alpaca", "host": "10.5.2.65"}
    conforms("AppState", st)
    assert call(s, "POST", "/scope/connect", {"host": "10.5.2.66"})[0] == 409
    assert len(made) == 1


def test_status_conforms_linked_and_not(linked):
    s, _ = linked
    code, st = call(s, "GET", "/scope/status")
    assert code == 200 and st == {"link": "none"}
    conforms("ScopeStatus", st)
    call(s, "POST", "/scope/connect", {"host": "10.5.2.65"})
    conforms("ScopeStatus", call(s, "GET", "/scope/status")[1])


def test_park_route_parks_the_linked_scope(linked):
    s, made = linked
    call(s, "POST", "/scope/connect", {"host": "10.5.2.65"})
    code, st = call(s, "POST", "/scope/park", {})
    assert code == 200 and made[0].calls == ["park"]
    conforms("ScopeStatus", st)


def test_a_refused_park_says_why(linked):
    s, made = linked
    call(s, "POST", "/scope/connect", {"host": "10.5.2.65"})
    made[0].fail = SunGuard("no Sun-safe path to (0,25)")
    code, body = call(s, "POST", "/scope/park", {})
    assert code == 502 and "Sun-safe" in body["error"]


def test_disconnect_parks_then_unlinks(linked):
    s, made = linked
    call(s, "POST", "/scope/connect", {"host": "10.5.2.65"})
    code, st = call(s, "POST", "/scope/disconnect", {})
    assert code == 200 and made[0].calls == ["park", "close"]
    assert st["scope"] == {"link": "none", "host": None}


def test_a_failed_park_keeps_the_link(linked):
    """Dropping the link would leave the scope open with nothing able to close it."""
    s, made = linked
    call(s, "POST", "/scope/connect", {"host": "10.5.2.65"})
    made[0].fail = SeestarError("Alpaca request failed after 3 tries")
    assert call(s, "POST", "/scope/disconnect", {})[0] == 502
    assert call(s, "GET", "/state")[1]["scope"]["link"] == "alpaca"
    assert made[0].calls == ["park"]


@pytest.mark.parametrize("path", ["/scope/connect", "/scope/park", "/scope/disconnect"])
def test_scope_routes_need_the_token(linked, path):
    s, made = linked
    assert call(s, "POST", path, {"host": "10.5.2.65"}, token=False)[0] == 401
    assert not made


def test_status_reads_pointing_from_ra_dec_and_sees_a_stowed_arm():
    link = scopes.AlpacaScope("h", connect=FakeMount)
    link.sc.rd = link.sky.altaz_to_radec(180.0, 30.0)
    st = link.status()
    assert abs(st["az"] - 180.0) < 0.05 and abs(st["alt"] - 30.0) < 0.05
    assert st["stowed"] is False and st["eq"] is True
    conforms("ScopeStatus", st)
    link.sc.rd = (4.0, -90.0)
    assert link.status()["stowed"] is True


def test_discover_finds_an_alpaca_reply_and_ignores_noise():
    srv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    srv.bind(("127.0.0.1", 0))

    def answer():
        msg, addr = srv.recvfrom(64)
        assert msg == b"alpacadiscovery1"
        srv.sendto(b"not json", addr)
        srv.sendto(json.dumps({"AlpacaPort": 32323}).encode(), addr)

    threading.Thread(target=answer, daemon=True).start()
    found = scopes.discover(timeout=0.5, to=srv.getsockname())
    srv.close()
    assert found == [{"host": "127.0.0.1", "port": 32323}]
    conforms("ScopeList", {"scopes": found})


class _Ptr(Pointer):
    def __init__(self, sc):
        self.sc, self.dry, self.route = sc, False, []

    def point_to(self, az, alt):
        self.route.append((az, alt))


def test_park_goes_north_first_then_stows():
    sc = FakeMount()
    ptr = _Ptr(sc)
    ptr.park()
    assert ptr.route == [Pointer.PARK_VIA] and sc.parks == 1


def test_a_park_that_never_stows_says_so(monkeypatch):
    sc = FakeMount()
    sc.park = lambda: None  # the mount accepts and never closes
    monkeypatch.setattr(Pointer, "PARK_TIMEOUT_S", 0)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    with pytest.raises(PointingError, match="did not stow"):
        _Ptr(sc).park()
