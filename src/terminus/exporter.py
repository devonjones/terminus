"""`terminus exporter`: the Seestar's own state as Prometheus metrics.

Strictly passive. It sends two requests on the control port (4700), both reads
that need no interop key: `get_device_state`, and `get_verify_str` as a
keepalive (the scope closes a socket that is quiet for ~16 s; measured
2026-10-18, firmware 9.75). Everything else comes from the events the scope
pushes on the same socket (Imaging, Exposure, PiStatus, Stack, ...). It never
authenticates, never PUTs, never touches Alpaca, and never calls any other
method: some, like pi_shutdown, are destructive.

While the scope images, get_device_state goes unanswered (no reply in 90 s of a
preview loop), so one request stays outstanding until it is answered, and
PiStatus events keep the battery and temperature current in the meantime.

One thread holds the socket and polls into a cache; /metrics renders the cache,
so a scrape never reaches the scope. A dropped socket or a sleeping scope only
ages the cache: `seestar_up` goes to 0 and the last values keep serving with
their timestamps.

Configuration is by environment, for the container: SEESTAR_HOST,
EXPORTER_PORT (9877), POLL_S (30), ON_DESK_DEG (10).
"""

import json
import logging
import math
import os
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger(__name__)

CONTROL_PORT = 4700
KEEPALIVE_S = 10
DEAD_S = 30  # nothing at all heard for this long, keepalives included: reconnect
ALLOWED_METHODS = frozenset({"get_device_state", "get_verify_str"})


class Cache:
    """What the poller last saw. Written by one thread, read by scrapes."""

    def __init__(self):
        self.lock = threading.Lock()
        self.state = None  # the last get_device_state result, PiStatus events merged in
        self.events = {}  # event name -> (last payload, time seen)
        self.last_poll = 0.0
        self.last_success = 0.0  # last device state
        self.last_heard = 0.0  # last line of any kind
        self.connected = False
        self.failures = 0
        self.connects = 0


class Poller:
    def __init__(self, host, cache, poll_s=30.0, port=CONTROL_PORT):
        self.host, self.port, self.cache, self.poll_s = host, port, cache, poll_s
        self.s = None
        self.buf = b""
        self.cid = 1000
        self.pending = None  # id of the outstanding get_device_state

    def send(self, method):
        if method not in ALLOWED_METHODS:  # the passive guarantee, enforced here
            raise ValueError(f"exporter refuses to call {method}")
        self.cid += 1
        self.s.sendall((json.dumps({"id": self.cid, "method": method}) + "\r\n").encode())
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
            if "Event" in d:
                c.events[str(d["Event"])] = (d, now)
                if d["Event"] == "PiStatus" and c.state is not None:
                    pi = c.state.setdefault("pi_status", {})
                    pi.update({k: v for k, v in d.items() if k not in ("Event", "Timestamp")})
            elif self.pending is not None and d.get("id") == self.pending:
                self.pending = None
                if isinstance(d.get("result"), dict):
                    c.state, c.last_success = d["result"], now
                else:
                    c.failures += 1

    def session(self):
        """One connection, until it drops."""
        self.s = socket.create_connection((self.host, self.port), timeout=10)
        self.s.settimeout(1.0)
        self.buf, self.pending = b"", None
        opened = time.time()
        with self.cache.lock:
            self.cache.connected = True
            self.cache.connects += 1
        next_poll = next_keepalive = 0.0
        while True:
            now = time.time()
            if now - max(opened, self.cache.last_heard) > DEAD_S:
                raise TimeoutError(f"nothing heard for {DEAD_S}s")
            if now >= next_poll and self.pending is None:
                self.pending = self.send("get_device_state")
                with self.cache.lock:
                    self.cache.last_poll = now
                next_poll = now + self.poll_s
                next_keepalive = now + KEEPALIVE_S
            elif now >= next_keepalive:
                self.send("get_verify_str")
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
        """Reconnect forever, backing off to a minute."""
        delay = 1
        while True:
            started = time.time()
            try:
                self.session()
            except Exception as e:  # never die: a scope that is off is the normal case
                log.info("scope %s:%s: %s; retrying in %ss", self.host, self.port, e, delay)
            finally:
                with self.cache.lock:
                    self.cache.connected = False
                    self.cache.failures += 1
                if self.s:
                    self.s.close()
            if self.cache.last_heard > started + 60:
                delay = 1  # the session lasted: a fresh drop retries at once
            time.sleep(delay)
            delay = min(60, delay * 2)


def _num(v):
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, (int, float)) and math.isfinite(v):
        return float(v)
    return None


def _label(v):
    return str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def render(cache, now=None, poll_s=30.0, on_desk_deg=10.0):
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
            cache.last_heard > now - 3 * poll_s,
            "1 when the scope was heard from in three polls",
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
        m(
            "seestar_last_success_timestamp_seconds",
            cache.last_success or None,
            "last device state received",
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

    tilt = ((st.get("balance_sensor") or {}).get("data") or {}).get("angle")
    m("seestar_tilt_degrees", tilt, "balance sensor tilt from level (about 90 - lat on a wedge)")
    if _num(tilt) is not None:
        m("seestar_on_desk", tilt < on_desk_deg, f"1 when tilt is under {on_desk_deg:g} deg")

    comp = (st.get("compass_sensor") or {}).get("data") or {}
    m("seestar_compass_direction_degrees", comp.get("direction"), "compass heading")
    m("seestar_compass_interference", comp.get("interference"), "compass interference flag")

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

    foc = st.get("focuser") or {}
    m("seestar_focuser_position", foc.get("step"), "focuser step")
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
        for k, v in sorted(ev.items()):
            if k not in ("Event", "Timestamp", "id") and _num(v) is not None:
                m(
                    "seestar_event_value",
                    v,
                    "numeric field of the last event",
                    {"event": name, "field": k},
                )
    return "\n".join(out) + "\n"


def serve(host, port=9877, poll_s=30.0, on_desk_deg=10.0):
    cache = Cache()
    threading.Thread(target=Poller(host, cache, poll_s).run, daemon=True).start()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.split("?")[0] != "/metrics":
                self.send_error(404)
                return
            body = render(cache, poll_s=poll_s, on_desk_deg=on_desk_deg).encode()
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
    )
