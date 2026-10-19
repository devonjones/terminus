"""`terminus exporter`: the Seestar's own state as Prometheus metrics.

Strictly passive. On the control port (4700) it sends three requests:
`get_verify_str`, `verify_client` to sign that challenge with the interop key
(SEESTAR_PEM), and `get_device_state`, which firmware 9.75 ignores until the
client has verified. The scope closes a socket that is quiet for ~16 s, so a
keepalive goes every KEEPALIVE_S: `get_verify_str` unkeyed, a pi_status state
read once verified (a new challenge undoes the verification).
Everything else comes from the events the scope pushes on the same socket
(Imaging, Exposure, PiStatus, Stack, ...). It never calls any other method: some,
like pi_shutdown, are destructive. On Alpaca (32323) it sends GETs, plus Connected=true to the focuser
and the switch (ALPACA_CONNECT, default "focuser,switch") for focuser position
and the dew heater. It never connects the camera, so there is no CCD temperature.

While the scope images, get_device_state goes unanswered all night. After
STATE_TIMEOUT_S the request is counted as failed and the socket reopened rather
than another request queued; PiStatus events keep the board temperature current,
and Alpaca, polled in its own thread, keeps pointing current.

One thread holds the socket and polls into a cache; /metrics renders the cache,
so a scrape never reaches the scope. A dropped socket or a sleeping scope only
ages the cache: `seestar_up` goes to 0 and the last values keep serving with
their timestamps.

Configuration is by environment, for the container: SEESTAR_HOST,
SEESTAR_PEM (the interop key; without it only events and Alpaca report),
EXPORTER_PORT (9877), POLL_S (30), ON_DESK_DEG (10), NATIVE_HOLD_S (10; 0 holds
the connection open), NATIVE_EVERY_S (60; 0 for Alpaca only).
"""

import http.client
import json
import logging
import math
import os
import socket
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .client import load_key, verified, verify_params

log = logging.getLogger(__name__)

CONTROL_PORT = 4700
KEEPALIVE_S = 10
DEAD_S = 30  # nothing at all heard for this long, keepalives included: reconnect
# An unanswered device state after this long counts as a failure and the socket is
# reopened, which drops the request instead of queueing another behind it.
STATE_TIMEOUT_S = 120
ALPACA_PORT = 32323
ALPACA_TIMEOUT_S = 10
# Alpaca GETs that answer with every device disconnected (2026-10-18, while imaging).
# Focuser position and the dew-heater switch need Connected=true (see CONNECTABLE);
# CCD temperature would need the camera connected, which the exporter never does.
# The scope allows only a few control connections (it answers the next with an
# Alert, code 429, and hangs up), and the phone app needs one. So the exporter holds
# its connection for HOLD_S out of every EVERY_S rather than all night.
NATIVE_HOLD_S = 10
NATIVE_EVERY_S = 60
# Further GETs that may or may not be implemented; whatever answers is exported as
# seestar_alpaca_value{device,property}.
ALPACA_EXTRA = [
    ("telescope", p)
    for p in (
        "siderealtime",
        "targetrightascension",
        "targetdeclination",
        "sideofpier",
        "trackingrate",
        "rightascensionrate",
        "declinationrate",
        "guideraterightascension",
        "guideratedeclination",
        "ispulseguiding",
        "athome",
        "atpark",
        "alignmentmode",
        "equatorialsystem",
        "focallength",
        "aperturediameter",
    )
] + [("focuser", p) for p in ("ismoving", "maxstep", "stepsize", "tempcomp")]
# The one write the exporter makes: Connected=true, on these devices only. Neither
# moves anything or touches the imaging path; Devon chose to connect them (2026-10-18)
# for focuser position and the dew heater. The camera is deliberately not allowed:
# connecting it mid-stack could take the sensor from the app.
CONNECTABLE = frozenset({"focuser", "switch"})
ALPACA_READS = {
    ("telescope", "rightascension"): "seestar_ra_hours",
    ("telescope", "declination"): "seestar_dec_degrees",
    ("telescope", "altitude"): "seestar_altitude_degrees",
    ("telescope", "azimuth"): "seestar_azimuth_degrees",
    ("telescope", "tracking"): "seestar_telescope_tracking",
    ("telescope", "slewing"): "seestar_telescope_slewing",
    ("focuser", "temperature"): "seestar_focuser_temp_celsius",
    ("filterwheel", "position"): "seestar_filter_position",
    ("telescope", "sitelatitude"): "seestar_site_latitude_degrees",
    ("telescope", "sitelongitude"): "seestar_site_longitude_degrees",
}
ALLOWED_METHODS = frozenset({"get_device_state", "get_verify_str", "verify_client"})


class Cache:
    """What the poller last saw. Written by one thread, read by scrapes."""

    def __init__(self):
        self.lock = threading.Lock()
        self.state = None  # the last get_device_state result, PiStatus events merged in
        self.events = {}  # event name -> (last payload, time seen)
        self.last_poll = 0.0
        self.last_success = 0.0  # last device state
        self.alpaca = {}  # metric name -> value
        self.alpaca_extra = {}  # (device, property) -> value
        self.switches = {}  # switch name -> value
        self.alpaca_connects = 0
        self.filter_names = None
        self.alpaca_last_poll = 0.0
        self.alpaca_last_success = 0.0
        self.alpaca_failures = 0
        self.last_heard = 0.0  # last line of any kind
        self.connected = False
        self.failures = 0
        self.connects = 0
        self.refused = 0  # connections the scope turned away as over its limit


class Poller:
    def __init__(
        self,
        host,
        cache,
        poll_s=30.0,
        port=CONTROL_PORT,
        hold_s=NATIVE_HOLD_S,
        every_s=NATIVE_EVERY_S,
        key=None,
    ):
        self.host, self.port, self.cache, self.poll_s = host, port, cache, poll_s
        self.hold_s, self.every_s = hold_s, every_s  # hold_s 0: keep the connection open
        self.refused = False
        self.s = None
        self.buf = b""
        self.cid = 1000
        self.pending = None  # (id, sent at) of the outstanding get_device_state
        self.key = key  # interop key: verify each session before asking for the state
        self.challenge_id = self.verify_id = self.challenge = None
        self.authed = self.auth_failed = False

    def send(self, method, params=None):
        if method not in ALLOWED_METHODS:  # the passive guarantee, enforced here
            raise ValueError(f"exporter refuses to call {method}")
        self.cid += 1
        msg = {"id": self.cid, "method": method}
        if params is not None:
            msg["params"] = params
        self.s.sendall((json.dumps(msg) + "\r\n").encode())
        return self.cid

    def handle(self, line, now):
        try:
            d = json.loads(line)
        except ValueError:
            return
        if not isinstance(d, dict):
            return
        c = self.cache
        with c.lock:
            c.last_heard = now
            if d.get("Event") == "Alert" and d.get("code") == 429:
                self.refused = True  # "Exceed the maximum number of connections"
            if "Event" in d:
                c.events[str(d["Event"])] = (d, now)
                if d["Event"] == "PiStatus":
                    if c.state is None:
                        c.state = {}
                    pi = c.state.setdefault("pi_status", {})
                    pi.update({k: v for k, v in d.items() if k not in ("Event", "Timestamp")})
            elif self.challenge_id is not None and d.get("id") == self.challenge_id:
                r = d.get("result")
                self.challenge = r.get("str") if isinstance(r, dict) else None
                self.challenge_id = None
            elif self.verify_id is not None and d.get("id") == self.verify_id:
                self.verify_id = None
                self.authed = verified(d)
                self.auth_failed = not self.authed
            elif self.pending is not None and d.get("id") == self.pending[0]:
                self.pending = None
                if isinstance(d.get("result"), dict):
                    c.state, c.last_success = d["result"], now
                else:
                    c.failures += 1

    def session(self):
        """One connection, for hold_s or until it drops."""
        self.s = socket.create_connection((self.host, self.port), timeout=10)
        self.s.settimeout(1.0)
        self.buf, self.pending, self.refused = b"", None, False
        self.challenge = self.verify_id = None
        self.authed, self.auth_failed = self.key is None, False
        self.challenge_id = self.send("get_verify_str") if self.key is not None else None
        opened = time.time()
        with self.cache.lock:
            self.cache.connected = True
            self.cache.connects += 1
        next_poll, next_keepalive = 0.0, opened + KEEPALIVE_S
        while True:
            now = time.time()
            if self.refused:
                with self.cache.lock:
                    self.cache.refused += 1
                raise ConnectionRefusedError("the scope is at its connection limit")
            if self.auth_failed:
                with self.cache.lock:
                    self.cache.failures += 1
                raise PermissionError("verify_client refused the signature (wrong key?)")
            if self.challenge:
                self.verify_id = self.send("verify_client", verify_params(self.key, self.challenge))
                self.challenge = None
            if self.hold_s and now - opened > self.hold_s:
                return
            if now - max(opened, self.cache.last_heard) > DEAD_S:
                raise TimeoutError(f"nothing heard for {DEAD_S}s")
            if self.pending is not None and now - self.pending[1] > STATE_TIMEOUT_S:
                with self.cache.lock:
                    self.cache.failures += 1
                raise TimeoutError(f"no device state in {STATE_TIMEOUT_S}s (the scope is imaging?)")
            if self.authed and now >= next_poll and self.pending is None:
                self.pending = (self.send("get_device_state"), now)
                with self.cache.lock:
                    self.cache.last_poll = now
                if self.key is None:
                    self.send("get_verify_str")  # answered even when the state is not
                next_poll = now + self.poll_s
                next_keepalive = now + KEEPALIVE_S
            elif now >= next_keepalive:
                # a fresh challenge undoes verification, so a verified session keeps
                # alive with a small state read instead
                if self.key is None:
                    self.send("get_verify_str")
                else:
                    self.send("get_device_state", {"keys": ["pi_status"]})
                next_keepalive = now + KEEPALIVE_S
            try:
                chunk = self.s.recv(65536)
            except TimeoutError:
                continue
            if not chunk:
                raise ConnectionError("scope closed the connection")
            self.buf += chunk
            *lines, self.buf = self.buf.split(b"\r\n")
            for line in lines:
                if line.strip():
                    self.handle(line.decode("utf-8", "replace"), time.time())

    def run(self):
        """Connect every every_s for hold_s (or hold on), backing off to a minute on errors."""
        delay = 1
        while True:
            started = time.time()
            try:
                self.session()
                pause, delay = max(1, self.every_s - (time.time() - started)), 1
            except ConnectionRefusedError as e:
                log.info("scope %s:%s: %s; next try in %ss", self.host, self.port, e, self.every_s)
                pause = self.every_s
            except Exception as e:  # never die: a scope that is off is the normal case
                if self.cache.last_heard > started + 60:
                    delay = 1  # the session lasted: a fresh drop retries at once
                log.info("scope %s:%s: %s; retrying in %ss", self.host, self.port, e, delay)
                pause, delay = delay, min(60, delay * 2)
            finally:  # let go of the slot before the pause, never during it
                with self.cache.lock:
                    self.cache.connected = False
                if self.s:
                    self.s.close()
            time.sleep(pause)


class AlpacaPoller:
    """GETs, each on its own short timeout, in a thread of its own so that a stalled
    native call never holds these up. The only write is Connected=true on a device in
    `connect` (a subset of CONNECTABLE), sent when it reads as disconnected."""

    def __init__(self, host, cache, poll_s=30.0, port=ALPACA_PORT, connect=CONNECTABLE):
        if not set(connect) <= CONNECTABLE:
            raise ValueError(f"exporter will only connect {sorted(CONNECTABLE)}")
        self.base = f"http://{host}:{port}/api/v1"
        self.cache, self.poll_s, self.connect = cache, poll_s, frozenset(connect)
        self.switch_names = {}

    def get(self, dev, prop, **params):
        q = urllib.parse.urlencode({**params, "ClientID": 42, "ClientTransactionID": 1})
        with urllib.request.urlopen(
            f"{self.base}/{dev}/0/{prop}?{q}", timeout=ALPACA_TIMEOUT_S
        ) as r:
            d = json.loads(r.read())
        return None if d.get("ErrorNumber") else d.get("Value")  # e.g. 1031, not connected

    def connect_device(self, dev):
        if dev not in CONNECTABLE:  # the write guarantee, enforced here
            raise ValueError(f"exporter refuses to connect {dev}")
        body = urllib.parse.urlencode(
            {"Connected": "true", "ClientID": 42, "ClientTransactionID": 1}
        )
        req = urllib.request.Request(f"{self.base}/{dev}/0/connected", body.encode(), method="PUT")
        with urllib.request.urlopen(req, timeout=ALPACA_TIMEOUT_S) as r:
            r.read()
        with self.cache.lock:
            self.cache.alpaca_connects += 1

    def connected_reads(self):
        """Readings that need the device connected: {metric name: value}, and the
        switches as {name: value}."""
        values, switches = {}, {}
        for dev in sorted(self.connect):
            if self.get(dev, "connected") is False:
                self.connect_device(dev)
        if "focuser" in self.connect:
            values["seestar_focuser_position"] = self.get("focuser", "position")
        if "switch" in self.connect:
            for i in range(int(self.get("switch", "maxswitch") or 0)):
                if i not in self.switch_names:
                    self.switch_names[i] = self.get("switch", "getswitchname", Id=i) or str(i)
                switches[self.switch_names[i]] = self.get("switch", "getswitchvalue", Id=i)
        return values, switches

    def poll_once(self):
        values, ok, extra = {}, False, {}
        for dev, prop in ALPACA_EXTRA:
            try:
                extra[(dev, prop)] = self.get(dev, prop)
            except (OSError, ValueError, http.client.HTTPException):
                pass
        for (dev, prop), name in ALPACA_READS.items():
            try:
                values[name] = self.get(dev, prop)
                ok = True
            except (OSError, ValueError, http.client.HTTPException):
                with self.cache.lock:
                    self.cache.alpaca_failures += 1
        names, switches = None, {}
        if ok:
            try:
                names = self.get("filterwheel", "names")
                more, switches = self.connected_reads()
                values.update(more)
            except (OSError, ValueError, http.client.HTTPException):
                with self.cache.lock:
                    self.cache.alpaca_failures += 1
        now = time.time()
        with self.cache.lock:
            self.cache.alpaca_last_poll = now
            if ok:
                self.cache.alpaca = {k: v for k, v in values.items() if v is not None}
                self.cache.alpaca_extra = {k: v for k, v in extra.items() if v is not None}
                self.cache.alpaca_last_success = now
                self.cache.switches = {k: v for k, v in switches.items() if v is not None}
                if isinstance(names, list):
                    self.cache.filter_names = names

    def run(self):
        while True:
            try:
                self.poll_once()
            except Exception as e:  # never die
                log.info("alpaca %s: %s", self.base, e)
            time.sleep(self.poll_s)


def _num(v):
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, (int, float)) and math.isfinite(v):
        return float(v)
    return None


def _flat(obj, prefix=""):
    """Every number in a nested dict/list, as (dotted path, value)."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _flat(v, f"{prefix}.{k}" if prefix else str(k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _flat(v, f"{prefix}.{i}" if prefix else str(i))
    elif _num(obj) is not None:
        yield prefix, obj


def _label(v):
    return str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def render(cache, now=None, poll_s=30.0, on_desk_deg=10.0, native_every_s=NATIVE_EVERY_S):
    """The cache as Prometheus text exposition."""
    now = time.time() if now is None else now
    out = []

    def m(name, value, help_, labels=None, kind="gauge"):
        v = _num(value)
        if v is None:
            return
        if not any(line.startswith(f"# HELP {name} ") for line in out):
            out.append(f"# HELP {name} {help_}")
            out.append(f"# TYPE {name} {kind}")
        lab = ",".join(f'{k}="{_label(x)}"' for k, x in (labels or {}).items())
        out.append(f"{name}{{{lab}}} {v!r}" if lab else f"{name} {v!r}")

    with cache.lock:
        st = cache.state or {}
        events = dict(cache.events)
        m(
            "seestar_up",
            cache.alpaca_last_success > now - 2 * poll_s
            or cache.last_heard > now - 2 * native_every_s,
            "1 when Alpaca answered in two polls or the control port in two native windows",
        )
        m("seestar_connected", cache.connected, "1 while the control socket is open")
        m(
            "seestar_request_failures_total",
            cache.failures,
            "polls or connections that failed",
            kind="counter",
        )
        m("seestar_connects_total", cache.connects, "control connections opened", kind="counter")
        m("seestar_last_poll_timestamp_seconds", cache.last_poll or None, "last poll sent")
        m("seestar_last_heard_timestamp_seconds", cache.last_heard or None, "last line received")
        m(
            "seestar_state_last_success_timestamp_seconds",
            cache.last_success or None,
            "last device state received (none while the scope images)",
        )
        m(
            "seestar_alpaca_last_poll_timestamp_seconds",
            cache.alpaca_last_poll or None,
            "last Alpaca poll attempted: the exporter's own heartbeat",
        )
        m(
            "seestar_alpaca_last_success_timestamp_seconds",
            cache.alpaca_last_success or None,
            "last Alpaca poll with an answer",
        )
        m(
            "seestar_alpaca_failures_total",
            cache.alpaca_failures,
            "Alpaca GETs failed",
            kind="counter",
        )
        m(
            "seestar_refused_total",
            cache.refused,
            "connections refused as over the scope's limit",
            kind="counter",
        )
        alpaca = dict(cache.alpaca)
        alpaca_extra = dict(cache.alpaca_extra)
        switches = dict(cache.switches)
        m(
            "seestar_alpaca_connects_total",
            cache.alpaca_connects,
            "Connected=true sent to the focuser or switch",
            kind="counter",
        )
        filter_names = cache.filter_names

    for name, v in sorted(alpaca.items()):
        m(name, v, "from Alpaca")
    for name, v in sorted(switches.items()):
        m("seestar_switch_value", v, "Alpaca switch (the dew heater)", {"switch": name})
    for (dev, prop), v in sorted(alpaca_extra.items()):
        m(
            "seestar_alpaca_value",
            v,
            "any other Alpaca property that answers",
            {"device": dev, "property": prop},
        )
    pos = _num(alpaca.get("seestar_filter_position"))
    if filter_names and pos is not None and 0 <= pos < len(filter_names):
        m(
            "seestar_filter_info",
            1,
            "the filter in the light path",
            {"filter": filter_names[int(pos)]},
        )

    dev = st.get("device") or {}
    if dev:
        m(
            "seestar_info",
            1,
            "scope identity",
            {
                "sn": dev.get("sn", ""),
                "model": dev.get("product_model", ""),
                "firmware": dev.get("firmware_ver_string", ""),
                "name": dev.get("name", ""),
            },
        )

    pi = st.get("pi_status") or {}
    m("seestar_battery_percent", pi.get("battery_capacity"), "battery charge")
    m("seestar_battery_temp_celsius", pi.get("battery_temp"), "battery temperature")
    m("seestar_board_temp_celsius", pi.get("temp"), "board temperature")
    m("seestar_charging", pi.get("charge_online"), "1 when external power is connected")
    m("seestar_overtemp", pi.get("is_overtemp"), "1 when the board reports overtemperature")
    m("seestar_battery_overtemp", pi.get("battery_overtemp"), "1 when the battery is too hot")

    bal = (st.get("balance_sensor") or {}).get("data") or {}
    tilt = bal.get("angle")
    for axis in "xyz":
        m("seestar_accel_g", bal.get(axis), "balance sensor (accelerometer) axis", {"axis": axis})
    m("seestar_tilt_degrees", tilt, "balance sensor tilt from level (about 90 - lat on a wedge)")
    if _num(tilt) is not None:
        # The angle is the z axis's to gravity: ~90 wherever z lies level (upright on a
        # desk or on any side, measured 2026-10-19), 90 - latitude on the wedge.
        on_desk = abs(tilt - 90) <= on_desk_deg
        m("seestar_on_desk", on_desk, f"1 when tilt is within {on_desk_deg:g} deg of 90")

    comp = (st.get("compass_sensor") or {}).get("data") or {}
    m("seestar_compass_direction_degrees", comp.get("direction"), "compass heading")
    m("seestar_compass_interference", comp.get("interference"), "compass interference flag")
    m("seestar_compass_stable", comp.get("stable"), "compass stable flag")
    m("seestar_compass_declination_degrees", comp.get("declination"), "magnetic declination")
    for axis in "xyz":
        m("seestar_magnetometer", comp.get(axis), "compass raw axis", {"axis": axis})
    ll = st.get("location_lon_lat")
    if isinstance(ll, list) and len(ll) == 2:
        m("seestar_location_lon_degrees", ll[0], "location stored in the scope")
        m("seestar_location_lat_degrees", ll[1], "location stored in the scope")

    mount = st.get("mount") or {}
    m("seestar_mount_stowed", mount.get("close"), "1 when the arm is closed")
    m("seestar_mount_tracking", mount.get("tracking"), "1 while tracking")
    m("seestar_mount_eq_mode", mount.get("equ_mode"), "1 in equatorial mode")
    if "move_type" in mount:
        m("seestar_mount_moving", mount["move_type"] != "none", "1 while the mount is moving")

    sta = st.get("station") or {}
    m("seestar_wifi_signal_dbm", sta.get("sig_lev"), "Wi-Fi signal level")
    m("seestar_wifi_freq_mhz", sta.get("freq"), "Wi-Fi frequency")

    for vol in (st.get("storage") or {}).get("storage_volume") or []:
        lab = {"volume": vol.get("name", "")}
        if _num(vol.get("free_mb")) is not None:
            m("seestar_storage_free_bytes", vol["free_mb"] * 2**20, "free storage", lab)
        if _num(vol.get("total_mb")) is not None:
            m("seestar_storage_total_bytes", vol["total_mb"] * 2**20, "total storage", lab)
        m("seestar_storage_used_percent", vol.get("used_percent"), "storage used", lab)

    m("seestar_camera_gain", (st.get("camera") or {}).get("gain"), "camera gain")

    # Pushed events: every top-level number (stacked_frame, dropped_frame, exp_us, ...)
    # and the state string, labelled by event, so new firmware fields appear unaided.
    for name, (ev, seen) in sorted(events.items()):
        m(
            "seestar_event_timestamp_seconds",
            seen,
            "when this event was last pushed",
            {"event": name},
        )
        if isinstance(ev.get("state"), str):
            m(
                "seestar_event_state",
                1,
                "the event's last state",
                {"event": name, "state": ev["state"]},
            )
        for path, v in _flat(
            {k: x for k, x in ev.items() if k not in ("Event", "Timestamp", "id")}
        ):
            m(
                "seestar_event_value",
                v,
                "numeric field of the last event (nested as a.b.0)",
                {"event": name, "field": path},
            )

    # Every number in the device state, whatever the firmware adds.
    for path, v in _flat(st):
        block, _, field = path.partition(".")
        m(
            "seestar_state_value",
            v,
            "numeric field of the device state (nested as a.b.0)",
            {"block": block, "field": field or block},
        )
    return "\n".join(out) + "\n"


def serve(host, port=9877, poll_s=30.0, on_desk_deg=10.0, pem=None):
    cache = Cache()
    key = load_key(pem) if pem else None  # a missing key fails here, at startup
    if key is None:
        log.warning("no SEESTAR_PEM: firmware 9.75 will not answer get_device_state")
    hold_s = float(os.environ.get("NATIVE_HOLD_S", NATIVE_HOLD_S))
    every_s = float(os.environ.get("NATIVE_EVERY_S", NATIVE_EVERY_S))
    if every_s > 0:  # NATIVE_EVERY_S=0: Alpaca only, the control port is never touched
        poller = Poller(host, cache, poll_s, hold_s=hold_s, every_s=every_s, key=key)
        threading.Thread(target=poller.run, daemon=True).start()
    connect = [d for d in os.environ.get("ALPACA_CONNECT", "focuser,switch").split(",") if d]
    alpaca = AlpacaPoller(host, cache, poll_s, connect=connect)
    threading.Thread(target=alpaca.run, daemon=True).start()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.split("?")[0] != "/metrics":
                self.send_error(404)
                return
            body = render(
                cache, poll_s=poll_s, on_desk_deg=on_desk_deg, native_every_s=every_s
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    log.info("serving :%s/metrics for scope %s", port, host)
    ThreadingHTTPServer(("", port), Handler).serve_forever()


def main_env(args=None):
    """`terminus exporter`: flags fall back to the container's environment."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    host = getattr(args, "host", None) or os.environ.get("SEESTAR_HOST")
    if not host:
        raise SystemExit("error: set SEESTAR_HOST or pass --host")
    serve(
        host,
        port=getattr(args, "port", None) or int(os.environ.get("EXPORTER_PORT", 9877)),
        poll_s=float(os.environ.get("POLL_S", 30)),
        on_desk_deg=float(os.environ.get("ON_DESK_DEG", 10)),
        pem=os.environ.get("SEESTAR_PEM"),
    )
