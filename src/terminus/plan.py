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
    """|dH/daz| as a function of azimuth, from a dense (az, alt) profile."""
    prof = np.asarray(profile, dtype=float)
    az, alt = prof[:, 0], prof[:, 1]
    order = np.argsort(az)
    az, alt = az[order], alt[order]
    grad = np.abs(np.gradient(alt, az))

    def at(a):
        return float(np.interp(a % 360.0, az, grad, period=360.0))

    return at


def information(azimuths, gradient_at, ridge=1e-6):
    """Fisher information for the orientation parameters, given these columns."""
    if not len(azimuths):
        return ridge * np.eye(PARAMS)
    J = np.array([jacobian_row(a, gradient_at(a)) for a in azimuths])
    return J.T @ J + ridge * np.eye(PARAMS)


def next_column(measured, candidates, gradient_at, criterion="D"):
    """The candidate azimuth that most improves the orientation estimate.

    'D' maximises the determinant of the information matrix — the standard
    D-optimal choice, which shrinks the joint uncertainty of all parameters.
    'A' minimises the total parameter variance instead, which is more willing to
    spend a measurement fixing the single worst-determined parameter.
    """
    best, best_score = None, -np.inf
    for c in candidates:
        M = information(list(measured) + [c], gradient_at)
        if criterion == "A":
            score = -float(np.trace(np.linalg.pinv(M)))
        else:
            sign, logdet = np.linalg.slogdet(M)
            score = logdet if sign > 0 else -np.inf
        if score > best_score:
            best, best_score = c, score
    return best, best_score


def rank_columns(measured, candidates, gradient_at, criterion="D", top=5):
    """Candidates ordered by how much each would help, best first."""
    scored = []
    for c in candidates:
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


def as_fiducial(az, edge, ceiling, Fiducial):
    """Turn a column measurement into a fiducial, keeping blocked columns.

    A column whose horizon lies above the search ceiling yields no edge. It must
    still be recorded — as a bound at the ceiling, not dropped. Discarding it
    loses the fact that the column was measured at all, and a later reader
    cannot tell an unmeasured azimuth from an obstructed one.

    `edge` is None when nothing was found, otherwise {'alt', 'snr'}.
    """
    if edge is None:
        return Fiducial(az, ceiling, ceiling=ceiling, bound=True, weight=1.0)
    bound = edge["alt"] >= ceiling - 1e-6
    weight = 1.0 if bound else min(1.0, (edge.get("snr") or 1.0) / 8.0)
    return Fiducial(az, edge["alt"], ceiling=ceiling, bound=bound, weight=weight)
