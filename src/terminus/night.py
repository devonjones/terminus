"""Find a horizon in a night-time brightness profile.

The daytime model does not survive after dark. It assumes a column is two
levels — bright sky above, dark terrain below — and finds the step between
them. Measured under suburban light pollution, neither level holds:

* **The sky is not flat.** Skyglow brightens strongly toward the horizon: one
  column measured 22 counts at altitude 60 rising to 71 at altitude 12, a
  threefold gradient with no obstruction involved. A step fitted on top of that
  is divided by the trend it sits on.
* **Terrain is not uniformly dark.** Streetlights and windows are far brighter
  than the night sky. One column read 5.8-10.7 counts throughout — fully
  blocked — except for 53.9 and 56.4 at altitudes 15 and 12.5, a lamp sitting
  inside the obstruction. A two-level fit put the "horizon" on the lamp.

So brightness alone cannot be thresholded. What still holds is that **open sky
follows the skyglow gradient smoothly, and terrain does not**. This module fits
that gradient from the upper part of the column, then looks for where the
profile departs from it.

Per-frame repeats do not help: measured scatter across six captures at one
pointing is 0.00-0.16 counts. The variation between altitudes is real scene
structure, not measurement noise, so averaging cannot improve it.
"""

import numpy as np

LAMP_FACTOR = 1.6  # brighter than the local sky model by this much is a light
MIN_DROP_FRAC = 0.5  # departure from the sky model that counts as terrain
SKY_FLOOR_FRAC = 0.5  # top of column must reach this fraction of the open-sky reference


def fit_skyglow(profile, top_frac=0.5):
    """Linear skyglow model from the upper part of the column.

    The top half is used because it is the part most likely to be open sky. If
    the column is blocked all the way up, the fit is meaningless — which the
    caller detects by finding no sky-like region at all.
    """
    prof = sorted(profile, key=lambda t: -t[0])
    n = max(3, int(len(prof) * top_frac))
    alts = np.array([a for a, _ in prof[:n]], dtype=float)
    lums = np.array([v for _, v in prof[:n]], dtype=float)
    if len(alts) < 3 or np.ptp(alts) < 1e-6:
        return None
    slope, intercept = np.polyfit(alts, lums, 1)
    resid = lums - (slope * alts + intercept)
    return {
        "slope": float(slope),
        "intercept": float(intercept),
        "scatter": float(np.sqrt(np.mean(resid**2))),
    }


def mask_lights(profile, model, factor=LAMP_FACTOR):
    """Flag samples far brighter than the sky model — lamps inside terrain."""
    out = []
    for alt, lum in profile:
        pred = model["slope"] * alt + model["intercept"]
        out.append(bool(pred > 0 and lum > factor * pred))
    return out


def find_horizon(profile, sky_ref=None, min_drop_frac=MIN_DROP_FRAC):
    """Altitude where the column stops following the skyglow model.

    Returns (altitude, detail). Altitude is None when the column never departs
    (open to the search floor) or never follows the model at all (blocked above
    the ceiling); `detail['reason']` says which, because those two are opposite
    conclusions and must not be confused.
    """
    prof = sorted(profile, key=lambda t: -t[0])
    model = fit_skyglow(prof)
    if model is None:
        return None, {"reason": "profile too short"}
    lights = mask_lights(prof, model)
    alts = np.array([a for a, _ in prof], dtype=float)
    lums = np.array([v for _, v in prof], dtype=float)
    pred = model["slope"] * alts + model["intercept"]
    with np.errstate(divide="ignore", invalid="ignore"):
        frac = np.where(pred > 1e-6, lums / pred, 1.0)

    # Is the top of the column sky at all? This test must be ABSOLUTE, against a
    # separately measured open-sky reference. Comparing it to the model fitted
    # from this same column is self-defeating: a column blocked all the way up
    # fits a model to its own terrain, which then matches perfectly. One such
    # column read 5.8-10.7 counts throughout against a reference of 20.7 and was
    # judged "open" by a relative test.
    top = float(np.median(lums[: max(2, len(lums) // 4)]))
    if sky_ref and top < SKY_FLOOR_FRAC * sky_ref:
        return None, {
            "reason": "blocked above the ceiling",
            "top": top,
            "sky_ref": float(sky_ref),
            "model": model,
        }

    for i in range(len(prof)):
        if lights[i]:
            continue  # a lamp is not the horizon
        if frac[i] < min_drop_frac:
            # confirm it stays down, ignoring lamps below
            below = [frac[j] for j in range(i, len(prof)) if not lights[j]]
            if below and float(np.median(below)) <= min_drop_frac:
                return float(alts[i]), {
                    "reason": "departed the skyglow model",
                    "frac": float(frac[i]),
                    "model": model,
                    "lights_at": [float(alts[j]) for j in range(len(prof)) if lights[j]],
                }
    return None, {
        "reason": "open to the search floor",
        "model": model,
        "lights_at": [float(alts[j]) for j in range(len(prof)) if lights[j]],
    }
