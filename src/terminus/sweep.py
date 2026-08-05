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
GOTO_TIMEOUT = 90
SKY_REF_MAX_AGE = 420  # re-measure open-sky brightness at least this often (s)
ARRIVE_DEG = 0.6  # goto counts as arrived within this true angular distance
MAX_TARGET_DEC = 88.5  # never command a goto nearer a pole than this
MAX_VIA_DEC = 80.0  # a waypoint nearer a pole than this is unreachable: RA is
#                     singular there and the mount cannot converge


class SunGuard(Exception):
    pass


class PointingError(Exception):
    """A slew did not arrive where it was told to go.

    `partial` carries whatever a sweep had measured before it gave up, so an
    abort late in a multi-hour run does not throw the night away. It defaults to
    None at CLASS level deliberately: `_goto_wait` raises this from deep inside a
    slew where no sweep state exists, and a caller reading `.partial` on one of
    those must get None rather than AttributeError.
    """

    partial = None


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


# Below this Sun altitude the scope's colour tagging is not evidence about the
# obstruction, only about the light. `classify` calls a pixel vegetation when it
# is green-dominant; a silhouette is neutral, fails that test, and falls into
# `structure = obstr & ~veg` by default. So after sunset EVERY column comes back
# "structure", stated with exactly the confidence of a real daylight reading.
#
# Zero is the honest boundary rather than a tuned one: it is where direct
# illumination stops. It is not the whole problem — for some minutes either side
# of it the light is strongly reddened, which pushes r and g above b across the
# whole frame and biases the test the other way. Nobody has measured how wide
# that window is, so this does not pretend to correct for it.
DAYLIGHT_TYPE_MIN_ALT = 0.0


def obstruction_type(veg_frac, struct_frac, sun_alt=None):
    """Vegetation or structure, from colour — a DAYLIGHT-ONLY hint.

    Returns "" when the Sun is down, meaning "this instrument cannot tell". That
    is a different claim from "structure" and must not be written as one: the
    mask is consumed by other software, so a type in it has to have been
    measured. Which instrument did the measuring is recorded alongside it; see
    `type_source` in `export.write_mask`.

    `sun_alt` of None means the caller did not say, and keeps the old behaviour
    so a hand-run daylight probe still works.
    """
    if veg_frac + struct_frac < 0.15:
        return "open"
    if sun_alt is not None and sun_alt < DAYLIGHT_TYPE_MIN_ALT:
        return ""
    return "tree" if veg_frac >= struct_frac else "structure"


# ---- path-safe pointing ---------------------------------------------------
SUN_SAFE_ALT = -3.0  # below this the Sun is occulted by the Earth and harmless
#                      (refraction lifts it about 0.6 deg, so this keeps margin)
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
        if salt < SUN_SAFE_ALT:
            return  # the Earth is between the optics and the Sun
        sep = ang_sep(az, alt, saz, salt)
        if sep < self.cone:
            raise SunGuard(f"({az:.0f},{alt:.0f}) is {sep:.1f} deg from Sun (< {self.cone})")

    def path_min_sep(self, rd0, rd1):
        """Smallest Sun separation (deg) over every plausible RA/Dec path from
        rd0 to rd1. Sun position is recomputed now, at call time."""
        saz, salt = self.sky.sun()
        if salt < SUN_SAFE_ALT:
            return 180.0  # Sun is set; no path can approach it
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

    def _moving(self):
        try:
            m = self.sc.call("get_device_state").get("result", {}).get("mount", {})
            return m.get("move_type") not in (None, "none")
        except Exception:
            return False

    def _goto_wait(self, ra, dec, settle):
        self.sc.goto(ra, dec)
        deadline = time.time() + GOTO_TIMEOUT
        while time.time() < deadline:
            time.sleep(0.7)
            rd = self.sc.equ_coord()
            # True angular separation, never a raw RA difference. RA converges to
            # a singularity at the poles: at this site, due north at altitude
            # ~= latitude IS the celestial pole, so scanning azimuth 0 walks
            # straight onto it and an arcminute of position error there swings RA
            # by hours. A raw-RA test can never be satisfied, which stalled a live
            # sweep at Dec 89.8 that the mount had in fact reached.
            if rd and ang_sep(rd[0] * 15.0, rd[1], ra * 15.0, dec) < ARRIVE_DEG:
                time.sleep(settle)
                return
            # Extend while the mount is demonstrably still slewing. A long swing
            # in RA can outlast any fixed timeout, and giving up mid-slew is the
            # dangerous case: the scope keeps moving after we stop watching.
            if self._moving():
                deadline = max(deadline, time.time() + GOTO_TIMEOUT)
        # Never fall through silently. A goto that quietly fails to arrive voids
        # every Sun-safety guarantee: the caller believes the scope is where it
        # asked, and plans the next path from a position the mount never reached.
        # Seen for real with the arm closed — point reported a successful landing
        # while the mount had not moved at all.
        rd = self.sc.equ_coord()
        where = f"RA {rd[0]:.3f} Dec {rd[1]:.2f}" if rd else "unreadable"
        raise PointingError(
            f"goto did not arrive within {GOTO_TIMEOUT}s "
            f"(wanted RA {ra:.3f} Dec {dec:.2f}, at {where}); "
            "mount may be closed, parked, or not tracking"
        )

    def avoid_pole(self, az, alt):
        """Nudge a target in azimuth if it lands on a celestial pole.

        Due north at altitude equal to the site latitude IS the pole, so a
        horizon sweep of azimuth 0 walks straight onto it. Pointing there is
        not merely inaccurate: RA becomes undefined, the mount makes enormous
        RA swings to reach nearby targets, and a live sweep stalled there twice.

        Azimuth is nudged rather than altitude because altitude is the quantity
        being measured — moving it would corrupt the horizon reading, whereas a
        degree or two of azimuth is well inside the mask's own resolution. The
        actual azimuth used is returned so callers record what was measured.
        """
        for delta in (0.0, 1.5, -1.5, 3.0, -3.0, 5.0, -5.0):
            cand_az = (az + delta) % 360.0
            _, dec = self.sky.altaz_to_radec(cand_az, alt)
            if abs(dec) <= MAX_TARGET_DEC:
                return cand_az, alt
        # Every nudge still lands on the pole: shift altitude as a last resort
        # so the column yields something rather than aborting the whole sweep.
        return (az + 1.5) % 360.0, alt + 1.5

    def point_to(self, az, alt):
        """Slew to (az, alt) along a Sun-safe path. Returns the final az/alt."""
        self._sun_check(az, alt)
        if self.dry:
            return az, alt
        cur = self.sc.equ_coord()
        if cur is None:
            raise SunGuard("cannot read current pointing; refusing to slew")
        self._sun_check(*self.sky.radec_to_altaz(*cur))
        az, alt = self.avoid_pole(az, alt)
        target = self.sky.altaz_to_radec(az, alt)

        if self.path_min_sep(cur, target) >= self.cone:
            self._goto_wait(*target, SETTLE)  # whole path is clear
        else:
            # Route over the top: the Sun is never at high altitude from a
            # mid-latitude site, so a high waypoint clears it when a direct
            # slew would not.
            #
            # Candidates are tried in order and every one is rejected if it
            # lands near a celestial pole. RA is a singularity there, so the
            # mount cannot converge: a waypoint at Dec 89.9 stalled at Dec 80.6
            # and aborted a live sweep. Staying under MAX_VIA_DEC keeps the
            # waypoint somewhere the mount can actually reach.
            mid_az = (az + wrap180(self.current_azalt()[0] - az) / 2) % 360.0
            via = None
            for via_alt in (min(85.0, max(alt, 70.0)), 75.0, 65.0, 55.0):
                for via_az in (mid_az, (mid_az + 30.0) % 360.0, (mid_az - 30.0) % 360.0):
                    cand = self.sky.altaz_to_radec(via_az, via_alt)
                    if abs(cand[1]) > MAX_VIA_DEC:
                        continue
                    if (
                        self.path_min_sep(cur, cand) >= self.cone
                        and self.path_min_sep(cand, target) >= self.cone
                    ):
                        via = cand
                        break
                if via:
                    break
            if via:
                self._goto_wait(*via, 0.3)
                # Re-verify before the second leg. The first one can now run for
                # minutes — GOTO_TIMEOUT is 90s and extends while the mount still
                # reports motion — so the clearance computed above is stale by the
                # time it is used, and the Sun has moved. Recompute against where
                # the mount ACTUALLY landed rather than where it was asked to go,
                # and re-derive the target: az/alt is fixed, but the RA/Dec that
                # holds it drifts about a degree every four minutes.
                landed = self.sc.equ_coord()
                if landed is None:
                    raise SunGuard("cannot read pointing after the waypoint; refusing to slew")
                self._sun_check(*self.sky.radec_to_altaz(*landed))
                target = self.sky.altaz_to_radec(az, alt)
                sep = self.path_min_sep(landed, target)
                if sep < self.cone:
                    raise SunGuard(
                        f"waypoint reached but the Sun has moved: second leg to "
                        f"({az:.0f},{alt:.0f}) now clears by only {sep:.1f} deg"
                    )
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
    if salt < SUN_SAFE_ALT:
        return False
    return (
        ang_sep(az, max(alt_min, 0.0), saz, salt) < cone or ang_sep(az, alt_max, saz, salt) < cone
    )


def reachable_now(ptr, alt, cone=None):
    """A predicate for `plan.next_column`: can the mount be sent to this azimuth?

    Checks the whole SLEW PATH from where the mount currently is, not the
    endpoint. That distinction is the entire point. Measured here with the Sun
    at az 123.0 alt 55.8, planning a column at az 161.7: the endpoint sits 59.4
    degrees away, comfortably outside a 30 degree cone, while the path passes
    within 7.5. Ranking on endpoint separation would have chosen it.

    The planner cannot decide this for itself — it is geometry plus a clock —
    so it is injected. Note the answer expires: it is true of this moment and
    this mount position, so build the predicate fresh each time you plan.
    """
    cone = ptr.cone if cone is None else cone
    rd0 = ptr.sc.equ_coord()
    if rd0 is None:
        return lambda az: False  # cannot read pointing, so cannot promise a path

    def ok(az):
        try:
            target = ptr.sky.altaz_to_radec(*ptr.avoid_pole(az, alt))
        except Exception:
            return False
        return ptr.path_min_sep(rd0, target) >= cone

    return ok


def hours_until_endpoint_clear(sky, az, alt, cone, within=8.0, step_min=15.0):
    """When does this column's ENDPOINT come out from behind the Sun?

    Returns hours, or None if not within `within`. Named for what it measures
    rather than for what a caller wants, because the two differ: "reachable"
    would promise a slew will be allowed, and this cannot promise that.

    A refused column is not permanently lost — the Sun moves about 15 degrees an
    hour — and under an observing deadline the right move is sometimes to wait
    rather than substitute a worse column. The planner cannot weigh that without
    knowing the wait, so this reports it.

    Deliberately an ENDPOINT test, unlike `reachable_now`, and only ever used to
    report a wait: the path depends on where the mount will be at the time,
    which is unknowable now. Treat the answer as "not before this", never as a
    promise that the slew will be allowed.
    """
    t = 0.0
    while t <= within * 60.0:
        when = _now() + datetime.timedelta(minutes=t)
        s = get_sun(when).transform_to(AltAz(obstime=when, location=sky.loc))
        saz, salt = float(s.az.deg), float(s.alt.deg)
        if salt < SUN_SAFE_ALT or ang_sep(az, alt, saz, salt) >= cone:
            return t / 60.0
        t += step_min
    return None


EDGE_MIN_STEP_FRAC = 0.25  # a step must also be this fraction of the sky reference
EDGE_TOP_FRACTION = 0.9  # a higher split wins if within this fraction of the best
MAX_POINTING_MISSES = 3  # consecutive non-arrivals before a sweep is abandoned
EDGE_SNR = 2.5  # a step must exceed the column's own sample-to-sample noise by
#                 this factor; below it, the "step" is indistinguishable from
#                 measurement scatter and the column is reported as unmeasured


def find_edge(profile, sky_ref=None):
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
    # Scatter from SECOND differences, which cancel any linear trend. Under light
    # pollution the open sky is not flat with altitude — skyglow brightens toward
    # the horizon, measured here as 36.9 at alt 60 rising to 73.7 at alt 11. A
    # first-difference estimate counts that gradient as noise and divides a real
    # step by the trend it sits on: four of five bright columns were rejected
    # that way, with true scatter under 1 count reported as 6-11.
    if len(lums) >= 3:
        sec = [lums[i + 2] - 2 * lums[i + 1] + lums[i] for i in range(len(lums) - 2)]
        # RMS, not median: the median is robust to large excursions, but in a
        # scatter-only profile those excursions ARE the noise, and discounting
        # them lets pure scatter pass as an edge.
        noise = math.sqrt(sum(v * v for v in sec) / len(sec)) / math.sqrt(6.0)
    else:
        noise = sum(abs(lums[i + 1] - lums[i]) for i in range(len(lums) - 1)) / (len(lums) - 1)
    noise = max(noise, 1e-6)
    steps = []
    for k in range(1, len(lums)):
        above = sum(lums[:k]) / k
        below = sum(lums[k:]) / (len(lums) - k)
        steps.append((above - below, k))
    best_step = max(s for s, _ in steps)
    # Take the HIGHEST competitive split, not the largest one. A pale structure
    # in twilight is not a silhouette: a lit roof sits partway between sky and
    # ground, so the column has three levels and two plausible change points —
    # sky/roof and roof/ground. Maximising the step picks between them almost at
    # random (measured on a real column: 98.2 against 99.4, a 1% margin) and the
    # lower one is the roof meeting the ground, not the sky meeting the horizon.
    # Everything below the true horizon is already terrain, so the topmost
    # significant transition is the one being looked for.
    candidates = [(s, k) for s, k in steps if s >= EDGE_TOP_FRACTION * best_step]
    step, k = min(candidates, key=lambda sk: sk[1]) if candidates else (best_step, None)
    snr = step / noise if noise > 0 else 0.0
    # Two gates, because each is blind to what the other catches. SNR is
    # scale-free and cannot tell whether there were enough photons at all; an
    # absolute floor cannot tell a clean step from a noisy one. Without the
    # floor, a trend-insensitive noise estimate makes a 19 percent wobble in the
    # skyglow gradient pass as a horizon.
    big_enough = sky_ref is None or step >= EDGE_MIN_STEP_FRAC * sky_ref
    ok = k is not None and snr >= EDGE_SNR and big_enough
    return (k - 1, step, snr) if ok else (None, step, snr)


def edge_candidates(profile):
    """All change points, best first, as (step, index_above_edge, altitude).

    Exposed so a caller can see whether a column had a genuine choice to make.
    """
    lums = [lum for _, lum in profile]
    out = []
    for k in range(1, len(lums)):
        above = sum(lums[:k]) / k
        below = sum(lums[k:]) / (len(lums) - k)
        out.append((above - below, k - 1, profile[k - 1][0]))
    return sorted(out, reverse=True)


def edge_is_ambiguous(profile, score_tol=0.05, alt_gap=5.0):
    """True when a column has two comparable change points far apart.

    A pale structure in twilight puts the column at three levels — sky, lit
    roof, dark ground — giving two plausible boundaries whose scores can sit
    within a percent of each other while their altitudes differ by tens of
    degrees. Whichever the detector picks is then close to a coin toss.

    The column says this about itself, with no external reference: two strong
    candidates separated in altitude means the answer is not determined by the
    data at hand. Such a column should be re-measured at finer altitude spacing
    rather than trusted, and its verdict flagged until it is.
    """
    cands = edge_candidates(profile)
    if len(cands) < 2:
        return False, None
    best_s, _, best_alt = cands[0]
    for s, _, alt in cands[1:]:
        if s >= (1.0 - score_tol) * best_s and abs(alt - best_alt) >= alt_gap:
            return True, (best_alt, alt, s / best_s if best_s else 0.0)
    return False, None


def save_scan_frame(rgb, az, alt, lum, save_dir, sky_ref=None):
    """Persist one scan sample. Keeping the whole scan, not just the chosen
    boundary, is what makes a run re-analysable: the classifier can be re-tuned
    against real frames instead of guessed thresholds.

    Scaling is FIXED against the run's open-sky reference, never a per-frame
    percentile stretch. A stretch normalises every frame to full range, so a
    uniformly dark terrain frame comes back as amplified noise and looks much
    like open sky — which destroys both visual review and any attempt to recover
    brightness from disk. With a common scale, dark frames stay dark and frames
    are comparable to each other.
    """
    import os

    from PIL import Image

    os.makedirs(save_dir, exist_ok=True)
    scale = (1.3 * sky_ref) if sky_ref else 255.0
    im = Image.fromarray(np.clip(rgb / max(scale, 1e-6) * 255, 0, 255).astype(np.uint8))
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
    sun_alt=None,
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
            save_scan_frame(best_rgb, az, alt, best_lum, frames_dir, sky_ref)
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
            return alt_max, "blocked_above", obstruction_type(v, st, sun_alt), profile
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
    return round(hi, 1), f"edge(rel {rel:.2f})", obstruction_type(v, st, sun_alt), profile


def save_boundary_frame(sc, ptr, az, alt, typ, save_dir, sky_ref=None):
    from PIL import Image, ImageDraw

    os.makedirs(save_dir, exist_ok=True)
    ptr.point_to(az, alt)
    rgb = sc.capture_rgb()
    scale = (1.3 * sky_ref) if sky_ref else 255.0
    im = Image.fromarray(np.clip(rgb / max(scale, 1e-6) * 255, 0, 255).astype(np.uint8))
    ImageDraw.Draw(im).text((12, 12), f"az {az}  alt {alt}  {typ}", fill=(255, 0, 0))
    path = f"{save_dir}/az{int(az):03d}.png"
    im.save(path)
    return path


def run_sweep(
    sc,
    sky,
    cfg,
    az_start=0,
    az_end=350,
    save_dir=None,
    dry=False,
    log=print,
    azimuths=None,
    should_stop=None,
):
    """Sweep azimuths, returning ({az: {...}}, [skipped_az], {az: profile}).

    Each column is a record, not a pair. It was `(alt, type)`, and the shape was
    losing the one thing the fit most needs to know: whether the column is a
    BOUND. `scan_horizon` returns status 'blocked_above' when it searched from
    the ceiling down and never found an edge — the horizon is AT LEAST alt_max —
    and `orient.fit` scores that one-sidedly. That status was logged and thrown
    away, so the mask could not distinguish it from an exact measurement at the
    ceiling, and `orient.from_mask` could not reconstruct it. See terminus-51.

    `export._column` still accepts the old pair, so a caller or a test that hands
    one over keeps working.

    `azimuths` scans an explicit list instead of the uniform az_start/az_end
    grid. The planner produces a list of interesting columns rather than a
    range, and re-measuring the handful that came back unresolved should not
    cost a whole circle.

    Each column's raw brightness profile is returned alongside the verdict, so a
    run can be re-judged later without re-observing the sky.

    `should_stop()` is checked BEFORE each column and ends the sweep cleanly. It
    exists because terminus-17 happened: the observing deadline was enforced by
    the caller before launch, a run overran it by fourteen minutes, and the
    reason it overran is that the sky brightened toward dawn, more columns
    resolved, and the run slowed down exactly as the deadline approached. A
    caller that starts an N-column run cannot know how long N columns will take,
    and the estimate degrades in the direction that matters.
    """
    ptr = Pointer(sc, sky, cfg["sun_cone_deg"], cfg["slew_step_deg"], dry)
    saz, salt = sky.sun()
    log(f"Sun az {saz:.0f} alt {salt:.0f}", flush=True)
    mask, skipped, profiles = {}, [], {}
    # Seed the open-sky brightness from a near-zenith frame, opposite the Sun.
    # Without it the first column has nothing to compare against, and a fully
    # blocked column is indistinguishable from a clear one.
    sky_ref = None
    misses = 0  # consecutive pointing failures; see MAX_POINTING_MISSES
    if not dry:
        try:
            zen_az = (saz + 180.0) % 360.0
            ptr.point_to(zen_az, 75.0)
            sky_ref = sky_reference(sc.capture_rgb(warmup=0.3))
            log(f"sky reference (az {zen_az:.0f} alt 75): {sky_ref:.1f}", flush=True)
        except (SunGuard, PointingError, OSError) as e:
            # PointingError belongs here for the same reason it does on the
            # mid-sweep refresh: seeding is a convenience, not a measurement, and
            # a mount that cannot reach the zenith will fail the columns too,
            # where the miss counter judges it properly. Letting it escape from
            # HERE is the worst case — it is the earliest goto in the sweep, so a
            # dead mount raises before a single column exists, and that exception
            # carries no partial result for the caller to save.
            log(f"could not seed sky reference: {e}", flush=True)
    ref_taken = time.time()
    if azimuths is None:
        columns = list(range(int(az_start), int(az_end) + 1, cfg["az_step"]))
    else:
        columns = sorted({int(round(a)) % 360 for a in azimuths})
    for az in columns:
        if column_touches_sun(sky, az, cfg["alt_min"], cfg["alt_max"], cfg["sun_cone_deg"]):
            skipped.append(az)
            log(f"az {az:3d}: skipped (Sun cone)", flush=True)
            continue
        if should_stop is not None and should_stop():
            log("stopping: the observing window has closed", flush=True)
            break
        # Re-seed the reference as the sky changes. A full sweep spans hours, and
        # across twilight the sky itself changes by orders of magnitude while a
        # once-measured reference stays pinned at its daylight value. Everything
        # is then judged against a sky that no longer exists, and every remaining
        # column reports "blocked" — a whole hemisphere lost to a stale number.
        if sky_ref is not None and time.time() - ref_taken > SKY_REF_MAX_AGE:
            try:
                saz_now, _ = sky.sun()
                ptr.point_to((saz_now + 180.0) % 360.0, 75.0)
                new_ref = sky_reference(sc.capture_rgb(warmup=0.3))
                log(f"sky reference refreshed: {sky_ref:.1f} -> {new_ref:.1f}", flush=True)
                sky_ref, ref_taken = new_ref, time.time()
            except (SunGuard, PointingError, OSError) as e:
                log(f"sky reference refresh failed ({e}); keeping {sky_ref:.1f}", flush=True)
                ref_taken = time.time()
        # The Sun is re-read per column rather than reused from the seed: a full
        # sweep spans hours and can start in daylight and end after sunset, so
        # one altitude taken at the top would let the late columns inherit a
        # daylight claim they never earned.
        #
        # Guarded, and OUTSIDE the try below, for two reasons. That try catches
        # only SunGuard and PointingError, so an OSError from here would escape
        # cmd_sweep's abort handler and take the whole in-memory mask with it —
        # a column that had already cleared the Sun-cone gate killing a sweep
        # that was otherwise fine. And the fallback is the LAST KNOWN altitude,
        # not None: None means "the caller did not say" and would restore the
        # daylight assumption, which is the wrong way to fail at night. The Sun
        # moves about 15 deg an hour, so yesterday's value is stale, but the
        # value from the previous column is not.
        try:
            salt = sky.sun()[1]
        except (OSError, ValueError) as e:
            log(f"az {az:3d}: could not re-read the Sun ({e}); keeping alt {salt:.1f}", flush=True)
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
                sun_alt=salt,
            )
            if profile:
                profiles[az] = profile
                peak = max(lum for _, lum in profile)
                sky_ref = peak if sky_ref is None else max(sky_ref, peak)
            frame = (
                save_boundary_frame(sc, ptr, az, alt, typ, save_dir, sky_ref)
                if (save_dir and not dry)
                else "-"
            )
            mask[az] = {"alt": alt, "type": typ, "bound": status == "blocked_above"}
            log(f"az {az:3d}: alt {alt:5.1f}  {typ:9s} [{status}]  {frame}", flush=True)
        except SunGuard as e:
            skipped.append(az)
            log(f"az {az:3d}: skipped ({e})", flush=True)
        except PointingError as e:
            # One column that would not arrive is not worth losing a night over,
            # but a mount that cannot point is: skipping every column silently
            # would produce an empty mask and hide exactly the failure this
            # exception was added to surface (a goto once reported success while
            # the arm was closed and nothing moved). So skip, and give up if they
            # keep coming.
            skipped.append(az)
            misses += 1
            log(f"az {az:3d}: skipped ({e}) [{misses}/{MAX_POINTING_MISSES}]", flush=True)
            if misses >= MAX_POINTING_MISSES:
                # Abandon the sweep, but do NOT discard what it measured. The
                # default az_step is 5 degrees, so three consecutive misses span
                # only a 15 degree arc — narrow enough to be a local stall near
                # the pole rather than a dead mount. Losing thirty good columns
                # to that would be a worse bug than the one being guarded
                # against. The partial result rides on the exception so the
                # caller can save it and still fail loudly.
                err = PointingError(
                    f"{misses} consecutive pointing failures ending at az {az}; "
                    f"the mount is not tracking commands"
                )
                err.partial = (mask, skipped, profiles)
                raise err from e
        else:
            misses = 0

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
            if should_stop is not None and should_stop():
                log("stopping refinement: the observing window has closed", flush=True)
                break
            work = False
            known = sorted(mask)
            for a0, a1 in zip(known, known[1:], strict=False):
                gap = a1 - a0
                if gap > 2 * cfg["az_step"]:
                    continue  # a Sun-skipped hole, not a measured neighbour
                if gap / 2 < refine_to or abs(mask[a1]["alt"] - mask[a0]["alt"]) < trigger:
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
                    mask[mid] = {"alt": alt, "type": typ, "bound": status == "blocked_above"}
                    if profile:
                        profiles[mid] = profile
                    if save_dir:
                        save_boundary_frame(sc, ptr, mid, alt, typ, save_dir, sky_ref)
                    budget -= 1
                    work = True
                    log(
                        f"az {mid:3d}: alt {alt:5.1f}  {typ:9s} [refine {a0}-{a1}]",
                        flush=True,
                    )
                except (SunGuard, PointingError) as e:
                    log(f"az {mid:3d}: refine skipped ({e})", flush=True)
    return mask, skipped, profiles


# ---- targeted measurement -------------------------------------------------
def plan_targeted(prior_alt, sigma=3.0, span=2.5, step=None, repeats=3):
    """Altitudes to sample when the horizon is already roughly known.

    A blind scan spends every pointing locating a boundary to within its own
    step size and returns a single number with no error bar. Given a prior — a
    photo-derived horizon, or a previous sweep — the same budget can be spent
    *at* the boundary instead, sampling it repeatedly.

    That buys two things a blind scan cannot give. Repeats at one altitude turn
    a moving branch from an unmeasurable bias into measurable variance, so wind
    shows up as spread rather than as a wrong answer. And sampling across the
    boundary rather than down to it yields the transition's width, which is the
    honest uncertainty of a canopy edge that genuinely is not a line.

    Returns [(altitude, repeats), ...] from high to low.
    """
    step = step or max(0.5, sigma * span / 5.0)
    lo = prior_alt - sigma * span
    hi = prior_alt + sigma * span
    n = max(3, int(round((hi - lo) / step)) + 1)
    alts = [hi - i * (hi - lo) / (n - 1) for i in range(n)]
    return [(round(a, 2), repeats) for a in alts]


def fit_transition(samples, sky_ref, frac=SKY_LUM_FRACTION):
    """Estimate the horizon and its uncertainty from repeated samples.

    `samples` is [(altitude, [lum, lum, ...]), ...]. Each altitude is scored by
    the fraction of its repeats that read as sky, giving a soft profile rather
    than a hard one; the horizon is where that fraction crosses one half, found
    by linear interpolation between the bracketing altitudes.

    Returns (altitude, sigma, detail). `sigma` is the width of the transition —
    the altitude span over which the sky fraction goes from mostly-sky to
    mostly-terrain. A wall gives a narrow transition; a windblown canopy gives a
    wide one, and that width is the uncertainty the mask should carry.
    """
    thresh = frac * sky_ref
    rows = []
    for alt, lums in sorted(samples, key=lambda s: -s[0]):
        if not lums:
            continue
        rows.append((alt, sum(1 for v in lums if v >= thresh) / len(lums), len(lums)))
    if len(rows) < 2:
        return None, None, {"reason": "not enough altitudes"}
    if rows[0][1] < 0.5:
        return None, None, {"reason": "no sky at the top of the sampled range"}
    if rows[-1][1] >= 0.5:
        return None, None, {"reason": "still sky at the bottom of the sampled range"}
    cross = None
    for i in range(len(rows) - 1):
        (a0, f0, _), (a1, f1, _) = rows[i], rows[i + 1]
        if f0 >= 0.5 > f1:
            t = (f0 - 0.5) / (f0 - f1) if f0 != f1 else 0.5
            cross = a0 + t * (a1 - a0)
            break
    if cross is None:
        return None, None, {"reason": "no crossing"}
    upper = [a for a, f, _ in rows if f >= 0.84]
    lower = [a for a, f, _ in rows if f <= 0.16]
    width = (min(upper) - max(lower)) if (upper and lower) else None
    sigma = abs(width) / 2.0 if width is not None else None
    return (
        round(cross, 2),
        round(sigma, 2) if sigma is not None else None,
        {"profile": rows, "n_alts": len(rows)},
    )


# ---- per-boundary models --------------------------------------------------
# A roofline and a tree crown are not the same kind of edge, so they should not
# be measured or judged by the same rule. Which one a column holds currently
# comes from the photo segmentation, because at 250mm focused at infinity every
# terrestrial target is far inside the hyperfocal distance, so the scope sees a
# blur and can report brightness but not identity.
#
# That is a limit of the focus position, not of the instrument. The scope can
# autofocus in scenery mode and resolve terrestrial detail, which would make
# type measurable from the telescope too and the two sources cross-checkable
# rather than one substituting for the other. See terminus-32; nothing below
# depends on it yet.
BOUNDARY_MODELS = {
    # A hard edge really is a step. Demand a clean one, expect it to be narrow,
    # and treat a wide transition as evidence something is wrong — most likely a
    # pale surface in twilight sitting partway between sky and ground.
    "structure": {
        "edge_snr": 2.5,
        "run": 6,
        "expect_width_deg": 1.0,
        "max_width_deg": 4.0,
        "repeats": 2,
        "buffer_deg": 1.0,
        "seasonal": False,
    },
    # Foliage is partially transmissive and moves. A wide transition is the
    # correct answer, not a failure, so accept a weaker step but insist on more
    # repeats to separate wind from structure.
    "tree": {
        "edge_snr": 2.0,
        "run": 4,
        "expect_width_deg": 4.0,
        "max_width_deg": 12.0,
        "repeats": 4,
        "buffer_deg": 3.0,
        "seasonal": True,
    },
}
DEFAULT_MODEL = "structure"


def boundary_model(kind):
    """Measurement and acceptance parameters for a boundary of this kind.

    Refuses an EMPTY kind rather than defaulting it. An empty type means the
    column was measured but not named — see `obstruction_type` — and quietly
    handing it the structure model would be the same bug this module's own
    comment above describes, one layer down: a tree column measured at night
    would be judged by the rule for a wall, which is exactly the case that
    matters most. Nothing calls this yet, so raising costs nothing now and turns
    a silent default into a decision at the moment someone wires it up.

    An unrecognised but non-empty kind still falls back, because that is a
    spelling mistake rather than an absence of evidence.
    """
    if not (kind or "").strip():
        raise ValueError(
            "no obstruction type for this column, so no boundary model applies. "
            "Decide explicitly: measure the type from the photo (type_source: "
            "photo), or pick a model deliberately and record why."
        )
    return BOUNDARY_MODELS.get(kind.lower(), BOUNDARY_MODELS[DEFAULT_MODEL])


def judge_width(kind, width_deg):
    """Is a measured transition width consistent with this kind of boundary?

    Returns (verdict, note). The interesting case is a structure boundary that
    comes back wide: a wall does not have a soft edge, so the width is telling
    you the column is not what it was labelled, or that the sky/terrain contrast
    has collapsed — exactly what a lit roof against a darkening sky produces.
    """
    if not (kind or "").strip():
        return "unknown", "the column has no type, so there is no rule to judge its width by"
    m = boundary_model(kind)
    if width_deg is None:
        return "unknown", "no width measured"
    if width_deg > m["max_width_deg"]:
        if kind == "structure":
            return "suspect", (
                f"a hard edge should be sharp; {width_deg:.1f} deg of transition "
                "suggests a pale surface against a dim sky, or a mislabelled column"
            )
        return "suspect", f"{width_deg:.1f} deg exceeds even foliage tolerance"
    if kind == "tree" and width_deg < 0.5:
        return "suspect", "foliage with a knife edge is more likely a mislabelled structure"
    return "ok", f"{width_deg:.1f} deg is consistent with {kind}"
