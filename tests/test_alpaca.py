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
        sc.equ_coord()


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
