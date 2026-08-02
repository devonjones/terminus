"""Horizon sweep: point the Seestar around the sky and, at each azimuth, find
the altitude where clear sky meets an obstruction.

Coordinates: pointing is by RA/Dec goto (reliable in EQ mode) with az/alt
converted through astropy for the moment of pointing, so the mask is in TRUE
az/alt to the accuracy of the polar alignment.

Safety: slews are broken into small steps (<= slew_step_deg); at every step the
Sun's position is recomputed for the exact current time and the target is
refused inside `sun_cone_deg`. Because each step is short and both ends are kept
outside the cone, the tube cannot arc through the Sun between waypoints.
"""

import datetime
import math
import os
import time
import warnings

import numpy as np

warnings.filterwarnings("ignore")
from astropy.coordinates import AltAz, EarthLocation, SkyCoord, get_sun  # noqa: E402
from astropy.time import Time  # noqa: E402
import astropy.units as u  # noqa: E402

SETTLE = 1.5
GOTO_TIMEOUT = 35


class SunGuard(Exception):
    pass


def _now():
    return Time(datetime.datetime.now(datetime.timezone.utc))


def wrap180(d):
    return (d + 180.0) % 360.0 - 180.0


def ang_sep(az1, alt1, az2, alt2):
    a1, d1, a2, d2 = map(math.radians, (az1, alt1, az2, alt2))
    c = math.sin(d1) * math.sin(d2) + math.cos(d1) * math.cos(d2) * math.cos(a1 - a2)
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


class Sky:
    """Coordinate transforms and Sun position for one observing site."""

    def __init__(self, lat, lon, elev_m=0.0):
        self.loc = EarthLocation(lat=lat * u.deg, lon=lon * u.deg, height=elev_m * u.m)

    def altaz_to_radec(self, az, alt, when=None):
        c = SkyCoord(
            AltAz(az=az * u.deg, alt=alt * u.deg, obstime=when or _now(), location=self.loc)
        ).icrs
        return c.ra.hourangle, c.dec.deg

    def radec_to_altaz(self, ra_h, dec_d, when=None):
        aa = SkyCoord(ra=ra_h * u.hourangle, dec=dec_d * u.deg).transform_to(
            AltAz(obstime=when or _now(), location=self.loc)
        )
        return aa.az.deg, aa.alt.deg

    def sun(self):
        now = _now()
        s = get_sun(now).transform_to(AltAz(obstime=now, location=self.loc))
        return float(s.az.deg), float(s.alt.deg)


# ---- color classifier -----------------------------------------------------
def classify(rgb):
    """Return (sky, veg, structure, median_lum) area fractions.
    sky = blue-dominant + bright; vegetation = green/yellow (tree); structure =
    any other non-sky (roof/wall/fence). Blue-dominance survives auto-exposure."""
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    lum = r + g + b
    lmax = lum.max() if lum.max() > 0 else 1.0
    sky = (b > r * 1.06) & (b > g * 0.90) & (lum > 0.20 * lmax)
    veg = (~sky) & (g > b) & (r > b) & ((g - b) > 0.10 * (g + 1.0))
    structure = (~sky) & (~veg)
    return float(sky.mean()), float(veg.mean()), float(structure.mean()), float(np.median(lum))


def obstruction_type(veg_frac, struct_frac):
    if veg_frac + struct_frac < 0.15:
        return "open"
    return "tree" if veg_frac >= struct_frac else "structure"


# ---- path-safe pointing ---------------------------------------------------
class Pointer:
    def __init__(self, sc, sky, cone, slew_step, dry=False):
        self.sc, self.sky, self.cone, self.slew_step, self.dry = sc, sky, cone, slew_step, dry

    def current_azalt(self):
        rd = self.sc.equ_coord()
        return self.sky.radec_to_altaz(*rd) if rd else (0.0, 90.0)

    def _sun_check(self, az, alt):
        saz, salt = self.sky.sun()
        sep = ang_sep(az, alt, saz, salt)
        if sep < self.cone:
            raise SunGuard(f"({az:.0f},{alt:.0f}) is {sep:.1f} deg from Sun (< {self.cone})")

    def _goto_wait(self, az, alt, settle):
        ra, dec = self.sky.altaz_to_radec(az, alt)
        self.sc.goto(ra, dec)
        deadline = time.time() + GOTO_TIMEOUT
        while time.time() < deadline:
            time.sleep(1.0)
            rd = self.sc.equ_coord()
            if rd and abs(rd[0] - ra) < 0.05 and abs(rd[1] - dec) < 0.5:
                break
        time.sleep(settle)

    def point_to(self, az, alt):
        """Slew to (az, alt) in small, Sun-checked steps. Returns final az/alt."""
        self._sun_check(az, alt)
        if self.dry:
            return az, alt
        cur_az, cur_alt = self.current_azalt()
        d_az, d_alt = wrap180(az - cur_az), alt - cur_alt
        n = max(1, math.ceil(max(abs(d_az), abs(d_alt)) / self.slew_step))
        for i in range(1, n + 1):
            wp_az = (cur_az + d_az * i / n) % 360.0
            wp_alt = cur_alt + d_alt * i / n
            self._sun_check(wp_az, wp_alt)  # exact-time Sun check per step
            self._goto_wait(wp_az, wp_alt, SETTLE if i == n else 0.3)
        rd = self.sc.equ_coord()
        return self.sky.radec_to_altaz(*rd) if rd else (az, alt)


# ---- horizon search -------------------------------------------------------
def column_touches_sun(sky, az, alt_min, alt_max, cone):
    saz, salt = sky.sun()
    return (
        ang_sep(az, max(alt_min, 0.0), saz, salt) < cone or ang_sep(az, alt_max, saz, salt) < cone
    )


def bisect_horizon(ptr, sc, az, alt_min, alt_max, tol, clear_thresh):
    """Return (horizon_alt, status, obstruction_type). A pointing is 'clear' only
    when sky fraction exceeds clear_thresh, so dappled canopy counts as blocked
    (conservative top-of-canopy horizon)."""
    last_obstr = (0.0, 0.0)

    def is_clear(alt):
        nonlocal last_obstr
        ptr.point_to(az, alt)
        if ptr.dry:
            return True
        s, v, st, _ = classify(sc.capture_rgb())
        if s <= clear_thresh:
            last_obstr = (v, st)
        return s > clear_thresh

    if not is_clear(alt_max):
        return alt_max, "blocked_above", obstruction_type(*last_obstr)
    if is_clear(alt_min):
        return alt_min, "open_to_min", "open"
    lo, hi = alt_min, alt_max
    while hi - lo > tol:
        mid = (lo + hi) / 2
        if is_clear(mid):
            hi = mid
        else:
            lo = mid
    return round(hi, 1), "ok", obstruction_type(*last_obstr)


def save_boundary_frame(sc, ptr, az, alt, typ, save_dir):
    from PIL import Image, ImageDraw

    os.makedirs(save_dir, exist_ok=True)
    ptr.point_to(az, alt)
    rgb = sc.capture_rgb()
    lo, hi = np.percentile(rgb, 1), np.percentile(rgb, 99.5)
    im = Image.fromarray(np.clip((rgb - lo) / (hi - lo) * 255, 0, 255).astype(np.uint8))
    ImageDraw.Draw(im).text((12, 12), f"az {az}  alt {alt}  {typ}", fill=(255, 0, 0))
    path = f"{save_dir}/az{int(az):03d}.png"
    im.save(path)
    return path


def run_sweep(sc, sky, cfg, az_start=0, az_end=350, save_dir=None, dry=False, log=print):
    """Sweep azimuths, returning ({az: (alt, type)}, [skipped_az])."""
    ptr = Pointer(sc, sky, cfg["sun_cone_deg"], cfg["slew_step_deg"], dry)
    saz, salt = sky.sun()
    log(f"Sun az {saz:.0f} alt {salt:.0f}")
    mask, skipped = {}, []
    for az in range(int(az_start), int(az_end) + 1, cfg["az_step"]):
        if column_touches_sun(sky, az, cfg["alt_min"], cfg["alt_max"], cfg["sun_cone_deg"]):
            skipped.append(az)
            log(f"az {az:3d}: skipped (Sun cone)")
            continue
        try:
            alt, status, typ = bisect_horizon(
                ptr, sc, az, cfg["alt_min"], cfg["alt_max"], cfg["alt_tol"], cfg["clear_thresh"]
            )
            frame = (
                save_boundary_frame(sc, ptr, az, alt, typ, save_dir)
                if (save_dir and not dry)
                else "-"
            )
            mask[az] = (alt, typ)
            log(f"az {az:3d}: horizon alt {alt:5.1f}  {typ:9s} [{status}]  {frame}")
        except SunGuard as e:
            skipped.append(az)
            log(f"az {az:3d}: skipped ({e})")
    return mask, skipped
