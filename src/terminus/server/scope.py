"""The app's telescope link: the Seestar over ASCOM Alpaca, no interop key.

Every motion goes through `sweep.Pointer`, so the Sun guard lives here in the
engine and every route (dev ones included) passes through it. The UI only shows
what this reports.
"""

import io
import json
import logging
import socket
import time

import numpy as np

from ..alpaca import PORT, Alpaca
from ..client import SeestarError
from ..config import SWEEP_DEFAULTS
from ..sweep import Pointer, Sky, is_stowed

log = logging.getLogger("terminus.server")
DISCOVERY_PORT = 32227
DISCOVERY_S = 2.0


def discover(timeout=DISCOVERY_S, to=("255.255.255.255", DISCOVERY_PORT)):
    """Alpaca servers answering a broadcast on this network: [{"host", "port"}]."""
    try:
        return _broadcast(timeout, to)
    except OSError as e:  # no network, or broadcast refused
        raise SeestarError(f"could not search for telescopes: {e}") from e


def _broadcast(timeout, to):
    found = {}
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.settimeout(0.3)
        s.sendto(b"alpacadiscovery1", to)
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                data, (host, _) = s.recvfrom(1024)
            except TimeoutError:
                continue
            try:
                found[host] = int(json.loads(data)["AlpacaPort"])
            except (ValueError, KeyError, TypeError):
                continue  # not an Alpaca reply
    return [{"host": h, "port": p} for h, p in sorted(found.items())]


SATURATED = 65000  # raw counts: the S50's frames topped out at 65504 in daylight (2026-10-07)


def preview_jpeg(raw):
    """A raw GRBG frame (red at [0,1], blue at [1,0]) as a colour JPEG at half size."""
    from PIL import Image

    a = raw.astype(np.float32)
    rgb = np.stack([a[0::2, 1::2], (a[0::2, 0::2] + a[1::2, 1::2]) / 2, a[1::2, 0::2]], -1)
    top = max(float(np.percentile(rgb, 99.5)), 1.0)
    out = (np.clip(rgb / top, 0, 1) ** (1 / 2.2) * 255).astype(np.uint8)
    buf = io.BytesIO()
    Image.fromarray(out).save(buf, "JPEG", quality=85)
    return buf.getvalue()


class NoScope:
    """No telescope linked."""

    link = "none"
    host = None

    def status(self):
        return {"link": self.link}

    def park(self):
        log.info("park: no scope linked, nothing to park")

    def point(self, az, alt):
        raise SeestarError("no telescope is linked")

    def frame(self, exposure_s):
        raise SeestarError("no telescope is linked")

    def preview_jpeg(self):
        return None

    def close(self):
        pass


class AlpacaScope:
    link = "alpaca"

    def __init__(self, host, port=PORT, connect=Alpaca):
        self.host = host
        self.sc = connect(host, port)
        lon, lat = self.sc.location()
        self.sky = Sky(lat, lon)
        sw = SWEEP_DEFAULTS
        self.ptr = Pointer(self.sc, self.sky, sw["sun_cone_deg"], sw["slew_step_deg"])
        self.last = None

    def status(self):
        rd = self.sc.equ_coord()
        if rd is None:
            raise SeestarError("the telescope did not report where it is pointing")
        ra, dec = rd
        az, alt = self.sky.radec_to_altaz(ra, dec)  # never the mount's own alt/az in EQ
        saz, salt = self.sky.sun()
        return {
            "link": self.link,
            "host": self.host,
            "eq": self.sc.is_eq_mode(),
            "az": round(float(az), 2),
            "alt": round(float(alt), 2),
            "stowed": is_stowed((ra, dec)),
            "moving": self.sc.moving(),
            "sun": {"az": round(saz, 1), "alt": round(salt, 1)},
            "cone": self.ptr.cone,
        }

    def park(self):
        self.ptr.park()

    def point(self, az, alt):
        """Slew to (az, alt): Pointer's Sun-checked route, every waypoint."""
        self.ptr.point_to(az, alt)

    def frame(self, exposure_s):
        """One raw frame where the scope points; kept for the preview."""
        self.last = self.sc.capture_raw16(exposure_s)
        return {
            "exposure_ms": round(exposure_s * 1000.0, 3),
            "median": float(np.median(self.last)),
            "saturated": round(float((self.last >= SATURATED).mean()), 4),
        }

    def preview_jpeg(self):
        """The last frame in colour (GRBG Bayer, half size), or None."""
        if self.last is None:
            return None
        return preview_jpeg(self.last)

    def close(self):
        self.sc.close()
