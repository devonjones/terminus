"""Pin a photo-derived horizon to the sky using telescope measurements.

The photo mosaic knows the horizon's *shape* precisely but not where north is,
nor how the camera was tilted. Both are fixed by one rigid rotation solved
against columns the telescope measured.

Three things here were learned the hard way and should not be simplified away.

**Rotate properly, do not approximate.** The obvious model, subtracting
`c0 + A cos(az) + B sin(az)`, is a first-order approximation to a rotation. At
the tilts users are told to produce — Mode B's instruction is to tilt up so the
horizon stays in frame — it distorts: measured against an exact rotation, a 12
degree tilt errs by 2.2 degrees at altitude 60, comparable to the entire error
budget, and worst exactly on the tall obstruction that matters most.

**A capped column is a bound, not a value.** A sweep that reaches its altitude
ceiling only knows the horizon is *at or above* it. Scoring that as an equality
throws away real agreement; scoring it two-sided is worse, because clamping also
clamps genuine measurements and manufactures a flattering residual. The rule is
one-sided, and every column carries its own ceiling so sweeps taken at different
ceilings can be mixed.

**One bad fiducial tips the whole sphere.** A rigid rotation cannot invent a
bend in the horizon, but it can tip everything, so least squares will happily
rotate the sky to accommodate a single wrong column. A too-low ceiling is known
to manufacture confident false edges, so robust fitting is not optional.
"""

import functools
import math

import numpy as np


class Fiducial:
    """One telescope-measured column.

    alt         measured altitude, or the ceiling if the column was capped
    ceiling     the altitude ceiling this column was scanned with
    bound       True if the scan hit its ceiling (alt is a lower bound)
    weight      how well this edge was DETECTED (from SNR)
    sigma       how far the thing detected may MOVE, in degrees, from its type
    photo_type  obstruction type from the panorama segmentation, or None
    scope_type  obstruction type from the telescope's own frame, or None

    `weight` and `sigma` are deliberately separate numbers rather than one
    combined weight. Sigma STANDARDISES the residual before the robust loss;
    weight scales the loss afterwards. Folding sigma into weight looks
    equivalent and is not: Huber's `delta` is a threshold on the residual, so
    multiplying the loss by 1/sigma^2 is inverse-variance weighting only while
    residuals stay inside the quadratic core. Past delta the two diverge — at a
    10 degree residual with sigma 3 and delta 4 they differ by 36% — and that is
    exactly the regime, large residuals on high-sigma vegetation columns, this
    weighting exists to handle.

    The two type sources are kept SEPARATE rather than merged into one field.
    They can disagree, and the disagreement is itself informative — averaging it
    away destroys the only signal that either is wrong. Today only `photo_type`
    is populated; `scope_type` arrives once the scope can autofocus on the edge.
    """

    __slots__ = (
        "az",
        "alt",
        "ceiling",
        "bound",
        "weight",
        "sigma",
        "photo_type",
        "scope_type",
    )

    def __init__(
        self,
        az,
        alt,
        ceiling=None,
        bound=False,
        weight=1.0,
        sigma=1.0,
        photo_type=None,
        scope_type=None,
    ):
        self.az = float(az)
        self.alt = float(alt)
        self.ceiling = float(ceiling) if ceiling is not None else None
        self.bound = bool(bound)
        self.weight = float(weight)
        self.sigma = float(sigma) if sigma else 1.0
        self.photo_type = photo_type
        self.scope_type = scope_type

    def headroom(self):
        """Gap between the result and its own ceiling.

        Small headroom means the search barely reached the horizon, and such a
        column may be an artifact: if the true horizon lies above the ceiling the
        whole scan sat inside terrain, where a step can still be fitted to
        variation within the obstruction and reported as a confident edge.
        """
        if self.ceiling is None or self.bound:
            return math.inf
        return self.ceiling - self.alt

    def __repr__(self):
        kind = ">=" if self.bound else "="
        return f"Fiducial(az={self.az:.0f}, {kind}{self.alt:.1f}, ceiling={self.ceiling})"


# How close to its ceiling a column must sit to count as censored by it. A tenth
# of a degree is well inside the sweep's own altitude step, so this catches the
# exact-equality case the writers actually produce without swallowing a genuine
# measurement that merely landed high.
CEILING_EPS = 0.1


def from_mask(mask, ceiling=None):
    """Build fiducials from a mask dict ({az: {alt, type, bound?}}).

    THE MASK MUST SAY WHICH COLUMNS ARE BOUNDS, and for a long time it did so by
    accident. This function matched `type == "edge"` and `type.startswith
    ("blocked")`, which was the mask vocabulary of 2026-08-02 — `blocked>35`
    literally encoded "the horizon is above the 35 degree ceiling". When the
    vocabulary became tree/structure/open, that encoding went with it, and this
    function silently returned ZERO fiducials for every mask written since.
    Nothing failed: an empty list is a legal result, and the only caller was the
    worked example in the package docstring, so the documented way in returned
    nothing and said nothing.

    A bound is now an explicit `bound: true` on the column, which is what it
    should always have been — boundedness is a property of the measurement, not
    a spelling of its type. Masks in the old vocabulary are still read, because
    they exist on disk and their `blocked...` types are real information.

    An untyped, unbounded column is an ordinary measurement. A column with no
    `alt` at all is not a measurement and is skipped.
    """
    out = []
    for az, entry in mask.items():
        if entry.get("alt") is None:
            continue
        typ = str(entry.get("type", "") or "")
        alt = float(entry["alt"])
        ceil = float(entry.get("ceiling", ceiling)) if (entry.get("ceiling") or ceiling) else None
        if typ == "unknown":
            # A failed measurement carries no information at all: it is neither
            # an edge nor a bound, and must not enter the fit as either.
            continue
        # `clipped` is the PHOTO's version of the same statement — the
        # obstruction ran off the top of the frame, so the altitude beside it is
        # a lower bound. Two instruments, one meaning, and the fit needs to score
        # both one-sidedly. Reading a clipped column as a confirmed exact edge
        # was the same mistake this function exists to stop making.
        bound = (
            bool(entry.get("bound", False))
            or bool(entry.get("clipped", False))
            or typ.startswith("blocked")
            # A COLUMN SITTING ON ITS CEILING IS A BOUND, whatever it is typed.
            # The scope cannot tilt past its search ceiling, so "60.0" out of a
            # 0-60 sweep does not mean the horizon is at 60 — it means the search
            # ran out of sky (M-09). Masks written before the explicit `bound`
            # field existed record these as ordinary edges, and the 2026-08-03
            # evening sweeps still on disk have four of them: az 320, 330, 340
            # and 350, all at exactly 60.0. Read as exact edges they claim a
            # measurement nobody made, and the fit then scores them two-sided,
            # so a photo horizon ABOVE 60 is penalised for being too high.
            or (ceil is not None and alt >= ceil - CEILING_EPS)
        )
        out.append(Fiducial(az, alt, ceil, bound=bound))
    return out


def _wrap180(x):
    return ((x + 180.0) % 360.0) - 180.0


@functools.lru_cache(maxsize=4096)
def _tilt_matrix(tilt_mag, tilt_dir):
    """Rodrigues rotation about a horizontal axis, as nine plain floats.

    Cached and returned unpacked because the fit evaluates this on the order of
    a million times: rebuilding a numpy 3x3 per call dominated the runtime by a
    wide margin (a full solve took 573s that way).
    """
    d = math.radians(tilt_dir)
    x, y, z = -math.sin(d), math.cos(d), 0.0
    th = math.radians(tilt_mag)
    c, s = math.cos(th), math.sin(th)
    k = 1.0 - c
    return (
        c + x * x * k, x * y * k - z * s, x * z * k + y * s,
        y * x * k + z * s, c + y * y * k, y * z * k - x * s,
        z * x * k - y * s, z * y * k + x * s, c + z * z * k,
    )  # fmt: skip


def _rotate_scalar(az, alt, tilt_mag, tilt_dir):
    """Scalar (az, alt) rotation in plain Python — the fit's inner loop."""
    m = _tilt_matrix(tilt_mag, tilt_dir)
    a, e = math.radians(az), math.radians(alt)
    ce = math.cos(e)
    vx, vy, vz = ce * math.cos(a), ce * math.sin(a), math.sin(e)
    wx = m[0] * vx + m[1] * vy + m[2] * vz
    wy = m[3] * vx + m[4] * vy + m[5] * vz
    wz = m[6] * vx + m[7] * vy + m[8] * vz
    return (
        math.degrees(math.atan2(wy, wx)) % 360.0,
        math.degrees(math.asin(max(-1.0, min(1.0, wz)))),
    )


def rotate(az_deg, alt_deg, pitch, tilt_mag, tilt_dir):
    """(azimuth, altitude) after rotating the sphere. Exact, no small angles.

    Returns BOTH coordinates because a tilt moves a point in azimuth as well as
    altitude — only a yaw is a pure relabelling of azimuth. Callers that ignore
    the returned azimuth are assuming the small-angle limit.
    """
    az = np.radians(np.asarray(az_deg, dtype=float))
    alt = np.radians(np.asarray(alt_deg, dtype=float))
    v = np.stack([np.cos(alt) * np.cos(az), np.cos(alt) * np.sin(az), np.sin(alt)], axis=-1)
    d = math.radians(tilt_dir)
    axis = np.array([-math.sin(d), math.cos(d), 0.0])
    th = math.radians(tilt_mag)
    c, s = math.cos(th), math.sin(th)
    x, y, z = axis
    R = np.array(
        [
            [c + x * x * (1 - c), x * y * (1 - c) - z * s, x * z * (1 - c) + y * s],
            [y * x * (1 - c) + z * s, c + y * y * (1 - c), y * z * (1 - c) - x * s],
            [z * x * (1 - c) - y * s, z * y * (1 - c) + x * s, c + z * z * (1 - c)],
        ]
    )
    w = v @ R.T
    return (
        np.degrees(np.arctan2(w[..., 1], w[..., 0])) % 360.0,
        np.degrees(np.arcsin(np.clip(w[..., 2], -1.0, 1.0))) - pitch,
    )


def rotate_alt(az_deg, alt_deg, pitch, tilt_mag, tilt_dir):
    """Altitude after rotating the sphere. Exact, no small-angle assumption."""
    return rotate(az_deg, alt_deg, pitch, tilt_mag, tilt_dir)[1]


def rotate_inverse(az_deg, alt_deg, pitch, tilt_mag, tilt_dir):
    """The exact inverse of `rotate`: TRUE (az, alt) back to the photo's own.

    Needed to draw the photograph rather than merely measure it. `rotate` carries
    a photo direction to where it lands in the world, which answers "what is the
    altitude at this fiducial"; rendering asks the opposite question — for this
    pixel of sky, which pixel of the panorama shows it — and forward-mapping
    instead leaves scatter holes that read as missing data when they are only
    missing samples.

    The rotation is rigid, so the inverse is the transpose and the pitch is
    undone first, in the opposite order to `rotate` applying it last.
    """
    az = np.radians(np.asarray(az_deg, dtype=float))
    alt = np.radians(np.asarray(alt_deg, dtype=float) + pitch)
    w = np.stack([np.cos(alt) * np.cos(az), np.cos(alt) * np.sin(az), np.sin(alt)], axis=-1)
    d = math.radians(tilt_dir)
    x, y, z = -math.sin(d), math.cos(d), 0.0
    th = math.radians(tilt_mag)
    c, s = math.cos(th), math.sin(th)
    R = np.array(
        [
            [c + x * x * (1 - c), x * y * (1 - c) - z * s, x * z * (1 - c) + y * s],
            [y * x * (1 - c) + z * s, c + y * y * (1 - c), y * z * (1 - c) - x * s],
            [z * x * (1 - c) - y * s, z * y * (1 - c) + x * s, c + z * z * (1 - c)],
        ]
    )
    v = w @ R  # w @ R is R.T @ w, the transpose of what `rotate` applies
    return (
        np.degrees(np.arctan2(v[..., 1], v[..., 0])) % 360.0,
        np.degrees(np.arcsin(np.clip(v[..., 2], -1.0, 1.0))),
    )


def native_column(sample, target_az, yaw, tilt_mag, tilt_dir, tol=1e-3, iters=8):
    """The photo column that lands at world azimuth `target_az`. Returns (phi, raw).

    Yaw is a pure rotation about the vertical, so it only relabels azimuth and
    `phi = target_az - yaw` is exact. Tilt is not: rotating about a horizontal
    axis moves a point in azimuth too, so the column that ENDS at target_az is
    not the one that STARTED at target_az - yaw. Reading the photo at the
    starting column and then reporting its altitude as if it belonged to the
    target azimuth silently reintroduces the small-angle approximation that
    `rotate` exists to avoid, and the error grows with tilt: on a synthetic
    horizon of modest slope it reached 0.56 deg RMS at 12 deg of tilt, and on a
    steep one far more.

    This is a fixed point, and d(az)/d(phi) is near 1, so it normally converges
    in two or three passes and exits early when it does — `iters` only bounds the
    hard cases, so raising it is nearly free. It was 6, which left almost no
    margin: worst landing error across this site's tilt range ran 0.013 deg at 6
    and 0.038 at 5, so a single-step regression would have degraded the fit
    without any test noticing. At 8 it is 0.002. It does NOT converge at a vertical discontinuity —
    a house corner, where the photo altitude jumps — because the azimuth shift
    depends on altitude, so a step across the cliff throws the iterate to the
    far side and it oscillates. That case is genuinely ill-posed: rotating a
    discontinuous curve leaves world azimuths that no photo column maps to. So
    the best iterate seen is kept and returned rather than whichever side the
    loop happened to stop on, which bounds the error by the width of the cliff
    instead of letting it land arbitrarily. Measured on a synthetic 22 degree
    step, this is exact at 3 degrees of tilt and degrades only past that.
    """
    phi = (target_az - yaw) % 360.0
    raw = sample(phi)
    if not tilt_mag or raw is None or not math.isfinite(raw):
        return phi, raw
    best = None
    for _ in range(iters):
        landed, _ = _rotate_scalar(phi + yaw, raw, tilt_mag, tilt_dir)
        err = _wrap180(target_az - landed)
        if best is None or abs(err) < best[0]:
            best = (abs(err), phi, raw)
        if abs(err) < tol:
            return phi, raw
        phi = (phi + err) % 360.0
        raw = sample(phi)
        if raw is None or not math.isfinite(raw):
            break
    return best[1], best[2]


def predict(fids, sample, yaw, tilt_mag, tilt_dir):
    """Photo altitude at each fiducial's azimuth, before pitch. NaN where unread.

    Split out from `residuals` because pitch enters as a pure offset, so the fit
    can sweep it without redoing the rotation — which is the expensive part.
    """
    out = np.empty(len(fids))
    for i, f in enumerate(fids):
        phi, raw = native_column(sample, f.az, yaw, tilt_mag, tilt_dir)
        if raw is None or not math.isfinite(raw):
            out[i] = np.nan
        else:
            out[i] = _rotate_scalar(phi + yaw, raw, tilt_mag, tilt_dir)[1]
    return out


def score(fids, photo, pitch):
    """Signed residual per fiducial given predicted altitudes and a pitch."""
    out = np.empty(len(photo))
    for i, f in enumerate(fids):
        p = photo[i] - pitch
        if not math.isfinite(p):
            out[i] = np.nan
        elif f.bound:
            # Agreement is one-sided: a photo that also exceeds the ceiling
            # confirms the bound exactly. Only falling short contradicts it.
            out[i] = 0.0 if p >= f.alt else p - f.alt
        else:
            out[i] = p - f.alt
    return out


def residuals(fids, sample, yaw, pitch, tilt_mag, tilt_dir):
    """Signed residual per fiducial. `sample(az)` returns the photo altitude."""
    return score(fids, predict(fids, sample, yaw, tilt_mag, tilt_dir), pitch)


def _huber(res, delta):
    a = np.abs(res)
    return np.where(a <= delta, 0.5 * res**2, delta * (a - 0.5 * delta))


def objective(residuals, weights, sigmas, delta=4.0, robust=True):
    """Weighted robust cost. Sigma standardises the residual BEFORE the loss.

    That order is the whole point and is easy to get wrong. `delta` is a
    threshold on the residual, so a column allowed to move 3 degrees must have
    its residual measured in units of those 3 degrees. Multiplying the finished
    loss by 1/sigma^2 instead is inverse-variance weighting only while residuals
    stay inside Huber's quadratic core; past delta the two diverge, and at a 10
    degree residual with sigma 3 and delta 4 they differ by 36 per cent —
    exactly the regime this weighting exists to handle.

    Lives at module level rather than inside `fit` so the invariant is reachable
    by a test. It was not, and reverting to the wrong form passed the suite.
    """
    z = np.asarray(residuals, dtype=float) / np.asarray(sigmas, dtype=float)
    loss = _huber(z, delta) if robust else 0.5 * z**2
    w = np.asarray(weights, dtype=float)
    return float((w * loss).sum() / w.sum())


PARAMS = 4  # yaw, pitch, and the two tilt components


def effective_constraints(fids, sample, solution):
    """How many fiducials actually constrain the fit.

    Not the same as how many were measured, and the difference is what made two
    runs look better determined than they were. A BOUND is scored one-sided: a
    photo that already exceeds its ceiling confirms it for free and contributes
    a residual of exactly zero, so it pins nothing. Counting it as a constraint
    inflates the apparent redundancy of precisely the runs most likely to be
    under-determined — the ones where columns failed to resolve and became
    bounds.
    """
    r = residuals(
        fids, sample, solution["yaw"], solution["pitch"],
        solution["tilt_mag"], solution["tilt_dir"],
    )  # fmt: skip
    n = 0
    for i, f in enumerate(fids):
        if not math.isfinite(r[i]):
            continue
        if f.bound and abs(r[i]) < 1e-9:
            continue  # satisfied for free; it is not holding anything down
        n += 1
    return n


def yaw_uncertainty(fids, sample, solution, tol=None, span=60.0, step=1.0):
    """Half-width of the yaw minimum, in degrees. None if it is not bounded.

    THE NUMBER THAT WAS MISSING. Two runs on the same photo mask reported yaw
    130.00 and 161.50, each with a small residual, and neither said anything
    about how well the yaw was actually determined. Scanning the objective shows
    why: the minimum is BROAD. On 2026-08-06 anything from 150 to 175 fitted
    almost as well as the best, so a solved yaw of 161.50 was really "somewhere
    around 160, give or take fifteen degrees" — and printed as if it were exact.

    `tol` is how much worse than the best the fit may be and still count as
    consistent. It defaults to the median sigma of the fiducials: the yaw is
    pinned only as tightly as the columns it was measured from, and claiming
    more precision than the measurements carry is the error this exists to
    prevent.

    PITCH AND TILT ARE RE-OPTIMISED AT EACH YAW, which is the whole point and
    the thing an earlier version of this got wrong. Holding them fixed makes the
    minimum look sharp — plus or minus one degree on data with no degrees of
    freedom at all — because it measures how badly yaw alone breaks the fit
    rather than how far yaw can move while the other parameters absorb it. The
    parameters trade against each other; that trade IS the uncertainty.
    """
    if tol is None:
        sig = [f.sigma for f in fids if f.sigma]
        tol = float(np.median(sig)) if sig else 1.0
    y0 = solution["yaw"]

    def best_rms_at(y):
        """Best achievable fit with the yaw pinned here."""
        best = np.inf
        for tm in np.arange(0.0, max(1e-9, solution["tilt_mag"] * 2 + 3.0), 3.0):
            for td in np.arange(0.0, 360.0, 45.0) if tm else (0.0,):
                photo = predict(fids, sample, y, float(tm), float(td))
                for p in np.arange(solution["pitch"] - 12.0, solution["pitch"] + 12.1, 1.5):
                    r = score(fids, photo, float(p))
                    ok = np.isfinite(r)
                    if ok.sum():
                        best = min(best, float(np.sqrt((r[ok] ** 2).mean())))
        return best

    # Baseline is the FIT's own rms, not this coarse scan's value at the same
    # yaw. The scan re-optimises on a deliberately cheap grid, so it can only do
    # worse than the fit — and using its own optimistic-at-the-centre value as
    # the reference made the interval look TIGHTER the coarser the scan got,
    # which is exactly backwards. Anchoring on the real best means grid coarseness
    # widens the interval, erring toward admitting uncertainty.
    limit = float(solution["rms"]) + tol
    half = 0.0
    while half < span:
        half += step
        if best_rms_at(y0 - half) > limit and best_rms_at(y0 + half) > limit:
            return half
    return None  # still consistent `span` degrees away: not bounded at all


def fit(
    fids,
    sample,
    yaw_step=0.5,
    tilt_max=15.0,
    tilt_step=1.5,
    pitch_range=12.0,
    robust=True,
    delta=4.0,
    min_headroom=None,
):
    """Solve yaw, pitch and tilt against the fiducials.

    Coarse grid then local refinement. The objective is a Huber loss by default,
    so one bad column cannot tip the whole sphere; set robust=False for plain
    least squares.

    min_headroom, if given, drops exact fiducials whose result sits closer than
    that to their own ceiling — the signature of a manufactured edge.
    """
    used = [f for f in fids if min_headroom is None or f.headroom() >= min_headroom]
    if len(used) < 4:
        raise ValueError(f"need at least 4 usable fiducials, have {len(used)}")

    weights = np.array([f.weight for f in used])
    sigmas = np.array([f.sigma for f in used])

    def cost_from(photo, p):
        r = score(used, photo, p)
        ok = np.isfinite(r)
        if ok.sum() < 4:
            return math.inf, 0
        return objective(r[ok], weights[ok], sigmas[ok], delta, robust), int(ok.sum())

    def cost(y, p, tm, td):
        return cost_from(predict(used, sample, y, tm, td), p)

    best = None
    for y in np.arange(0.0, 360.0, yaw_step):
        for tm in np.arange(0.0, tilt_max + 1e-9, tilt_step):
            dirs = [0.0] if tm == 0 else np.arange(0.0, 360.0, 30.0)
            for td in dirs:
                # The rotation is the expensive part and pitch is a pure offset,
                # so rotate once and slide pitch over the result.
                photo = predict(used, sample, y, tm, td)
                ok = np.isfinite(photo)
                if ok.sum() < 4:
                    continue
                p0 = float(np.median(score(used, photo, 0.0)[ok]))
                p0 = max(-pitch_range, min(pitch_range, p0))
                c, n = cost_from(photo, p0)
                if best is None or c < best[0]:
                    best = (c, y, p0, tm, td, n)
    _, y, p, tm, td, n = best
    for _ in range(3):  # local refinement
        improved = False
        for dy in (-yaw_step / 2, 0, yaw_step / 2):
            for dp in (-0.5, 0, 0.5):
                for dtm in (-tilt_step / 2, 0, tilt_step / 2):
                    for dtd in (-15.0, 0, 15.0):
                        cand = (y + dy, p + dp, max(0.0, tm + dtm), (td + dtd) % 360)
                        c, nn = cost(*cand)
                        if c < best[0]:
                            best = (c, *cand, nn)
                            y, p, tm, td, n = *cand, nn
                            improved = True
        yaw_step /= 2
        tilt_step /= 2
        if not improved:
            break
    r = residuals(used, sample, y, p, tm, td)
    ok = np.isfinite(r)
    return {
        "yaw": float(y % 360.0),
        "pitch": float(p),
        "tilt_mag": float(tm),
        "tilt_dir": float(td % 360.0),
        "rms": float(np.sqrt((r[ok] ** 2).mean())),
        "n": int(ok.sum()),
        "n_bound": int(sum(1 for f in used if f.bound)),
        "residuals": {used[i].az: float(r[i]) for i in range(len(used)) if ok[i]},
        "dropped": [f.az for f in fids if f not in used],
    }
