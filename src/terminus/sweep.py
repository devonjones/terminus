"""Horizon sweep: point the Seestar around the sky and, at each azimuth, find
the altitude where clear sky meets an obstruction.

Coordinates: pointing is by RA/Dec goto (reliable in EQ mode) with az/alt
converted through astropy for the moment of pointing, so the mask is in TRUE
az/alt to the accuracy of the polar alignment.

Safety: the mount slews along its RA and Dec axes, so a goto traces a path
through RA/Dec space rather than through az/alt. Every candidate path (the
diagonal and both L-routes, since the axes may drive together or in sequence) is
sampled, converted to az/alt, and checked against the Sun's live position before
any motion; anything inside `sun_cone_deg` is refused, and a route over the top
is tried first. Endpoint-only checks are not sufficient — they let the tube swing
across the Sun between two safe positions.
"""

import datetime
import math
import os
import time
import warnings

import numpy as np

warnings.filterwarnings("ignore")
import astropy.units as u  # noqa: E402
from astropy.coordinates import AltAz, EarthLocation, SkyCoord, get_sun  # noqa: E402
from astropy.time import Time  # noqa: E402

SETTLE = 1.5
GOTO_TIMEOUT = 35


class SunGuard(Exception):
    pass


def _now():
    return Time(datetime.datetime.now(datetime.UTC))


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


# ---- classifier -----------------------------------------------------------
# Sky is the light source; everything terrestrial is lit BY it and silhouettes
# darker. Luminance is therefore the robust discriminator, and it holds whether
# the sky is blue, overcast, or twilight-lit. Colour alone does not work: a dark
# branch silhouetted against bright sky still reads blue (scattered skylight plus
# optical blur), so a blue-dominance test scores obstructions as sky.
#
# Clouds are bright, so they classify as sky — which is what a HORIZON mask wants:
# it records permanent terrain, not weather.
SKY_LUM_FRACTION = 0.55  # a pixel below this fraction of the sky reference is obstruction


def sky_reference(rgb):
    """Brightness level representing open sky in this frame (95th percentile)."""
    return float(np.percentile(rgb.sum(2) / 3.0, 95))


def classify(rgb, sky_ref=None):
    """Return (sky, veg, structure, median_lum) area fractions.

    sky_ref: the luminance of open sky, from a known-clear frame in the same
    column (see `sky_reference`). Passing it makes the split absolute, so a frame
    filled entirely with obstruction is still recognised as dark. Without it the
    frame's own 95th percentile is used, which only works when the frame contains
    some sky.

    Vegetation vs structure is a best-effort tag on the obstruction pixels and is
    only meaningful in daylight; near sunset everything silhouettes to neutral
    black and obstructions come back as 'structure'.
    """
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    lum = (r + g + b) / 3.0
    ref = sky_ref if sky_ref else sky_reference(rgb)
    sky = lum >= SKY_LUM_FRACTION * ref
    obstr = ~sky
    veg = obstr & (g > b) & (r > b) & ((g - b) > 0.10 * (g + 1.0))
    structure = obstr & ~veg
    return float(sky.mean()), float(veg.mean()), float(structure.mean()), float(np.median(lum))


def obstruction_type(veg_frac, struct_frac):
    if veg_frac + struct_frac < 0.15:
        return "open"
    return "tree" if veg_frac >= struct_frac else "structure"


# ---- path-safe pointing ---------------------------------------------------
PATH_SAMPLES = 40  # points sampled along a candidate slew path when Sun-checking


def wrap_ra(d_hours):
    """Shortest signed RA difference, in hours."""
    return (d_hours + 12.0) % 24.0 - 12.0


class Pointer:
    """Points the scope by RA/Dec goto, refusing any slew whose PATH approaches
    the Sun.

    The mount moves along its RA and Dec axes, so a slew traces a path through
    RA/Dec space — not through az/alt. Checking only the endpoints (or stepping
    in az/alt) can therefore miss a tube that swings across the Sun in between.
    Here every candidate path is sampled in RA/Dec, converted to az/alt, and
    checked against the Sun's live position. Because the mount may drive the two
    axes together or one-then-the-other, all three plausible shapes are checked:
    the diagonal and both L-routes. If the direct path is unsafe, a route "over
    the top" (via high altitude) is tried before giving up.
    """

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

    def path_min_sep(self, rd0, rd1):
        """Smallest Sun separation (deg) over every plausible RA/Dec path from
        rd0 to rd1. Sun position is recomputed now, at call time."""
        saz, salt = self.sky.sun()
        ra0, dec0 = rd0
        ra1, dec1 = rd1
        dra = wrap_ra(ra1 - ra0)
        worst = 180.0
        for shape in ("diag", "ra_first", "dec_first"):
            for i in range(PATH_SAMPLES + 1):
                t = i / PATH_SAMPLES
                if shape == "diag":
                    ra, dec = ra0 + dra * t, dec0 + (dec1 - dec0) * t
                elif shape == "ra_first":
                    ra = ra0 + dra * min(1.0, 2 * t)
                    dec = dec0 + (dec1 - dec0) * max(0.0, 2 * t - 1)
                else:
                    dec = dec0 + (dec1 - dec0) * min(1.0, 2 * t)
                    ra = ra0 + dra * max(0.0, 2 * t - 1)
                az, alt = self.sky.radec_to_altaz(ra % 24.0, dec)
                worst = min(worst, ang_sep(az, alt, saz, salt))
        return worst

    def _goto_wait(self, ra, dec, settle):
        self.sc.goto(ra, dec)
        deadline = time.time() + GOTO_TIMEOUT
        while time.time() < deadline:
            time.sleep(0.7)
            rd = self.sc.equ_coord()
            if rd and abs(wrap_ra(rd[0] - ra)) < 0.05 and abs(rd[1] - dec) < 0.5:
                break
        time.sleep(settle)

    def point_to(self, az, alt):
        """Slew to (az, alt) along a Sun-safe path. Returns the final az/alt."""
        self._sun_check(az, alt)
        if self.dry:
            return az, alt
        cur = self.sc.equ_coord()
        if cur is None:
            raise SunGuard("cannot read current pointing; refusing to slew")
        self._sun_check(*self.sky.radec_to_altaz(*cur))
        target = self.sky.altaz_to_radec(az, alt)

        if self.path_min_sep(cur, target) >= self.cone:
            self._goto_wait(*target, SETTLE)  # whole path is clear
        else:
            # Route over the top: the Sun is never at high altitude from a
            # mid-latitude site, so a high waypoint clears it when a direct
            # slew would not.
            via_alt = min(85.0, max(alt, 70.0))
            via_az = (az + wrap180(self.current_azalt()[0] - az) / 2) % 360.0
            via = self.sky.altaz_to_radec(via_az, via_alt)
            if (
                self.path_min_sep(cur, via) >= self.cone
                and self.path_min_sep(via, target) >= self.cone
            ):
                self._goto_wait(*via, 0.3)
                self._goto_wait(*target, SETTLE)
            else:
                raise SunGuard(
                    f"no Sun-safe path to ({az:.0f},{alt:.0f}) "
                    f"(direct min sep {self.path_min_sep(cur, target):.1f} deg)"
                )
        rd = self.sc.equ_coord()
        return self.sky.radec_to_altaz(*rd) if rd else (az, alt)


# ---- horizon search -------------------------------------------------------
def column_touches_sun(sky, az, alt_min, alt_max, cone):
    saz, salt = sky.sun()
    return (
        ang_sep(az, max(alt_min, 0.0), saz, salt) < cone or ang_sep(az, alt_max, saz, salt) < cone
    )


EDGE_REL = 0.20  # a drop this large, relative to the column's brightest sample,
#                  counts as the sky/terrain edge rather than a lighting gradient


def find_edge(profile):
    """Locate the sky->terrain step in a column brightness profile.

    `profile` is [(alt, mean_lum), ...] ordered from high altitude down. The
    horizon is the largest DROP between consecutive samples. Working on the
    difference rather than an absolute level is what makes this survive twilight:
    the sky itself brightens or dims steadily with altitude (a strong vertical
    gradient near sunset), but only terrain produces a step.

    Returns (index_above_edge, drop, relative_drop); index is None if no step
    stands out from the column's own gradient.
    """
    if len(profile) < 2:
        return None, 0.0, 0.0
    peak = max(lum for _, lum in profile) or 1.0
    drops = [(profile[i][1] - profile[i + 1][1], i) for i in range(len(profile) - 1)]
    drop, idx = max(drops)
    return (idx, drop, drop / peak) if drop / peak >= EDGE_REL else (None, drop, drop / peak)


def scan_horizon(ptr, sc, az, alt_min, alt_max, coarse_step, tol, sky_ref=None):
    """Walk a column from `alt_max` down, then refine the brightness step.

    Returns (horizon_alt, status, obstruction_type, profile). `profile` is the
    raw [(alt, mean_lum)] samples — recorded so a column can be re-judged later
    without re-observing it.
    """
    if ptr.dry:
        return alt_min, "open_to_min", "open", []

    def sample(alt):
        ptr.point_to(az, alt)
        rgb = sc.capture_rgb(warmup=0.3)
        return rgb, float((rgb.sum(2) / 3.0).mean())

    profile, frames = [], {}
    alt = alt_max
    while alt >= alt_min - 1e-6:
        rgb, lum = sample(alt)
        profile.append((round(alt, 1), round(lum, 1)))
        frames[round(alt, 1)] = rgb
        alt -= coarse_step

    idx, drop, rel = find_edge(profile)
    if idx is None:
        # No step: either the whole column is open, or it is blocked all the way
        # up. Only brightness relative to known open sky can tell those apart, so
        # without a reference the column is reported as unknown rather than
        # guessed at — an unmeasured azimuth is safer than a wrong one.
        peak = max(lum for _, lum in profile)
        if sky_ref is None:
            return alt_max, "no_reference", "unknown", profile
        if peak < 0.5 * sky_ref:
            dark = frames[profile[0][0]]
            _, v, st, _ = classify(dark, sky_ref)
            return alt_max, "blocked_above", obstruction_type(v, st), profile
        return alt_min, "open_to_min", "open", profile

    hi_alt, hi_lum = profile[idx]
    lo_alt, lo_lum = profile[idx + 1]
    mid_lum = (hi_lum + lo_lum) / 2.0  # halfway across this column's own step
    lo, hi = lo_alt, hi_alt
    while hi - lo > tol:
        mid = (lo + hi) / 2.0
        _, lum = sample(mid)
        if lum >= mid_lum:
            hi = mid
        else:
            lo = mid
    _, v, st, _ = classify(frames[lo_alt], hi_lum)
    return round(hi, 1), f"edge(rel {rel:.2f})", obstruction_type(v, st), profile


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
    """Sweep azimuths, returning ({az: (alt, type)}, [skipped_az], {az: profile}).

    Each column's raw brightness profile is returned alongside the verdict, so a
    run can be re-judged later without re-observing the sky.
    """
    ptr = Pointer(sc, sky, cfg["sun_cone_deg"], cfg["slew_step_deg"], dry)
    saz, salt = sky.sun()
    log(f"Sun az {saz:.0f} alt {salt:.0f}", flush=True)
    mask, skipped, profiles = {}, [], {}
    # Seed the open-sky brightness from a near-zenith frame, opposite the Sun.
    # Without it the first column has nothing to compare against, and a fully
    # blocked column is indistinguishable from a clear one.
    sky_ref = None
    if not dry:
        try:
            zen_az = (saz + 180.0) % 360.0
            ptr.point_to(zen_az, 75.0)
            sky_ref = sky_reference(sc.capture_rgb(warmup=0.3))
            log(f"sky reference (az {zen_az:.0f} alt 75): {sky_ref:.1f}", flush=True)
        except (SunGuard, OSError) as e:
            log(f"could not seed sky reference: {e}", flush=True)
    for az in range(int(az_start), int(az_end) + 1, cfg["az_step"]):
        if column_touches_sun(sky, az, cfg["alt_min"], cfg["alt_max"], cfg["sun_cone_deg"]):
            skipped.append(az)
            log(f"az {az:3d}: skipped (Sun cone)", flush=True)
            continue
        try:
            alt, status, typ, profile = scan_horizon(
                ptr,
                sc,
                az,
                cfg["alt_min"],
                cfg["alt_max"],
                cfg.get("coarse_step", 5.0),
                cfg["alt_tol"],
                sky_ref,
            )
            if profile:
                profiles[az] = profile
                peak = max(lum for _, lum in profile)
                sky_ref = peak if sky_ref is None else max(sky_ref, peak)
            frame = (
                save_boundary_frame(sc, ptr, az, alt, typ, save_dir)
                if (save_dir and not dry)
                else "-"
            )
            mask[az] = (alt, typ)
            log(f"az {az:3d}: alt {alt:5.1f}  {typ:9s} [{status}]  {frame}", flush=True)
        except SunGuard as e:
            skipped.append(az)
            log(f"az {az:3d}: skipped ({e})", flush=True)
    return mask, skipped, profiles
