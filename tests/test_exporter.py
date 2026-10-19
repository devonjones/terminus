import io
import json
import socket
import threading
import time
import urllib.request

import pytest

from terminus import exporter
from terminus.exporter import AlpacaPoller, Cache, Poller, render

# Abridged from a live get_device_state, 2026-10-18 (firmware 9.75, on the EQ wedge).
STATE = {
    "pi_status": {"temp": 51.5, "battery_capacity": 100, "charge_online": True, "battery_temp": 24},
    "balance_sensor": {
        "code": 0,
        "data": {"x": 0.121486, "y": -0.727569, "z": 0.6477, "angle": 48.714649},
    },
    "location_lon_lat": [-104.894, 39.79],
    "mount": {"move_type": "none", "close": True, "tracking": False, "equ_mode": True},
    "station": {"freq": 5200, "sig_lev": -73, "ssid": "x"},
    "storage": {"storage_volume": [{"name": "emmc", "total_mb": 51854, "free_mb": 38848}]},
    "device": {"sn": "d3c4c6db", "product_model": "Seestar S50", "firmware_ver_string": "9.75"},
    "focuser": {"step": 1570},
}


def _poller(state=STATE):
    c = Cache()
    p = Poller("scope", c)
    p.pending = (7, 990.0)
    p.handle(json.dumps({"id": 7, "result": state}), now=1000.0)
    return c, p


def _value(text, series):
    for line in text.splitlines():
        if line.startswith(series + " "):
            return float(line.split()[-1])
    raise AssertionError(f"{series} not in output")


def test_device_state_becomes_metrics():
    c, _ = _poller()
    out = render(c, now=1010.0)
    assert _value(out, "seestar_up") == 1
    assert _value(out, "seestar_battery_percent") == 100
    assert _value(out, "seestar_tilt_degrees") == pytest.approx(48.714649)
    assert _value(out, "seestar_on_desk") == 0
    assert _value(out, 'seestar_accel_g{axis="z"}') == pytest.approx(0.6477)
    assert _value(out, "seestar_location_lat_degrees") == 39.79
    assert _value(out, "seestar_mount_stowed") == 1
    assert _value(out, "seestar_mount_moving") == 0
    assert _value(out, 'seestar_storage_free_bytes{volume="emmc"}') == 38848 * 2**20
    assert _value(out, "seestar_state_last_success_timestamp_seconds") == 1000.0


def test_flat_on_a_desk_reads_on_desk():
    c, _ = _poller({"balance_sensor": {"data": {"angle": 1.2}}})
    assert _value(render(c, now=1000.0), "seestar_on_desk") == 1


def test_a_silent_scope_is_down_but_keeps_its_last_values():
    c, _ = _poller()
    out = render(c, now=1000.0 + 91)
    assert _value(out, "seestar_up") == 0
    assert _value(out, "seestar_battery_percent") == 100


def test_events_export_their_numbers_and_pistatus_updates_the_state():
    c, p = _poller()
    p.handle(
        json.dumps(
            {
                "Event": "Stack",
                "state": "frame_complete",
                "stacked_frame": 12,
                "dropped_frame": 1,
                "Timestamp": "31094.9",
            }
        ),
        now=1001.0,
    )
    p.handle(json.dumps({"Event": "PiStatus", "temp": 60.5, "Timestamp": "1"}), now=1002.0)
    out = render(c, now=1003.0)
    assert _value(out, 'seestar_event_value{event="Stack",field="stacked_frame"}') == 12
    assert _value(out, 'seestar_event_state{event="Stack",state="frame_complete"}') == 1
    assert "Timestamp" not in out
    assert _value(out, "seestar_board_temp_celsius") == 60.5
    assert _value(out, "seestar_battery_percent") == 100


def test_a_reply_to_another_request_is_not_taken_for_the_state():
    c = Cache()
    p = Poller("scope", c)
    p.pending = (7, 0.0)
    p.handle(json.dumps({"id": 8, "result": {"str": "challenge"}}), now=1.0)
    assert c.state is None and p.pending == (7, 0.0)


def test_pistatus_reports_before_any_device_state():
    # Started mid-stack, the device state never answers; PiStatus must still report.
    c = Cache()
    Poller("scope", c).handle(json.dumps({"Event": "PiStatus", "temp": 55.0}), now=1.0)
    assert _value(render(c, now=2.0), "seestar_board_temp_celsius") == 55.0


def _fake_scope(replies):
    """A control port that answers keepalives and never the device state."""
    srv = socket.create_server(("127.0.0.1", 0))

    def run():
        conn, _ = srv.accept()
        conn.settimeout(5)
        buf = b""
        try:
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                buf += chunk
                *lines, buf = buf.split(b"\r\n")
                for line in lines:
                    d = json.loads(line)
                    replies.append(d["method"])
                    if d["method"] == "get_verify_str":
                        conn.sendall(json.dumps({"id": d["id"], "result": {}}).encode() + b"\r\n")
        except OSError:
            pass

    threading.Thread(target=run, daemon=True).start()
    return srv


def test_an_unanswered_device_state_times_out_counts_and_reconnects(monkeypatch):
    monkeypatch.setattr(exporter, "STATE_TIMEOUT_S", 0.5)
    monkeypatch.setattr(exporter, "KEEPALIVE_S", 0.1)
    sent = []
    srv = _fake_scope(sent)
    c = Cache()
    with pytest.raises(TimeoutError):
        Poller("127.0.0.1", c, port=srv.getsockname()[1]).session()
    assert c.failures == 1
    assert sent.count("get_device_state") == 1  # no second request queued behind it
    srv.close()


def test_alpaca_reads_only_and_skips_what_needs_connected(monkeypatch):
    calls = []

    def fake_urlopen(url, timeout):
        calls.append(url)
        assert not isinstance(url, urllib.request.Request)  # a bare URL is a GET
        value = {"rightascension": 19.9, "position": 2, "names": ["Dark", "IR", "LP"]}
        prop = url.split("/")[-1].split("?")[0]
        body = {"Value": value.get(prop, 1.0), "ErrorNumber": 1031 if prop == "slewing" else 0}
        return io.BytesIO(json.dumps(body).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    c = Cache()
    AlpacaPoller("scope", c).poll_once()
    out = render(c, now=c.alpaca_last_poll)
    assert _value(out, "seestar_ra_hours") == 19.9
    assert "seestar_telescope_slewing" not in out
    assert _value(out, 'seestar_filter_info{filter="LP"}') == 1
    assert all("/connected" not in u for u in calls)


def test_alpaca_down_keeps_the_heartbeat_but_not_the_success(monkeypatch):
    def refuse(url, timeout):
        raise ConnectionRefusedError

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    c = Cache()
    AlpacaPoller("scope", c).poll_once()
    assert c.alpaca_last_poll > 0 and c.alpaca_last_success == 0
    assert c.alpaca_failures == len(exporter.ALPACA_READS)


def test_the_exporter_will_not_send_anything_off_its_allowlist():
    a, b = socket.socketpair()
    p = Poller("scope", Cache())
    p.s = a
    with pytest.raises(ValueError):
        p.send("pi_shutdown")
    p.send("get_device_state")
    b.settimeout(1)
    assert json.loads(b.recv(4096))["method"] == "get_device_state"
    a.close()
    b.close()


def test_a_scope_that_hangs_up_is_reconnected():
    srv = socket.create_server(("127.0.0.1", 0))
    port = srv.getsockname()[1]
    c = Cache()

    def scope():
        for _ in range(2):
            conn, _ = srv.accept()
            conn.recv(4096)
            conn.sendall(b'{"Event": "PiStatus", "temp": 50}\r\n')
            conn.close()

    threading.Thread(target=scope, daemon=True).start()
    p = Poller("127.0.0.1", c, port=port)
    for _ in range(2):
        with pytest.raises(ConnectionError):
            p.session()
    assert c.connects == 2 and "PiStatus" in c.events
    srv.close()


def _one_shot_scope(payload):
    srv = socket.create_server(("127.0.0.1", 0))

    def run():
        conn, _ = srv.accept()
        conn.sendall(payload)
        time.sleep(0.3)
        conn.close()

    threading.Thread(target=run, daemon=True).start()
    return srv


def test_the_connection_limit_is_counted_and_not_retried_at_once():
    alert = {"Event": "Alert", "Msg": "Exceed the maximum number of connections", "code": 429}
    srv = _one_shot_scope(json.dumps(alert).encode() + b"\r\n")
    c = Cache()
    with pytest.raises(ConnectionRefusedError):
        Poller("127.0.0.1", c, port=srv.getsockname()[1]).session()
    assert c.refused == 1
    assert _value(render(c, now=time.time()), "seestar_refused_total") == 1
    srv.close()


def test_a_short_session_lets_go_of_the_connection_after_hold_s(monkeypatch):
    monkeypatch.setattr(exporter, "KEEPALIVE_S", 0.1)
    sent = []
    srv = _fake_scope(sent)
    t = time.time()
    Poller("127.0.0.1", Cache(), port=srv.getsockname()[1], hold_s=0.5).session()
    assert time.time() - t < 3
    srv.close()


def test_every_number_is_exported_nested_ones_included():
    c, p = _poller({"pi_status": {"temp": 50}, "new_block": {"a": [1, {"b": 2.5}], "s": "x"}})
    p.handle(json.dumps({"Event": "PlateSolve", "result": {"ra_dec": [19.9, 18.8]}}), now=1001.0)
    out = render(c, now=1002.0)
    assert _value(out, 'seestar_state_value{block="new_block",field="a.1.b"}') == 2.5
    assert _value(out, 'seestar_event_value{event="PlateSolve",field="result.ra_dec.1"}') == 18.8
