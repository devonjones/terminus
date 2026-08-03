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


EDGE_SNR = 2.5  # a step must exceed the column's own sample-to-sample noise by
#                 this factor; below it, the "step" is indistinguishable from
#                 measurement scatter and the column is reported as unmeasured


def find_edge(profile):
    """Locate the sky->terrain step in a column brightness profile.

    `profile` is [(alt, mean_lum), ...] ordered from high altitude down. The fit
    is a change point: for each split, compare the mean brightness above it with
    the mean below, and take the split that maximises the difference. Terrain
    darkens *everything* below the horizon, so a real edge separates two levels;
    a single large drop between neighbouring samples does not qualify, since one
    noisy frame produces exactly that.

    The step is accepted only when it exceeds the column's own sample-to-sample
    noise by EDGE_SNR. That guard matters: with auto-exposure enabled the camera
    normalises each frame toward mid-grey, which flattens real contrast and
    leaves scatter that a naive detector happily reports as a horizon.

    Returns (index_above_edge, step_size, snr); index is None when nothing in the
    column stands clear of its noise.
    """
    if len(profile) < 3:
        return None, 0.0, 0.0
    lums = [lum for _, lum in profile]
    noise = sum(abs(lums[i + 1] - lums[i]) for i in range(len(lums) - 1)) / (len(lums) - 1)
    best = (0.0, None)
    for k in range(1, len(lums)):
        above = sum(lums[:k]) / k
        below = sum(lums[k:]) / (len(lums) - k)
        if above - below > best[0]:
            best = (above - below, k)
    step, k = best
    snr = step / noise if noise > 0 else 0.0
    return (k - 1, step, snr) if (k is not None and snr >= EDGE_SNR) else (None, step, snr)


def save_scan_frame(rgb, az, alt, lum, save_dir):
    """Persist one scan sample. Keeping the whole scan, not just the chosen
    boundary, is what makes a run re-analysable: the classifier can be re-tuned
    against real frames instead of guessed thresholds."""
    import os

    from PIL import Image

    os.makedirs(save_dir, exist_ok=True)
    lo, hi = np.percentile(rgb, 1), np.percentile(rgb, 99.5)
    span = max(hi - lo, 1e-6)
    im = Image.fromarray(np.clip((rgb - lo) / span * 255, 0, 255).astype(np.uint8))
    im.save(f"{save_dir}/az{int(az):03d}_alt{alt:05.1f}_lum{lum:05.1f}.png")


def scan_horizon(
    ptr,
    sc,
    az,
    alt_min,
    alt_max,
    coarse_step,
    tol,
    sky_ref=None,
    frames_dir=None,
    repeats=1,
):
    """Walk a column from `alt_max` down, then refine the brightness step.

    Returns (horizon_alt, status, obstruction_type, profile). `profile` is the
    raw [(alt, mean_lum)] samples — recorded so a column can be re-judged later
    without re-observing it.
    """
    if ptr.dry:
        return alt_min, "open_to_min", "open", []

    def sample(alt):
        """Brightest of `repeats` captures at this pointing.

        Under drifting cloud the sky's brightness varies while terrain stays
        black, so the maximum is what separates them: repeated looks can only
        find sky, never invent it. Averaging would let a momentarily dark sky
        drift toward the terrain level and blur the very step being measured.
        """
        ptr.point_to(az, alt)
        best_rgb, best_lum = None, -1.0
        for _ in range(max(1, repeats)):
            rgb = sc.capture_rgb(warmup=0.3)
            lum = float((rgb.sum(2) / 3.0).mean())
            if lum > best_lum:
                best_rgb, best_lum = rgb, lum
        if frames_dir:
            save_scan_frame(best_rgb, az, alt, best_lum, frames_dir)
        return best_rgb, best_lum

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
        if sky_ref is None:
            return alt_max, "no_reference", "unknown", profile
        lums = sorted(lum for _, lum in profile)
        median = lums[len(lums) // 2]
        # Median, not peak: a blocked column can still contain one bright sample
        # (a gap in foliage, a streetlight, a passing reflection), and judging by
        # the maximum lets that single outlier declare the whole column open.
        if median < 0.5 * sky_ref:
            dark = frames[profile[0][0]]
            _, v, st, _ = classify(dark, sky_ref)
            return alt_max, "blocked_above", obstruction_type(v, st), profile
        if lums[0] >= 0.5 * sky_ref:
            return alt_min, "open_to_min", "open", profile
        # Bright overall but with dark samples that form no clean step: report it
        # as unmeasured rather than inventing a horizon.
        return alt_max, "inconclusive", "unknown", profile

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
                frames_dir=(f"{save_dir}/scan" if (save_dir and not dry) else None),
                repeats=cfg.get("samples_per_point", 1),
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

    # ---- adaptive refinement ------------------------------------------------
    # A uniform step wastes time on flat stretches (a long roofline) and still
    # misses narrow features (a gap between houses, one tree) wherever the
    # horizon moves faster than the step. Subdivide only between neighbours that
    # disagree, which is where the detail actually is.
    refine_to = cfg.get("az_refine_deg", 0)
    trigger = cfg.get("refine_threshold_deg", 10.0)
    budget = cfg.get("refine_max_columns", 24)
    if refine_to and not dry:
        work = True
        while work and budget > 0:
            work = False
            known = sorted(mask)
            for a0, a1 in zip(known, known[1:], strict=False):
                gap = a1 - a0
                if gap > 2 * cfg["az_step"]:
                    continue  # a Sun-skipped hole, not a measured neighbour
                if gap / 2 < refine_to or abs(mask[a1][0] - mask[a0][0]) < trigger:
                    continue
                mid = int(round((a0 + a1) / 2))
                if mid in mask or budget <= 0:
                    continue
                try:
                    alt, status, typ, profile = scan_horizon(
                        ptr,
                        sc,
                        mid,
                        cfg["alt_min"],
                        cfg["alt_max"],
                        cfg.get("coarse_step", 5.0),
                        cfg["alt_tol"],
                        sky_ref,
                        frames_dir=(f"{save_dir}/scan" if save_dir else None),
                    )
                    mask[mid] = (alt, typ)
                    if profile:
                        profiles[mid] = profile
                    if save_dir:
                        save_boundary_frame(sc, ptr, mid, alt, typ, save_dir)
                    budget -= 1
                    work = True
                    log(
                        f"az {mid:3d}: alt {alt:5.1f}  {typ:9s} [refine {a0}-{a1}]",
                        flush=True,
                    )
                except SunGuard as e:
                    log(f"az {mid:3d}: refine skipped ({e})", flush=True)
    return mask, skipped, profiles
