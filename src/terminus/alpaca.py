"""The Seestar over ASCOM Alpaca (port 32323): no interop key needed.

Speaks the subset of `client.Seestar` that `sweep.Pointer` and the night scan
use, so every slew still goes through `Pointer.point_to` and its Sun checks.
Enable "Alpaca" in the Seestar app; the scope must be in EQ mode.

The Telescope reports EquatorialSystem 1 (JNow): its RA/Dec reproduce its own
az/alt to 0.003 deg read as JNow, 0.3 deg off read as J2000 (2026-10-06). The
rest of terminus works in ICRS, so this converts at the boundary.

The server drops requests under load, so every call has a long timeout and
retries. Measured 2026-10-05: setting Tracking returns 1279 and takes anyway,
and gotos are refused while it is off.
"""

import http.client
import itertools
import json
import logging
import struct
import time
import urllib.error
import urllib.parse
import urllib.request

import astropy.units as u
import numpy as np
from astropy.coordinates import TETE, SkyCoord
from astropy.time import Time

from .client import SeestarError

log = logging.getLogger(__name__)
PORT = 32323
TIMEOUT_S = 30
RETRIES = 3
CLIENT_ID = 7


def icrs_to_jnow(ra_h, dec_d, when=None):
    c = SkyCoord(ra=ra_h * u.hourangle, dec=dec_d * u.deg).transform_to(
        TETE(obstime=when or Time.now())
    )
    return c.ra.hourangle, c.dec.deg


def jnow_to_icrs(ra_h, dec_d, when=None):
    c = SkyCoord(TETE(ra=ra_h * u.hourangle, dec=dec_d * u.deg, obstime=when or Time.now())).icrs
    return c.ra.hourangle, c.dec.deg


def parse_imagebytes(blob):
    """An Alpaca ImageBytes response as a 2-D array (rows = y).

    Header: 11 little-endian int32s. Element types: 1 int16, 2 int32, 3 double,
    6 byte, 8 uint16. Alpaca sends the array x-major, so it is transposed here.
    """
    _ver, err, _ctid, _stid, start, _img_t, xmit_t, rank, d1, d2, _d3 = struct.unpack(
        "<11i", blob[:44]
    )
    if err:
        raise SeestarError(f"camera image error {err}: {blob[start:].decode(errors='replace')}")
    if rank != 2:
        raise SeestarError(f"camera image has rank {rank}; expected a mono/Bayer frame")
    dtype = {1: "<i2", 2: "<i4", 3: "<f8", 6: "u1", 8: "<u2"}[xmit_t]
    return np.frombuffer(blob, dtype=dtype, offset=start, count=d1 * d2).reshape(d1, d2).T


def _check(d, what):
    """An Alpaca reply, or the scope's own error raised."""
    if d.get("ErrorNumber"):
        raise SeestarError(f"{what}: {d['ErrorNumber']} {d.get('ErrorMessage')}")
    return d


class Alpaca:
    def __init__(self, host, port=PORT):
        self.base = f"http://{host}:{port}/api/v1"
        self.img = None  # the native client's imaging socket; nothing to close here
        self._txn = itertools.count(1)
        self._cam_on = False
        self._put("telescope", "connected", Connected="true")

    # ---- transport -------------------------------------------------------
    def _request(self, req, raw=False):
        last = None
        for attempt in range(RETRIES):
            try:
                with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
                    body = r.read()
                    if raw and r.headers.get_content_type() == "application/imagebytes":
                        return body
                    return json.loads(body)
            except urllib.error.HTTPError as e:
                # The server answered and refused: retrying repeats the refusal.
                reason = e.read().decode(errors="replace")[:200]
                raise SeestarError(f"Alpaca refused {req.full_url}: {e.code} {reason}") from e
            except ValueError as e:  # not JSON
                raise SeestarError(f"Alpaca sent an unreadable reply to {req.full_url}") from e
            except (urllib.error.URLError, OSError, http.client.HTTPException) as e:
                last = e
                time.sleep(1 + attempt)
        raise SeestarError(f"Alpaca request failed after {RETRIES} tries: {req.full_url}: {last}")

    def _ids(self):
        return {"ClientID": CLIENT_ID, "ClientTransactionID": next(self._txn)}

    def _get(self, dev, prop, **params):
        q = urllib.parse.urlencode({**params, **self._ids()})
        d = self._request(urllib.request.Request(f"{self.base}/{dev}/0/{prop}?{q}"))
        return _check(d, f"{dev}.{prop}").get("Value")

    def _put(self, dev, method, check=True, **params):
        data = urllib.parse.urlencode({**params, **self._ids()}).encode()
        d = self._request(
            urllib.request.Request(f"{self.base}/{dev}/0/{method}", data, method="PUT")
        )
        return _check(d, f"{dev}.{method}") if check else d

    # ---- mount (the client.Seestar subset Pointer uses) ------------------
    def equ_coord(self):
        """(ra_hours, dec_deg) in ICRS, or None when it cannot be read: the
        native link's contract, which Pointer and the sweep turn into a skipped
        column rather than a lost run."""
        try:
            ra, dec = self._get("telescope", "rightascension"), self._get(
                "telescope", "declination"
            )
        except SeestarError as e:
            log.warning("could not read the pointing: %s", e)
            return None
        return jnow_to_icrs(ra, dec)

    def is_eq_mode(self):
        return self._get("telescope", "alignmentmode") in (1, 2)  # polar, German polar

    def location(self):
        return self._get("telescope", "sitelongitude"), self._get("telescope", "sitelatitude")

    def moving(self):
        return bool(self._get("telescope", "slewing"))

    def goto(self, ra_hours, dec_deg):
        if not self._get("telescope", "tracking"):
            self._put("telescope", "tracking", check=False, Tracking="true")  # answers 1279, takes
            if not self._get("telescope", "tracking"):
                raise SeestarError("tracking will not turn on; the mount refuses gotos without it")
        ra, dec = icrs_to_jnow(ra_hours, dec_deg)
        self._put("telescope", "slewtocoordinatesasync", RightAscension=ra, Declination=dec)

    def park(self):
        self._put("telescope", "park")

    def abort(self):
        self._put("telescope", "abortslew", check=False)

    # ---- camera ----------------------------------------------------------
    def _camera(self):
        if not self._cam_on:
            self._put("camera", "connected", Connected="true")
            self._cam_on = True

    def capture_raw16(self, exposure_s=2.0):
        """One raw frame exposed at the current pointing (the night measurement)."""
        self._camera()
        self._put("camera", "startexposure", Duration=exposure_s, Light="true")
        # Never take a "ready" from before this exposure could have finished:
        # it would be the previous pointing's frame.
        time.sleep(exposure_s)
        deadline = time.time() + exposure_s + 60
        while not self._get("camera", "imageready"):
            if time.time() > deadline:
                raise SeestarError(
                    f"no image {exposure_s + 60:.0f}s after a {exposure_s}s exposure"
                )
            time.sleep(0.5)
        req = urllib.request.Request(
            f"{self.base}/camera/0/imagearray?{urllib.parse.urlencode(self._ids())}",
            headers={"Accept": "application/imagebytes"},
        )
        body = self._request(req, raw=True)
        if isinstance(body, dict):  # the server ignored Accept: JSON ImageArray, x-major
            return np.asarray(_check(body, "camera.imagearray")["Value"]).T
        return parse_imagebytes(body)

    def capture_raw16_median(self, exposure_s=2.0):
        return float(np.median(self.capture_raw16(exposure_s)))

    def capture_rgb(self, *a, **k):
        self.lock_exposure()

    def lock_exposure(self, exp_ms=None, gain=None):
        raise SeestarError(
            "daytime measurement over Alpaca is not built yet: use the native link "
            '([scope] link = "native" with a pem), or measure after dark'
        )

    # ---- the native client's channel calls: nothing to do over Alpaca ----
    def start_view(self, mode="scenery"):
        pass

    def stop_view(self):
        pass

    def close_imaging(self):
        pass

    def reconnect(self):
        pass

    def close(self):
        for dev in ("camera", "telescope"):
            try:
                self._put(dev, "connected", check=False, Connected="false")
            except SeestarError as e:
                # Courtesy only: the scope keeps running either way.
                log.warning("could not disconnect the %s: %s", dev, e)
