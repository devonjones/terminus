import json
import socket
import threading

import pytest

from terminus.exporter import Cache, Poller, render

# Abridged from a live get_device_state, 2026-10-18 (firmware 9.75, on the EQ wedge).
STATE = {
    "pi_status": {"temp": 51.5, "battery_capacity": 100, "charge_online": True, "battery_temp": 24},
    "balance_sensor": {"code": 0, "data": {"angle": 48.714649}},
    "mount": {"move_type": "none", "close": True, "tracking": False, "equ_mode": True},
    "station": {"freq": 5200, "sig_lev": -73, "ssid": "x"},
    "storage": {"storage_volume": [{"name": "emmc", "total_mb": 51854, "free_mb": 38848}]},
    "device": {"sn": "d3c4c6db", "product_model": "Seestar S50", "firmware_ver_string": "9.75"},
    "focuser": {"step": 1570},
}


def _poller(state=STATE):
    c = Cache()
    p = Poller("scope", c)
    p.pending = 7
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
    assert _value(out, "seestar_mount_stowed") == 1
    assert _value(out, "seestar_mount_moving") == 0
    assert _value(out, 'seestar_storage_free_bytes{volume="emmc"}') == 38848 * 2**20
    assert _value(out, "seestar_last_success_timestamp_seconds") == 1000.0


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
    p.pending = 7
    p.handle(json.dumps({"id": 8, "result": {"str": "challenge"}}), now=1.0)
    assert c.state is None and p.pending == 7


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
