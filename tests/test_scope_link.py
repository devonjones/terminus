"""The app's telescope link: sidecar routes, status, discovery and park. No hardware."""

import json
import socket
import threading
import time

import numpy as np
import pytest
from test_server import call, conforms, serve  # noqa: F401 (serve is a fixture)

from terminus.client import SeestarError
from terminus.server import scope as scopes
from terminus.sweep import Pointer, PointingError, PointingUnreadable, Sky, SunGuard


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


@pytest.mark.parametrize(
    "fail, why",
    [
        (SunGuard("no Sun-safe path to (0,25)"), "Sun-safe"),
        (PointingError("park did not stow in 90 s"), "did not stow"),
        (SeestarError("Alpaca request failed after 3 tries"), "3 tries"),
    ],
)
def test_a_refused_park_says_why(linked, fail, why):
    s, made = linked
    call(s, "POST", "/scope/connect", {"host": "10.5.2.65"})
    made[0].fail = fail
    code, body = call(s, "POST", "/scope/park", {})
    assert code == 502 and why in body["error"]


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
    """Pointer with the slew recorded and the stow's Sun separation chosen."""

    def __init__(self, sc, lat=39.8, stow_sep=180.0):
        self.sc, self.dry, self.cone, self.route = sc, False, 30.0, []
        self.sky = Sky(lat, -104.9)
        self.stow_sep = stow_sep

    def point_to(self, az, alt):
        self.route.append((az, alt))

    def path_min_sep(self, rd0, rd1):
        assert rd1[1] == -90.0, "the stow leg runs to Dec -90"
        return self.stow_sep


def test_park_goes_via_the_up_pole_then_stows():
    sc = FakeMount()
    ptr = _Ptr(sc)
    ptr.park()
    assert ptr.route == [(0.0, Pointer.PARK_VIA_ALT)] and sc.parks == 1


def test_a_southern_park_goes_via_south():
    """Due north is where the southern midday Sun is."""
    sc = FakeMount()
    ptr = _Ptr(sc, lat=-33.9)
    ptr.park()
    assert ptr.route == [(180.0, Pointer.PARK_VIA_ALT)]


def test_a_stow_that_would_pass_the_sun_is_refused():
    """The firmware picks the stow's path; it is checked like any slew."""
    sc = FakeMount()
    with pytest.raises(SunGuard, match="stow would pass"):
        _Ptr(sc, stow_sep=5.0).park()
    assert sc.parks == 0


def test_parking_a_stowed_scope_does_nothing():
    """Park and disconnect must work on a closed scope."""
    sc = FakeMount()
    sc.rd = (4.0, -90.0)
    ptr = _Ptr(sc)
    ptr.park()
    assert ptr.route == [] and sc.parks == 0


def test_a_park_that_never_stows_says_so(monkeypatch):
    sc = FakeMount()
    sc.park = lambda: None  # the mount accepts and never closes
    monkeypatch.setattr(Pointer, "PARK_TIMEOUT_S", 0)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    with pytest.raises(PointingError, match="did not stow"):
        _Ptr(sc).park()


def test_shutdown_waits_for_a_park_already_under_way():
    """Two parks driving the mount at once is what the lock prevents."""
    import types

    import terminus.server as server_module

    running, most = [0], [0]

    def park():
        running[0] += 1
        most[0] = max(most[0], running[0])
        time.sleep(0.2)
        running[0] -= 1

    server = types.SimpleNamespace(
        linking=threading.Lock(),
        scope=types.SimpleNamespace(park=park, close=lambda: None),
        jobs=types.SimpleNamespace(stop=lambda: None),
        server_close=lambda: None,
    )

    def route_park():
        with server.linking:
            park()

    t = threading.Thread(target=route_park)
    t.start()
    time.sleep(0.05)
    server_module._shutdown(server)
    t.join()
    assert most[0] == 1


def test_a_failed_search_is_a_scope_error_not_a_site_error(linked, monkeypatch):
    s, _ = linked

    def no_network(*a, **k):
        raise OSError("Network is unreachable")

    monkeypatch.setattr(scopes, "_broadcast", no_network)
    code, body = call(s, "GET", "/scope/discover")
    assert code == 502 and "search for telescopes" in body["error"]


@pytest.mark.parametrize("path", ["/scope/discover", "/scope/status"])
@pytest.mark.parametrize(
    "bad",
    [
        {"token": False},
        {"headers": {"Host": "evil.example"}},
        {"headers": {"Origin": "https://evil.example"}},
    ],
)
def test_scope_reads_refuse_strangers(linked, monkeypatch, path, bad):
    s, _ = linked
    monkeypatch.setattr(scopes, "_broadcast", lambda *a: pytest.fail("searched for a stranger"))
    assert call(s, "GET", path, **bad)[0] in (401, 403)


def test_discover_through_the_sidecar(linked, monkeypatch):
    s, _ = linked
    monkeypatch.setattr(scopes, "_broadcast", lambda *a: [{"host": "10.5.2.65", "port": 32323}])
    code, body = call(s, "GET", "/scope/discover")
    assert code == 200 and body == {"scopes": [{"host": "10.5.2.65", "port": 32323}]}
    conforms("ScopeList", body)


def test_a_failed_connect_leaves_nothing_linked(serve, monkeypatch):  # noqa: F811
    def unreachable(host):
        raise SeestarError("Alpaca request failed after 3 tries")

    monkeypatch.setattr(scopes, "AlpacaScope", unreachable)
    s = serve()
    code, body = call(s, "POST", "/scope/connect", {"host": "10.5.2.65"})
    assert code == 502 and "3 tries" in body["error"]
    assert call(s, "GET", "/state")[1]["scope"] == {"link": "none", "host": None}


def test_status_says_so_when_the_pointing_cannot_be_read():
    link = scopes.AlpacaScope("h", connect=FakeMount)
    link.sc.rd = None
    with pytest.raises(SeestarError, match="where it is pointing"):
        link.status()


def test_the_cli_links_over_alpaca_without_a_key(monkeypatch):
    from terminus import alpaca, cli

    monkeypatch.setattr(alpaca, "Alpaca", lambda host: ("alpaca", host))
    assert cli._connect({"link": "alpaca", "host": "10.5.2.65"}) == ("alpaca", "10.5.2.65")


def test_cli_park_reports_a_sun_refusal_plainly(monkeypatch):
    from terminus import cli

    def refuse(self):
        raise SunGuard("the stow would pass 5.0 deg from the Sun")

    monkeypatch.setattr(cli, "_sky", lambda sc, cfg: Sky(39.8, -104.9))
    monkeypatch.setattr(Pointer, "park", refuse)
    cfg = {"sweep": {"sun_cone_deg": 30.0, "slew_step_deg": 5.0}}
    with pytest.raises(SeestarError, match="park: the stow would pass"):
        cli.cmd_park(FakeMount(), cfg, None)


def test_a_dec_that_rounds_past_the_pole_still_converts():
    """Interpolating a path to Dec -90 lands on -90.00000000000001 for about a fifth
    of northern daytime parks; astropy refused it and the stow-leg check crashed."""
    az, alt = Sky(39.79, -104.89).radec_to_altaz(6.0407, -90.00000000000001)
    assert abs(alt + 39.79) < 0.1  # the south pole, below a northern horizon


def test_only_the_south_pole_is_stowed():
    """The stow is Dec -90; a scope near the north pole is pointing, not parked."""
    from terminus.sweep import is_stowed

    assert is_stowed((4.0, -90.0)) and is_stowed((4.0, -89.85))
    assert not is_stowed((4.0, 89.8)) and not is_stowed(None)


def test_an_unreadable_pointing_is_not_taken_for_the_zenith():
    sc = FakeMount()
    sc.rd = None
    ptr = Pointer(sc, Sky(39.79, -104.89), 30, 5)
    with pytest.raises(PointingUnreadable):
        ptr.current_azalt()


def test_a_park_that_loses_the_pointing_before_the_stow_refuses():
    sc = FakeMount()
    ptr = _Ptr(sc)

    def lose_it(az, alt):
        sc.rd = None

    ptr.point_to = lose_it
    with pytest.raises(PointingUnreadable, match="before the stow"):
        ptr.park()
    assert sc.parks == 0


MORNING = "2026-10-07T14:25:00Z"  # Sun about az 110 alt 14 at the S50 spot


class _Mount(FakeMount):
    """Arrives wherever it is sent, at once, and records each goto. Declares
    Alpaca's floors."""

    min_alt_deg, escape_floor_deg = 0.0, 5.0

    def __init__(self, rd):
        super().__init__()
        self.rd, self.gotos = rd, []

    def goto(self, ra, dec):
        self.gotos.append((ra, dec))
        self.rd = (ra, dec)


def _morning(monkeypatch, when=MORNING):
    from astropy.time import Time

    from terminus import sweep

    monkeypatch.setattr(sweep, "_now", lambda: Time(when))
    monkeypatch.setattr(sweep.time, "sleep", lambda s: None)
    return Sky(39.791668, -104.894165)


def test_routes_stay_above_the_mounts_floor(monkeypatch):
    """2026-10-07: the only Sun-safe route west ran the RA axis the long way, through
    alt -36.6, and Alpaca refused it as below the horizon."""
    sky = _morning(monkeypatch)
    ptr = Pointer(_Mount(None), sky, 30.0, 5.0)
    here = sky.altaz_to_radec(75.8, 1.2)
    west = sky.altaz_to_radec(260.0, 30.0)
    ptr.MIN_ALT_DEG = -90.0
    assert ptr.plan_route(here, west) is not None  # control: below the floor it plans
    ptr.MIN_ALT_DEG = 0.0
    assert ptr.plan_route(here, west) is None


def test_a_link_declares_its_mounts_floors():
    from unittest.mock import MagicMock

    from terminus.alpaca import Alpaca

    assert (Alpaca.min_alt_deg, Alpaca.escape_floor_deg) == (0.0, 5.0)

    class AlpacaLike(FakeMount):
        min_alt_deg, escape_floor_deg = Alpaca.min_alt_deg, Alpaca.escape_floor_deg

    link = scopes.AlpacaScope("h", connect=AlpacaLike)
    assert (link.ptr.MIN_ALT_DEG, link.ptr.ESCAPE_TURN_FLOOR_DEG) == (0.0, 5.0)
    plain = Pointer(MagicMock(), Sky(39.8, -104.9), 30, 5)  # the native link declares nothing
    assert (plain.MIN_ALT_DEG, plain.ESCAPE_TURN_FLOOR_DEG) == (-5.0, None)


class FrameLink(FakeLink):
    def __init__(self, host):
        super().__init__(host)
        self.points, self.exposures = [], []

    def point(self, az, alt):
        self.points.append((az, alt))

    def frame(self, exposure_s):
        self.exposures.append(exposure_s)
        return {"exposure_ms": exposure_s * 1000, "median": 19360.0, "saturated": 0.0}

    def preview_jpeg(self):
        return scopes.preview_jpeg(np.full((4, 4), 1000, np.uint16)) if self.exposures else None


@pytest.fixture
def framed(serve, monkeypatch):  # noqa: F811
    made = []
    monkeypatch.setattr(
        scopes, "AlpacaScope", lambda host: made.append(FrameLink(host)) or made[-1]
    )
    s = serve()
    call(s, "POST", "/scope/connect", {"host": "10.5.2.65"})
    return s, made


def test_point_goes_to_the_link_and_answers_with_status(framed):
    s, made = framed
    code, st = call(s, "POST", "/scope/point", {"az": 260, "alt": 30.5})
    assert code == 200 and made[0].points == [(260.0, 30.5)]
    conforms("ScopeStatus", st)


@pytest.mark.parametrize(
    "body", [{"az": 360, "alt": 30}, {"az": -1, "alt": 30}, {"az": 10, "alt": -6},
             {"az": 10, "alt": 91}, {"az": "10", "alt": 30}, {"az": True, "alt": 30}, {}],
)  # fmt: skip
def test_point_refuses_a_bad_target(framed, body):
    s, made = framed
    assert call(s, "POST", "/scope/point", body)[0] == 400 and made[0].points == []


def test_a_sun_refusal_on_point_says_why(framed):
    s, made = framed

    def refuse(az, alt):
        raise SunGuard("(110,15) is 3.0 deg from Sun (< 30.0)")

    made[0].point = refuse
    code, body = call(s, "POST", "/scope/point", {"az": 110, "alt": 15})
    assert code == 502 and "from Sun" in body["error"]


def test_a_frame_and_its_preview(framed):
    s, made = framed
    assert call(s, "GET", "/scope/frame.jpg")[0] == 204  # nothing taken yet
    code, f = call(s, "POST", "/scope/frame", {"exposure_ms": 2})
    assert code == 200 and made[0].exposures == [0.002]
    conforms("ScopeFrame", f)
    code, jpg = call(s, "GET", "/scope/frame.jpg")
    assert code == 200 and jpg[:2] == b"\xff\xd8"


@pytest.mark.parametrize("ms", [0, 0.01, 20_000, "2", None])
def test_a_frame_refuses_a_bad_exposure(framed, ms):
    s, made = framed
    assert call(s, "POST", "/scope/frame", {"exposure_ms": ms})[0] == 400 and not made[0].exposures


def test_nothing_linked_means_no_point_and_no_frame(serve):  # noqa: F811
    s = serve()
    assert call(s, "POST", "/scope/point", {"az": 260, "alt": 30})[0] == 502
    assert call(s, "POST", "/scope/frame", {"exposure_ms": 2})[0] == 502
    assert call(s, "GET", "/scope/frame.jpg")[0] == 204


def test_the_preview_is_in_colour_red_where_grbg_puts_it():
    """GRBG: red at [0,1], blue at [1,0]. A frame bright only on the red sites is red."""
    import io

    from PIL import Image

    raw = np.zeros((8, 8), np.uint16)
    raw[0::2, 1::2] = 40000
    px = np.asarray(Image.open(io.BytesIO(scopes.preview_jpeg(raw))).convert("RGB")).mean((0, 1))
    assert px[0] > 200 and px[1] < 60 and px[2] < 60


def test_the_link_frame_keeps_the_raw_for_the_preview():
    link = scopes.AlpacaScope("h", connect=FakeMount)
    link.sc.capture_raw16 = lambda s: np.full((4, 4), 65504, np.uint16)
    f = link.frame(0.002)
    assert f == {"exposure_ms": 2.0, "median": 65504.0, "saturated": 1.0}
    assert link.preview_jpeg()[:2] == b"\xff\xd8"


def test_an_escape_never_climbs_toward_the_sun(monkeypatch):
    """The floor's climb has no promise of gaining separation, unlike the turn: from
    alt 2.7 under a Sun at alt 14 it would close in, so the escape refuses."""
    sky = _morning(monkeypatch)
    mount = _Mount(sky.altaz_to_radec(86.8, 2.7))
    with pytest.raises(SunGuard, match="nearer the Sun"):
        Pointer(mount, sky, 30.0, 5.0).escape()
    assert mount.gotos == []


def test_an_escape_gains_separation_at_every_step(monkeypatch):
    """With the Sun just above the horizon and below the floor, the climb moves
    away from it, and every step after does too."""
    from terminus.sweep import ang_sep

    sky = _morning(monkeypatch, "2026-10-07T13:00:00Z")  # Sun about 1 deg below the horizon
    saz, salt = sky.sun()
    assert -3.0 < salt < 1.0
    mount = _Mount(sky.altaz_to_radec(saz - 10.0, 1.0))
    start = ang_sep(saz - 10.0, 1.0, saz, salt)
    Pointer(mount, sky, 30.0, 5.0).escape()
    seps = [ang_sep(*sky.radec_to_altaz(*g), saz, salt) for g in mount.gotos]
    assert mount.gotos and min(seps) >= start
    assert seps == sorted(seps)
