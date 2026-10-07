"""The app's telescope link: the Seestar over ASCOM Alpaca, no interop key.

Every motion goes through `sweep.Pointer`, so the Sun guard lives here in the
engine and every route (dev ones included) passes through it. The UI only shows
what this reports.
"""

import json
import logging
import socket
import time

from ..alpaca import PORT, Alpaca
from ..client import SeestarError
from ..config import SWEEP_DEFAULTS
from ..sweep import Pointer, Sky, is_stowed

log = logging.getLogger("terminus.server")
# Alpaca refuses any target below its horizon ("below horizon", 2026-10-07), so
# Pointer's routes and escapes must stay above it.
ALPACA_FLOOR_DEG = 0.0
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


class NoScope:
    """No telescope linked."""

    link = "none"
    host = None

    def status(self):
        return {"link": self.link}

    def park(self):
        log.info("park: no scope linked, nothing to park")

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
        self.ptr.MIN_ALT_DEG = ALPACA_FLOOR_DEG

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

    def close(self):
        self.sc.close()
