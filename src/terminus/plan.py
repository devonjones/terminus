"""Choose which horizon column to measure next.

A blind sweep spends hours measuring azimuths that carry almost no information.
Given a photo-derived horizon, the telescope's job is not to measure the horizon
at all — the photo already does that densely — but to pin down where that
horizon sits on the sky. That is three numbers (yaw, pitch, tilt), and a handful
of well-chosen columns determines them.

Which columns are worth measuring follows from the fit itself. Altitude depends
on the three parameters as

    d(alt)/d(yaw)   = -dH/daz        the horizon's local gradient
    d(alt)/d(pitch) = 1              the same everywhere
    d(alt)/d(tilt)  = cos(az), sin(az)

so **yaw information comes only from steep horizon**. In a flat stretch of fence
a rotation barely changes the predicted altitude, and measuring there says
almost nothing about orientation. Pitch and tilt want the opposite — spread
around the circle — and the criterion below balances the two.

A consequence worth stating plainly: an awkward horizon is an *advantage* here.
Cliffs, a tall tree, sharp rooflines all carry orientation information. A site
with an even, open horizon is the hard case and needs more columns, not fewer.
"""

import numpy as np

PARAMS = 4  # yaw, pitch, and the two tilt components


def jacobian_row(az_deg, gradient_deg_per_deg):
    """Sensitivity of predicted altitude to each fit parameter at this azimuth."""
    r = np.radians(az_deg)
    return np.array([-gradient_deg_per_deg, 1.0, np.cos(r), np.sin(r)])


def horizon_gradient(profile):
    """SIGNED dH/daz as a function of azimuth, from a dense (az, alt) profile.

    The sign matters, and taking the magnitude here was wrong: it made the
    Jacobian's yaw entry -|dH/daz| everywhere, giving a falling edge the sign of
    a rising one. Flipping one entry of the row is not harmless the way flipping
    the whole row would be — the row enters the information matrix as an outer
    product, so it corrupts the yaw/pitch and yaw/tilt cross terms and skews
    column selection wherever the horizon slopes downward.

    The only consumer is `jacobian_row`, which negates it.
    """
    prof = np.asarray(profile, dtype=float)
    az, alt = prof[:, 0], prof[:, 1]
    order = np.argsort(az)
    az, alt = az[order], alt[order]
    grad = np.gradient(alt, az)

    def at(a):
        return float(np.interp(a % 360.0, az, grad, period=360.0))

    return at


def information(azimuths, gradient_at, ridge=1e-6):
    """Fisher information for the orientation parameters, given these columns."""
    if not len(azimuths):
        return ridge * np.eye(PARAMS)
    J = np.array([jacobian_row(a, gradient_at(a)) for a in azimuths])
    return J.T @ J + ridge * np.eye(PARAMS)


def partition(candidates, reachable=None):
    """Split candidates into (feasible, refused) by an injected predicate.

    `reachable(az)` answers whether the mount may actually be sent there —
    which this module deliberately cannot decide for itself. Feasibility is Sun
    geometry, it changes with the clock, and it belongs to whatever owns the
    ephemeris; `plan` stays a pure function of information.
    """
    if reachable is None:
        return list(candidates), []
    ok, refused = [], []
    for c in candidates:
        (ok if reachable(c) else refused).append(c)
    return ok, refused


def next_column(measured, candidates, gradient_at, criterion="D", reachable=None):
    """The candidate azimuth that most improves the orientation estimate.

    'D' maximises the determinant of the information matrix — the standard
    D-optimal choice, which shrinks the joint uncertainty of all parameters.
    'A' minimises the total parameter variance instead, which is more willing to
    spend a measurement fixing the single worst-determined parameter.

    Unreachable candidates are removed BEFORE the criterion is applied, and the
    order matters. Filtering afterwards yields the best infeasible column plus a
    fallback; filtering first yields the best feasible CONFIGURATION, which is a
    different and better answer — the columns are chosen jointly, so losing one
    changes which others are worth having.

    Returns (None, -inf) when nothing is reachable. That is a real state, not an
    error: the Sun moves, and the right response is sometimes to wait rather
    than to substitute a worse column.
    """
    usable, _ = partition(candidates, reachable)
    best, best_score = None, -np.inf
    for c in usable:
        M = information(list(measured) + [c], gradient_at)
        if criterion == "A":
            score = -float(np.trace(np.linalg.pinv(M)))
        else:
            sign, logdet = np.linalg.slogdet(M)
            score = logdet if sign > 0 else -np.inf
        if score > best_score:
            best, best_score = c, score
    return best, best_score


def rank_columns(measured, candidates, gradient_at, criterion="D", top=5, reachable=None):
    """Candidates ordered by how much each would help, best first.

    Unreachable candidates are dropped before ranking, for the reason given in
    `next_column`.
    """
    usable, _ = partition(candidates, reachable)
    scored = []
    for c in usable:
        M = information(list(measured) + [c], gradient_at)
        if criterion == "A":
            s = -float(np.trace(np.linalg.pinv(M)))
        else:
            sign, logdet = np.linalg.slogdet(M)
            s = logdet if sign > 0 else -np.inf
        scored.append((s, c))
    scored.sort(reverse=True)
    return [c for _, c in scored[:top]]


def seed_columns(n, available, start=0.0):
    """Evenly spaced starting columns, snapped to what can actually be measured."""
    picks = []
    for i in range(n):
        target = (start + i * 360.0 / n) % 360.0
        pick = min(available, key=lambda a: abs(((a - target + 180) % 360) - 180))
        if pick not in picks:
            picks.append(pick)
    return sorted(picks)


def is_stable(history, window=3, yaw_tol=1.0, key="yaw"):
    """Has the fit stopped moving?

    Stability of the solution, not smallness of the residual, is the stopping
    rule. While only a few columns are in hand the residual is meaningless —
    four points fitting four parameters interpolate, and the RMS reads near zero
    however wrong the answer is. It only becomes informative once there is
    redundancy, by which point the fit has usually already settled.
    """
    vals = [h[key] for h in history if h.get(key) is not None]
    if len(vals) < window + 1:
        return False, None
    recent = vals[-(window + 1) :]
    last = recent[-1]
    spread = max(abs(((v - last + 180) % 360) - 180) for v in recent)
    return spread <= yaw_tol, spread


def residual_targets(residuals, top=3, min_abs=3.0):
    """Azimuths worth re-measuring because the model disagrees with reality.

    A different objective from `next_column`: once the orientation has settled,
    a large residual is no longer evidence about the rotation but about a bad
    column, a bad patch of photo, or a boundary that genuinely moved between the
    two observations.
    """
    bad = [(abs(v), az) for az, v in residuals.items() if abs(v) >= min_abs]
    bad.sort(reverse=True)
    return [az for _, az in bad[:top]]


def _sigma(uncertainty):
    """Degrees this column's boundary may move. 1.0 when unknown.

    Tested with `is None` rather than truthiness: 0.0 is a real value meaning
    "perfectly certain", and reading it as "unknown" would be a silent
    downgrade. It is clamped away from zero because a zero sigma is a division
    by zero dressed as infinite confidence, which nothing has earned.
    """
    if uncertainty is None:
        return 1.0
    return max(float(uncertainty), 1e-6)


def as_fiducial(az, edge, ceiling, Fiducial, uncertainty=None, photo_type=None, scope_type=None):
    """Turn a column measurement into a fiducial, keeping blocked columns.

    A column whose horizon lies above the search ceiling yields no edge. It must
    still be recorded — as a bound at the ceiling, not dropped. Discarding it
    loses the fact that the column was measured at all, and a later reader
    cannot tell an unmeasured azimuth from an obstructed one.

    `edge` is None when nothing was found, otherwise {'alt', 'snr'}.

    `uncertainty` is the column's altitude uncertainty in degrees, as
    `skymask.type_uncertainty` computes it and as the mask carries it: about 1
    degree for solid structure, 3 for vegetation, widened by the gap fraction.
    It enters the weight as 1/sigma^2 and MULTIPLIES the SNR term rather than
    replacing it, because the two measure different things. SNR says how well
    this edge was DETECTED. Type says how well the thing detected STAYS PUT
    between the photograph and the measurement — a crisp canopy edge can score
    excellently on the first and badly on the second, and a fit that only knows
    the first will trust it as much as a roofline.

    The 1 and 3 degree priors are a claim about how far foliage moves, not a
    tuning knob. If they are wrong the fix is to measure how far the foliage
    actually moved between two captures, not to adjust them until a fit looks
    better.

    `photo_type` and `scope_type` are recorded separately and neither is merged
    into the other; see `Fiducial`.
    """
    if edge is None:
        return Fiducial(
            az,
            ceiling,
            ceiling=ceiling,
            bound=True,
            weight=1.0,
            sigma=_sigma(uncertainty),
            photo_type=photo_type,
            scope_type=scope_type,
        )
    bound = edge["alt"] >= ceiling - 1e-6
    weight = 1.0 if bound else min(1.0, (edge.get("snr") or 1.0) / 8.0)
    return Fiducial(
        az,
        edge["alt"],
        ceiling=ceiling,
        bound=bound,
        weight=weight,
        sigma=_sigma(uncertainty),
        photo_type=photo_type,
        scope_type=scope_type,
    )
