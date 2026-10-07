"""The Alpaca link against a fake Seestar Alpaca server: no hardware."""

import json
import struct
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import pytest

from terminus import alpaca
from terminus.alpaca import Alpaca, icrs_to_jnow, jnow_to_icrs, parse_imagebytes
from terminus.client import SeestarError
from terminus.config import ConfigError, load_config


def imagebytes(frame):
    """Alpaca ImageBytes for a uint16 frame (sent x-major)."""
    h, w = frame.shape
    header = struct.pack("<11i", 1, 0, 0, 0, 44, 2, 8, 2, w, h, 0)
    return header + np.ascontiguousarray(frame.T).astype("<u2").tobytes()


class FakeSeestar:
    """Answers like the S50 did on 2026-10-06, quirks included."""

    def __init__(self):
        self.state = {"rightascension": 3.0, "declination": 4.0, "tracking": False}
        self.puts = []
        self.drop = 0  # requests to swallow without answering
        self.frame = np.arange(12, dtype=np.uint16).reshape(3, 4) * 16

    def handle(self, req, method):
        if self.drop:
            self.drop -= 1
            return None
        url = urllib.parse.urlparse(req.path)
        dev, prop = url.path.split("/")[3], url.path.split("/")[5]
        if method == "PUT":
            n = int(req.headers["Content-Length"])
            form = dict(urllib.parse.parse_qsl(req.rfile.read(n).decode()))
            self.puts.append((dev, prop, form))
            if prop == "tracking":
                self.state["tracking"] = True
                return {"ErrorNumber": 1279, "ErrorMessage": "mount sync failed"}
            return {"ErrorNumber": 0}
        if prop == "imagearray":
            return imagebytes(self.frame)
        if prop == "imageready":
            return {"Value": True, "ErrorNumber": 0}
        if prop == "slewing":
            return {"Value": False, "ErrorNumber": 0}
        return {"Value": self.state.get(prop, 0), "ErrorNumber": 0}


@pytest.fixture
def scope(monkeypatch):
    fake = FakeSeestar()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def answer(self, method):
            out = fake.handle(self, method)
            if out is None:
                return  # dropped: the client times out
            raw = isinstance(out, bytes)
            body = out if raw else json.dumps(out).encode()
            self.send_response(200)
            kind = "application/imagebytes" if raw else "application/json"
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self.answer("GET")

        def do_PUT(self):
            self.answer("PUT")

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(alpaca, "TIMEOUT_S", 0.5)
    monkeypatch.setattr(alpaca.time, "sleep", lambda s: None)
    yield fake, Alpaca("127.0.0.1", srv.server_address[1])
    srv.shutdown()


def test_jnow_round_trips_and_differs_from_icrs_by_precession():
    ra, dec = icrs_to_jnow(3.0, 4.0)
    back = jnow_to_icrs(ra, dec)
    assert abs(back[0] - 3.0) < 1e-6 and abs(back[1] - 4.0) < 1e-6
    shift = np.hypot((ra - 3.0) * 15.0, dec - 4.0)
    assert 0.3 < shift < 0.45, f"26 years of precession is about 0.36 deg, got {shift}"


def test_pointing_is_read_and_commanded_in_icrs(scope):
    fake, sc = scope
    assert np.allclose(sc.equ_coord(), jnow_to_icrs(3.0, 4.0), atol=1e-4)
    sc.goto(5.0, 20.0)
    slew = [f for d, p, f in fake.puts if p == "slewtocoordinatesasync"][0]
    want = icrs_to_jnow(5.0, 20.0)
    assert abs(float(slew["RightAscension"]) - want[0]) < 1e-4
    assert abs(float(slew["Declination"]) - want[1]) < 1e-4


def test_goto_turns_tracking_on_through_its_false_error(scope):
    fake, sc = scope
    sc.goto(5.0, 20.0)
    props = [p for _, p, _ in fake.puts]
    assert props.index("tracking") < props.index("slewtocoordinatesasync")


def test_goto_refuses_when_tracking_stays_off(scope):
    fake, sc = scope
    fake.state["tracking"] = False
    orig = fake.handle

    def stuck(req, method):
        out = orig(req, method)
        fake.state["tracking"] = False
        return out

    fake.handle = stuck
    with pytest.raises(SeestarError, match="tracking"):
        sc.goto(5.0, 20.0)
    assert "slewtocoordinatesasync" not in [p for _, p, _ in fake.puts]


def test_a_dropped_request_is_retried(scope):
    fake, sc = scope
    fake.drop = alpaca.RETRIES - 1
    assert sc.is_eq_mode() is False  # alignmentmode 0 in the fake: answered, not raised


def test_a_scope_that_never_answers_says_so(scope):
    fake, sc = scope
    fake.drop = alpaca.RETRIES
    with pytest.raises(SeestarError, match="after 3 tries"):
        sc.is_eq_mode()


def test_an_unreadable_pointing_is_none_like_the_native_link(scope):
    """Pointer and the sweep skip a column on None; an exception lost the run."""
    fake, sc = scope
    fake.drop = alpaca.RETRIES
    assert sc.equ_coord() is None


def test_a_frame_comes_back_row_major(scope):
    fake, sc = scope
    assert np.array_equal(sc.capture_raw16(0.1), fake.frame)
    assert ("camera", "connected", {"Connected": "true"}) in [
        (d, p, {k: v for k, v in f.items() if k == "Connected"}) for d, p, f in fake.puts
    ]


def test_imagebytes_errors_are_raised():
    header = struct.pack("<11i", 1, 1031, 0, 0, 44, 0, 0, 0, 0, 0, 0)
    with pytest.raises(SeestarError, match="1031"):
        parse_imagebytes(header + b"not connected")


def test_daytime_measurement_says_it_is_not_built(scope):
    _, sc = scope
    with pytest.raises(SeestarError, match="daytime"):
        sc.lock_exposure()


def test_config_without_a_key_links_over_alpaca(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('[scope]\nhost = "h"\n')
    assert load_config(str(p))["link"] == "alpaca"
    p.write_text('[scope]\nhost = "h"\npem = "k.pem"\n')
    assert load_config(str(p))["link"] == "native"
    p.write_text('[scope]\nhost = "h"\nlink = "native"\n')
    with pytest.raises(ConfigError, match="pem"):
        load_config(str(p))


def test_park_is_a_put_to_the_telescope(scope):
    fake, sc = scope
    sc.park()
    assert ("telescope", "park") in [(d, p) for d, p, _ in fake.puts]


class _Arm:
    """A mount whose arm opens on unpark: Dec -90 until then."""

    def __init__(self):
        self.opened = False

    def unpark(self):
        self.opened = True

    def equ_coord(self):
        return (6.0, 10.0) if self.opened else (6.0, -90.0)


class _Sky:
    def __init__(self, sun_alt):
        self.sun_alt = sun_alt

    def sun(self):
        return (90.0, self.sun_alt)

    def radec_to_altaz(self, ra, dec):
        return (88.0, 4.0)


@pytest.mark.parametrize("sun_alt", [-2.9, 10.0])
def test_unpark_is_refused_while_the_sun_is_up(monkeypatch, sun_alt):
    """The firmware picks the arm's path, so no Pointer guards it: Sun down only."""
    from terminus import cli

    arm = _Arm()
    monkeypatch.setattr(cli, "_sky", lambda sc, cfg: _Sky(sun_alt))
    with pytest.raises(SeestarError, match="Sun is up"):
        cli.cmd_unpark(arm, {}, None)
    assert not arm.opened


def test_unpark_opens_the_arm_after_dark(monkeypatch, capsys):
    from terminus import cli

    arm = _Arm()
    monkeypatch.setattr(cli, "_sky", lambda sc, cfg: _Sky(-20.0))
    cli.cmd_unpark(arm, {}, None)
    assert arm.opened and "arm open" in capsys.readouterr().out


def test_unpark_over_alpaca_says_to_use_the_app(monkeypatch):
    from terminus import cli

    monkeypatch.setattr(cli, "_sky", lambda sc, cfg: _Sky(-20.0))
    with pytest.raises(SeestarError, match="Seestar app"):
        cli.cmd_unpark(object(), {}, None)


def test_a_refusal_is_not_retried_and_keeps_its_reason(scope):
    fake, sc = scope
    seen = []
    orig = fake.handle

    def refuse(req, method):
        seen.append(method)
        if method == "PUT":
            req.send_response(400)
            body = b"Declination out of range"
            req.send_header("Content-Length", str(len(body)))
            req.end_headers()
            req.wfile.write(body)
            return None
        return orig(req, method)

    fake.handle = refuse
    with pytest.raises(SeestarError, match="400 Declination out of range"):
        sc._put("telescope", "abortslew")
    assert seen.count("PUT") == 1


def test_an_unreadable_reply_is_a_scope_error(scope):
    fake, sc = scope

    def garbled(req, method):
        req.send_response(200)
        req.send_header("Content-Length", "3")
        req.end_headers()
        req.wfile.write(b"<?>")
        return None

    fake.handle = garbled
    with pytest.raises(SeestarError, match="unreadable"):
        sc.is_eq_mode()


def test_classify_over_alpaca_says_daytime_is_not_built(scope):
    _, sc = scope
    with pytest.raises(SeestarError, match="daytime"):
        sc.capture_rgb()


def test_a_frame_is_not_taken_before_its_exposure_ends(scope, monkeypatch):
    """A "ready" left over from the last frame would be the last pointing's sky."""
    fake, sc = scope
    waits = []
    monkeypatch.setattr(alpaca.time, "sleep", waits.append)
    sc.capture_raw16(2.0)
    assert waits and waits[0] == 2.0


def test_a_json_image_array_comes_back_row_major(scope):
    """Some servers ignore Accept and send ImageArray as JSON, x-major."""
    fake, sc = scope
    orig = fake.handle

    def json_image(req, method):
        if "imagearray" in req.path:
            return {"Value": fake.frame.T.tolist(), "ErrorNumber": 0}
        return orig(req, method)

    fake.handle = json_image
    assert np.array_equal(sc.capture_raw16(0.1), fake.frame)


def test_an_unknown_link_is_refused(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('[scope]\nhost = "h"\nlink = "bluetooth"\n')
    with pytest.raises(ConfigError, match="link must be"):
        load_config(str(p))


def test_the_frame_conversion_reproduces_the_scope_on_2026_10_06():
    """Measured: the scope read JNow (3.314722 h, 4.150833 deg) and its own az/alt
    as 86.808/2.655 at once. Read as JNow it reproduces them; swapped it is 0.3 deg off."""
    from astropy.time import Time

    from terminus.sweep import Sky

    when = Time("2026-10-07T03:15:18.29Z")
    sky = Sky(39.791458, -104.894089)
    az, alt = sky.radec_to_altaz(*jnow_to_icrs(3.314722, 4.150833, when), when)
    assert abs(az - 86.808) < 0.05 and abs(alt - 2.655) < 0.05, (az, alt)


def test_a_scope_error_on_a_read_is_raised(scope):
    fake, sc = scope
    orig = fake.handle
    fake.handle = lambda req, m: (
        {"ErrorNumber": 1031, "ErrorMessage": "not connected"}
        if "alignmentmode" in req.path
        else orig(req, m)
    )
    with pytest.raises(SeestarError, match="1031"):
        sc.is_eq_mode()


def test_a_server_error_is_retried(scope):
    """A 503 is the server's trouble; one of them must not end a night's sweep."""
    fake, sc = scope
    orig, failed = fake.handle, []

    def busy_once(req, method):
        if not failed:
            failed.append(1)
            req.send_response(503)
            req.send_header("Content-Length", "0")
            req.end_headers()
            return None
        return orig(req, method)

    fake.handle = busy_once
    assert sc.is_eq_mode() is False and failed


def test_an_image_that_never_arrives_says_so(scope, monkeypatch):
    fake, sc = scope
    orig = fake.handle
    fake.handle = lambda req, m: (
        {"Value": False, "ErrorNumber": 0} if "imageready" in req.path else orig(req, m)
    )
    clock = iter(range(0, 10_000, 30))
    monkeypatch.setattr(alpaca.time, "time", lambda: next(clock))
    with pytest.raises(SeestarError, match="no image"):
        sc.capture_raw16(2.0)
