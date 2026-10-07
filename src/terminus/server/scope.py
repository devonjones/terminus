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
from ..config import SWEEP_DEFAULTS
from ..sweep import Pointer, Sky

log = logging.getLogger("terminus.server")
DISCOVERY_PORT = 32227
DISCOVERY_S = 2.0


def discover(timeout=DISCOVERY_S, to=("255.255.255.255", DISCOVERY_PORT)):
    """Alpaca servers answering a broadcast on this network: [{"host", "port"}]."""
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

    def status(self):
        ra, dec = self.sc.equ_coord()
        az, alt = self.sky.radec_to_altaz(ra, dec)  # never the mount's own alt/az in EQ
        saz, salt = self.sky.sun()
        return {
            "link": self.link,
            "host": self.host,
            "eq": self.sc.is_eq_mode(),
            "az": round(float(az), 2),
            "alt": round(float(alt), 2),
            "stowed": abs(abs(float(dec)) - 90.0) < 0.5,
            "moving": self.sc.moving(),
            "sun": {"az": round(saz, 1), "alt": round(salt, 1)},
            "cone": self.ptr.cone,
        }

    def park(self):
        self.ptr.park()

    def close(self):
        self.sc.close()
