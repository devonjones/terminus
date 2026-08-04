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

import math

import numpy as np


class Fiducial:
    """One telescope-measured column.

    alt      measured altitude, or the ceiling if the column was capped
    ceiling  the altitude ceiling this column was scanned with
    bound    True if the scan hit its ceiling (alt is a lower bound)
    """

    __slots__ = ("az", "alt", "ceiling", "bound", "weight")

    def __init__(self, az, alt, ceiling=None, bound=False, weight=1.0):
        self.az = float(az)
        self.alt = float(alt)
        self.ceiling = float(ceiling) if ceiling is not None else None
        self.bound = bool(bound)
        self.weight = float(weight)

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


def from_mask(mask, ceiling=None):
    """Build fiducials from a terminus mask dict ({az: {alt, type}})."""
    out = []
    for az, entry in mask.items():
        typ = entry.get("type", "")
        alt = float(entry["alt"])
        ceil = float(entry.get("ceiling", ceiling)) if (entry.get("ceiling") or ceiling) else None
        if typ == "edge":
            out.append(Fiducial(az, alt, ceil, bound=False))
        elif typ.startswith("blocked"):
            out.append(Fiducial(az, alt, ceil, bound=True))
        # 'unknown' is a failed measurement, not a bound: it carries no
        # information at all and must not enter the fit.
    return out


def rotate_alt(az_deg, alt_deg, pitch, tilt_mag, tilt_dir):
    """Altitude after rotating the sphere. Exact, no small-angle assumption."""
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
    return np.degrees(np.arcsin(np.clip(w[..., 2], -1.0, 1.0))) - pitch


def residuals(fids, sample, yaw, pitch, tilt_mag, tilt_dir):
    """Signed residual per fiducial. `sample(az)` returns the photo altitude."""
    out = []
    for f in fids:
        raw = sample((f.az - yaw) % 360.0)
        if raw is None or not np.isfinite(raw):
            out.append(np.nan)
            continue
        photo = float(rotate_alt(f.az, raw, pitch, tilt_mag, tilt_dir))
        if f.bound:
            # Agreement is one-sided: a photo that also exceeds the ceiling
            # confirms the bound exactly. Only falling short contradicts it.
            out.append(0.0 if photo >= f.alt else photo - f.alt)
        else:
            out.append(photo - f.alt)
    return np.array(out, dtype=float)


def _huber(res, delta):
    a = np.abs(res)
    return np.where(a <= delta, 0.5 * res**2, delta * (a - 0.5 * delta))


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

    def cost(y, p, tm, td):
        r = residuals(used, sample, y, p, tm, td)
        ok = np.isfinite(r)
        if ok.sum() < 4:
            return math.inf, 0
        w = np.array([f.weight for f in used])[ok]
        loss = _huber(r[ok], delta) if robust else 0.5 * r[ok] ** 2
        return float((w * loss).sum() / w.sum()), int(ok.sum())

    best = None
    for y in np.arange(0.0, 360.0, yaw_step):
        for tm in np.arange(0.0, tilt_max + 1e-9, tilt_step):
            dirs = [0.0] if tm == 0 else np.arange(0.0, 360.0, 30.0)
            for td in dirs:
                # pitch has a closed-ish form: centre the residuals
                r = residuals(used, sample, y, 0.0, tm, td)
                ok = np.isfinite(r)
                if ok.sum() < 4:
                    continue
                p0 = float(np.median(r[ok]))
                p0 = max(-pitch_range, min(pitch_range, p0))
                c, n = cost(y, p0, tm, td)
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
