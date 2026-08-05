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
# Growing the sky region stops at the first sample that is not on the line, and
# that test is NOT the detection threshold. MIN_DROP_FRAC is a factor of two,
# deliberately decisive, and using it to grow let terrain in: az 190 drops from
# 62.9 to 32.7, a ratio of 0.505, which clears a 0.5 test by one part in two
# hundred and then poisons the fit it was supposed to stop. Terrain is a STEP,
# so the natural scale is the scatter of the sky already established — the same
# drop is thirty sigma. The relative floor keeps a chance-flat opening quartet
# from having near-zero scatter and stopping growth on the first ripple.
DEPARTURE_SIGMA = 4.0
# A quarter. Chosen to sit clearly between two measurements, not tuned to taste:
# real skyglow departs from a straight line by perhaps ten to twenty per cent
# over a full column, and the az 190 roofline is a forty-eight per cent drop.
# Anything in between separates model error from terrain.
#
# THIS RETIRES MIN_DROP_FRAC AS THE DETECTOR. That was a factor of two, and two
# real columns sat astride it: az 60 cleared it by 0.004 and was then discarded
# by the persistence check, while az 190 missed it by 0.018 and was never seen
# at all. A rule that a genuine roofline fails by one part in fifty is not
# measuring the thing it claims to measure. MIN_DROP_FRAC is kept only for
# `mask_lights`-adjacent callers and for compatibility.
MIN_DIP_FRAC = 0.25
# This module's premise is that open sky follows a measurable skyglow gradient.
# That gradient is about a count per degree — one column measured 22 counts at
# altitude 60 rising to 71 at 12 — against a scatter of one to six counts. Over
# a narrow window there is nothing to measure: a 3.5 degree refinement scan at
# az 190 gives 3.5 counts of gradient against 5.9 of scatter, so "sky" cannot be
# established and every sample looks equally like it.
#
# The failure that makes this worth refusing rather than attempting: that column
# is mostly ROOF, and the module answered "open to the search floor". A confident
# wrong answer in the unsafe direction. A narrow window is `sweep.find_edge`'s
# job — a two-level step needs no gradient.
MIN_COLUMN_SPAN = 10.0


def departed(lum, pred, scatter):
    """Has this sample fallen off the sky model? One rule, used everywhere.

    Growing the sky region and detecting the horizon are the same question asked
    at different points, so they must not use different tests — and they did.
    Growth used MIN_DROP_FRAC, a factor of two, which let terrain in; detection
    used it too, and missed real steps by fractions of a per cent. Az 190 falls
    from 62.9 to 32.7, a ratio of 0.518 against a 0.5 rule: a genuine roofline,
    thirty sigma clear of the sky's own scatter, rejected by one part in fifty.
    Az 60 was the mirror image, clearing the same rule by 0.004 and then being
    thrown away by the persistence check.

    Terrain is a STEP, so the natural scale is the scatter of the sky already
    established. The relative floor stops a chance-flat opening quartet, whose
    scatter can be near zero, from calling every ripple a horizon.
    """
    if pred <= 0:
        return False  # no opinion where the model does not describe sky
    return lum < pred - max(DEPARTURE_SIGMA * scatter, MIN_DIP_FRAC * pred)


def fit_skyglow(profile, top_frac=0.5):
    """Linear skyglow model from the upper part of the column.

    The top fraction is used because it is the part most likely to be open sky.
    That is an assumption, not a guarantee, and on a blocked column it fails
    badly — see `sky_model`, which is what callers should use.

    Returns `slope`, `intercept`, `scatter`, and `zero_alt`: the altitude where
    the model predicts zero brightness, or None if it never does within a
    plausible range. A model that predicts NEGATIVE sky is not describing sky.
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
        "zero_alt": float(-intercept / slope) if slope else None,
        "n_fitted": int(len(alts)),
        "span": float(np.ptp(alts)),
    }


def is_skyglow(model, alt_min):
    """Can this model describe sky all the way down the column it will be used on?

    One physical test, and it needs no threshold: brightness cannot be negative.
    A camera cannot report it and the sky does not do it. So a linear model that
    goes below zero anywhere in the range it is about to be applied over is not
    a model of sky there, whatever it is elsewhere.

    That is exactly what a terrain-contaminated fit looks like. On az 60, fitted
    across its own ground, the model came back slope +4.244 intercept -55.115 —
    predicting negative sky below altitude 13 in a column searched to 3. Every
    consequence in terminus-47 follows from that one fact, including the worst:
    where the prediction crossed zero the ratio test scored the DEEPEST TERRAIN
    AS PERFECT SKY.

    Deliberately NOT a test on the sign of the slope. Skyglow does brighten
    toward the horizon, so sky usually has a negative slope, but a genuine
    stretch of sky can rise slightly — az 190 rises 3.35 counts over 6 degrees,
    four per cent, which is real and harmless. Rejecting on slope alone threw
    that column away and lost a measurement the ticket itself cites.
    """
    if model is None:
        return False
    return model["slope"] * alt_min + model["intercept"] > 0.0


def mask_lights(profile, model, factor=LAMP_FACTOR):
    """Flag samples far brighter than the sky model — lamps inside terrain."""
    out = []
    for alt, lum in profile:
        pred = model["slope"] * alt + model["intercept"]
        out.append(bool(pred > 0 and lum > factor * pred))
    return out


def sky_model(profile, min_samples=4):
    """Fit the skyglow to the SKY, by GROWING DOWN from the top of the column.

    `fit_skyglow` on a fixed top fraction assumes the top half is open. On a
    blocked column it is not, and the terrain then sets the slope — which is the
    whole of terminus-47. On az 60 the horizon sits at altitude 36 in a column
    searched from 55, so more than half the samples are ground and the fit came
    back describing the ground: slope +4.244, predicting negative sky below 13.

    Growing rather than shrinking, because shrinking needs the contaminated
    model to identify its own contamination, and a model that bad cannot. Fitted
    across the terrain of az 190, nothing in the column looked like a departure
    at all, so an iterative shrink simply kept the broken fit. Growth starts
    from the samples most likely to be sky — the highest — and stops at the
    first that falls away from what has been established so far.

    Returns (model, n_top): the model and how many of the highest samples it was
    fitted to.
    """
    prof = sorted(profile, key=lambda t: -t[0])
    if len(prof) < min_samples:
        return None, 0
    kept = list(range(min_samples))
    model = fit_skyglow(prof[:min_samples], top_frac=1.0)
    if model is None:
        return None, 0
    for i in range(min_samples, len(prof)):
        alt, lum = prof[i]
        pred = model["slope"] * alt + model["intercept"]
        if pred <= 0:
            break
        tolerance = max(DEPARTURE_SIGMA * model["scatter"], MIN_DIP_FRAC * pred)
        if lum > pred + tolerance:
            # A LAMP. Far brighter than the sky it sits in, so it is not sky and
            # must not join the fit — one streetlight at 90 counts against a
            # 27-count sky took the scatter from 0.1 to 22, after which nothing
            # below could look like terrain and the whole column read as open.
            # Stepped over rather than stopped at, because there is usually sky
            # below a lamp and it is still sky.
            continue
        if lum < pred - tolerance:
            break  # terrain: this sample is not on the same line
        kept.append(i)
        grown = fit_skyglow([prof[j] for j in kept], top_frac=1.0)
        if grown is None:
            break
        model = grown
    return model, len(kept)


def find_horizon(profile, sky_ref=None, min_drop_frac=MIN_DROP_FRAC):
    """Altitude where the column stops following the skyglow model.

    Returns (altitude, detail). Altitude is None when the column never departs
    (open to the search floor) or never follows the model at all (blocked above
    the ceiling); `detail['reason']` says which, because those two are opposite
    conclusions and must not be confused.
    """
    prof = sorted(profile, key=lambda t: -t[0])
    span = prof[0][0] - prof[-1][0] if len(prof) > 1 else 0.0
    if span < MIN_COLUMN_SPAN:
        return None, {
            "reason": (
                f"column spans only {span:.1f} deg: too little for a skyglow gradient "
                f"to be measurable (needs {MIN_COLUMN_SPAN:g}). Use sweep.find_edge, "
                "which fits a two-level step and needs no gradient."
            ),
            "span": float(span),
        }
    model, n_top = sky_model(prof)
    if model is None:
        return None, {"reason": "profile too short"}
    if not is_skyglow(model, min(a for a, _ in prof)):
        # The fit does not describe sky over the range it would be applied to.
        # Growing from the top makes this rare, so reaching here means the top of
        # the column is not sky either: the ceiling is inside the obstruction.
        return None, {
            "reason": "no usable sky model: it predicts negative brightness in this column",
            "model": model,
        }
    lights = mask_lights(prof, model)
    alts = np.array([a for a, _ in prof], dtype=float)
    lums = np.array([v for _, v in prof], dtype=float)
    pred = model["slope"] * alts + model["intercept"]
    # NaN, not 1.0, where the model predicts non-positive brightness. Clamping
    # to 1.0 scored the DEEPEST TERRAIN AS PERFECT SKY — the further into the
    # ground a column went, the more sky-like it looked — and poisoned any
    # median taken across it. A model that predicts negative sky is not a model
    # there, and "no opinion" is the honest value.
    with np.errstate(divide="ignore", invalid="ignore"):
        frac = np.where(pred > 0, lums / pred, np.nan)

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

    scatter = model["scatter"]
    for i in range(len(prof)):
        if lights[i] or not np.isfinite(frac[i]):
            continue  # a lamp is not the horizon, and neither is an opinionless sample
        if departed(lums[i], pred[i], scatter):
            # Confirm it STAYS down, ignoring lamps and samples the model has no
            # opinion about. A single dark frame is not a horizon; a roofline
            # darkens everything below it.
            below = [
                departed(lums[j], pred[j], scatter)
                for j in range(i, len(prof))
                if not lights[j] and np.isfinite(frac[j])
            ]
            if below and sum(below) >= 0.5 * len(below):
                # Step back over lamps as well as over the departure: the last
                # CLEAR SKY sample is the answer, and a streetlight inside the
                # obstruction is not sky. Reporting its altitude would put the
                # horizon at a lamp burning below the roofline.
                j = i - 1
                while j >= 0 and (lights[j] or not np.isfinite(frac[j])):
                    j -= 1
                if j < 0:
                    # The very highest sample already departs, so the sky the
                    # model was fitted to is above the search ceiling. Reporting
                    # a horizon here would be reporting one from inside terrain.
                    return None, {
                        "reason": "blocked above the ceiling",
                        "frac": float(frac[0]),
                        "model": model,
                    }
                # The LAST SAMPLE THAT STILL FOLLOWED, not the first that
                # departed. The true crossing lies between them and the sample
                # grid cannot resolve it, so the choice is which way to be
                # wrong. `find_edge` reports the last clear sky, the mask's
                # header defines altitude as "lowest clear sky", and a planner
                # takes the number literally — so reporting the first TERRAIN
                # sample would claim clear sky where there is ground. Measured
                # on az 60: the crossing is at 36.46, the samples are 37 and 35,
                # and 35 would have promised two degrees of sky that is a roof.
                return float(alts[j]), {
                    "reason": "departed the skyglow model",
                    "frac": float(frac[i]),
                    "model": model,
                    "n_top": n_top,
                    "lights_at": [float(alts[j]) for j in range(len(prof)) if lights[j]],
                }
    return None, {
        "reason": "open to the search floor",
        "model": model,
        "n_top": n_top,
        "lights_at": [float(alts[j]) for j in range(len(prof)) if lights[j]],
    }
