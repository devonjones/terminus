"""The guided orientation loop: plan a column, measure it, refit, stop when settled.

`plan` and `orient` implement the result this project is built on — a handful of
information-chosen columns pinning the photo horizon to the sky, instead of a
blind circle — and until now there was no way to run it. It existed as a library
that one ad-hoc session ever called.

This module is the loop that joins them, and it is deliberately separated from
the CLI and from the telescope. It takes a `measure(az)` callable and does not
care whether that slews a mount or reads a saved profile off disk. Two
consequences, both wanted:

  * The loop is testable without hardware, against synthetic horizons where the
    true yaw is known, so "does it converge to the right answer" is a test rather
    than a hope.
  * A night's saved profiles can be REPLAYED through it. Every sweep already
    writes `<mask>_profiles.json`, so a run can be re-judged, and a change to the
    planner or the fit can be compared against a real night without waiting for
    another one.

WHY IT STOPS ON STABILITY, NOT ON RESIDUAL. While only a few columns are in hand
the residual is meaningless: four points fitting four parameters interpolate, and
the RMS reads near zero however wrong the answer is. One fit reported 0.28 deg
from four edges and was nearly published. Worse, RMS is blind to uniform bias,
because the fit absorbs a constant offset into pitch. What settles is the yaw,
and only once there is redundancy — so the yaw settling is the signal.
"""

import numpy as np

from . import plan as planner
from .orient import PARAMS, Fiducial, effective_constraints, fit, native_column
from .orient import rotate as _rotate


def photo_sample(rows):
    """`sample(az)` over a photo mask's columns, interpolated across the wrap.

    `orient.native_column` calls this at arbitrary azimuths while solving a fixed
    point, so it has to answer between the mask's columns and it has to be
    continuous at 0/360 — a discontinuity there would put a false cliff at north
    and drag the yaw toward it.

    A PARTIAL MASK IS BRIDGED, NOT REFUSED, and the bridge is not a measurement.
    A mask covering az 70-250 interpolates a straight line across the 110 degrees
    nobody photographed, because `np.interp` cannot distinguish a gap from a flat
    stretch. That is deliberate — a partial sweep is a normal intermediate state
    and the fit only ever reads the sample AT measured columns, so the invented
    stretch is not what the yaw is solved against. It becomes wrong only if a
    fiducial lands inside the gap, which is the caller's business to avoid; the
    same bridging is what `Horizon` has always done.
    """
    pts = sorted((float(az), float(alt)) for az, alt, *_ in rows)
    if not pts:
        raise ValueError("the photo mask has no columns to fit against")
    az = np.array([p[0] for p in pts])
    alt = np.array([p[1] for p in pts])

    def sample(a):
        return float(np.interp(float(a) % 360.0, az, alt, period=360.0))

    return sample


def orient_mask(rows, solution):
    """Re-read the photo horizon in TRUE azimuth, given a solved orientation.

    Not a relabelling. Yaw alone would be — it is a rotation about the vertical,
    so it only renames columns — but tilt moves a point in azimuth as well as
    altitude, so the photo column that ENDS at a true azimuth is not the one that
    started at `az - yaw`. `native_column` solves for the right one; using the
    naive column instead reintroduces exactly the small-angle error the fit was
    written to avoid.

    Returns [(az, alt, type)] on the same azimuth grid the mask came in on.
    """
    sample = photo_sample(rows)
    yaw = solution["yaw"]
    tm, td, pitch = solution["tilt_mag"], solution["tilt_dir"], solution["pitch"]
    out = []
    for az, *_ in sorted(rows):
        target = float(az)
        phi, raw = native_column(sample, target, yaw, tm, td)
        if raw is None or not np.isfinite(raw):
            continue
        _, landed_alt = _rotate(phi + yaw, raw, pitch, tm, td)
        # The type travels with the column it was read from, not with the
        # azimuth it lands at. `native_column` may read a neighbouring column
        # under tilt, and carrying the ORIGINAL row's type would attach a label
        # to a measurement it did not come from.
        out.append((target, round(float(landed_alt), 2), _type_at(rows, phi)))
    return out


def _type_at(rows, phi):
    """The obstruction type of the photo column nearest native azimuth `phi`."""
    best, label = None, ""
    for az, _alt, *rest in rows:
        d = abs(((float(az) - phi + 180.0) % 360.0) - 180.0)
        if best is None or d < best:
            best, label = d, (rest[0] if rest else "")
    return label


class Step:
    """One column: what was planned, what came back, and what the fit then said.

    Kept as a record rather than printed and dropped, because the interesting
    question after a run is not the final number but how it got there — which
    column moved the yaw, and whether it settled or merely stopped.
    """

    __slots__ = ("az", "fiducial", "solution", "spread", "note")

    def __init__(self, az, fiducial=None, solution=None, spread=None, note=""):
        self.az = az
        self.fiducial = fiducial
        self.solution = solution
        self.spread = spread
        self.note = note


def run(
    rows,
    measure,
    candidates=None,
    seed=4,
    max_columns=12,
    window=3,
    yaw_tol=1.0,
    min_dof=2,
    reachable=None,
    should_stop=None,
    log=print,
    min_headroom=None,
    fit_kw=None,
):
    """Measure columns until the solved yaw stops moving. Returns (solution, steps).

    `measure(az)` returns `(edge, ceiling, uncertainty)` for that azimuth, where
    `edge` is None when nothing was found or a dict with 'alt' and 'snr'. A
    column that finds nothing is NOT dropped: `plan.as_fiducial` turns it into a
    bound at the ceiling, because "measured and blocked above 60" is information
    and an absent column is not distinguishable from an unvisited one.

    `measure` may return None to mean the column could not be attempted at all —
    a refused slew, a missing profile. That is different from finding nothing,
    and it is recorded as a skip rather than as a bound.

    Seeding is even spacing, not information: with no fiducials there is nothing
    to be D-optimal about, and the criterion needs a starting configuration
    before it can improve one. After that every column is chosen by
    `plan.next_column`.

    Returns `solution=None` when fewer than four columns were measured, which the
    fit needs. That is a real outcome of a short or unlucky run, not an error.

    `should_stop()` is checked BEFORE each column and ends the run cleanly when
    it answers True. It exists so an observing-window deadline can be enforced
    where the answer is current, rather than by a caller before launch: a caller
    starting an N-column run cannot know how long N columns take, and the
    estimate degrades in the direction that matters (see terminus-17, where a
    run overran because the sky brightened, more columns resolved, and it slowed
    exactly as the deadline approached).

    `fit_kw` passes grid settings through to `orient.fit`. The default grid is
    the accurate one and costs tens of seconds per refit — cheap next to the
    minutes a column takes to measure, and far too slow for a test suite, which
    is the only reason this is reachable.
    """
    sample = photo_sample(rows)
    # THE GRADIENT MUST BE READ IN THE PHOTO'S OWN FRAME. The mask is in the
    # panorama's azimuth and the scope points in true azimuth, so the slope that
    # decides whether a column carries yaw information sits at `az - yaw`, not at
    # `az`. Asking for it at the true azimuth read the horizon from a completely
    # different part of the sky: with a solved yaw of 161, true az 140 looked
    # like a gradient of -5.6 (steep, and worth measuring) when the slope there
    # is really +0.19 — flat, and nearly worthless for pinning the yaw. The
    # planner spent the whole run choosing columns on a horizon it was not
    # pointing at.
    #
    # Before the first fit there is no yaw to correct by, which is exactly why
    # the seeds are evenly spaced rather than chosen: with nothing solved there
    # is nothing to be optimal about.
    photo_gradient = planner.horizon_gradient([(float(az), float(alt)) for az, alt, *_ in rows])

    def gradient_at(true_az):
        yaw = solution["yaw"] if solution else 0.0
        return photo_gradient(true_az - yaw)

    if candidates is None:
        candidates = sorted({int(round(float(az))) % 360 for az, *_ in rows})
    if not candidates:
        raise ValueError("no candidate azimuths to measure")

    solution = None
    fids, measured, history, steps = [], [], [], []
    queue = planner.seed_columns(min(seed, len(candidates)), candidates)

    while len(measured) < max_columns:
        if should_stop is not None and should_stop():
            steps.append(Step(None, note="stopped early"))
            break
        if queue:
            az = queue.pop(0)
        else:
            az, _score = planner.next_column(
                measured, [c for c in candidates if c not in measured], gradient_at,
                reachable=reachable,
            )  # fmt: skip
            if az is None:
                log("no reachable column left to measure; stopping", flush=True)
                steps.append(Step(None, note="nothing reachable"))
                break
        if az in measured:
            continue

        got = measure(az)
        if got is None:
            log(f"az {az:3d}: skipped (not attempted)", flush=True)
            steps.append(Step(az, note="skipped"))
            # Do not retry it, or the loop can spin on one unreachable column.
            candidates = [c for c in candidates if c != az]
            continue

        edge, ceiling, uncertainty = got
        f = planner.as_fiducial(az, edge, ceiling, Fiducial, uncertainty=uncertainty)
        fids.append(f)
        measured.append(az)

        if len(fids) < 4:
            log(f"az {az:3d}: alt {f.alt:5.1f}{' (bound)' if f.bound else ''}  "
                f"[{len(fids)}/4 before a fit is possible]", flush=True)  # fmt: skip
            steps.append(Step(az, f, note="collecting"))
            continue

        solution = fit(fids, sample, min_headroom=min_headroom, **(fit_kw or {}))
        history.append(solution)
        stable, spread = planner.is_stable(history, window=window, yaw_tol=yaw_tol)
        # STABILITY IS NOT ENOUGH ON ITS OWN. With as many free parameters as
        # constraints the fit interpolates: the yaw can sit perfectly still
        # across refits while being undetermined, and the residual stays small
        # because it has to. Two runs on the same photo mask settled on 130 and
        # 160 that way, each reporting a small RMS and neither having a single
        # degree of freedom.
        #
        # A BOUND usually contributes nothing — scored one-sided, it is satisfied
        # for free whenever the photo already exceeds its ceiling — so the count
        # that matters is of constraints that actually hold something down, not
        # of columns visited.
        dof = effective_constraints(fids, sample, solution) - PARAMS
        solution["dof"] = dof
        if stable and dof < min_dof:
            stable = False
            log(
                f"   yaw is holding still but the fit has {dof} degree(s) of freedom; "
                f"measuring on until it has {min_dof}",
                flush=True,
            )
        steps.append(Step(az, f, solution, spread))
        log(
            f"az {az:3d}: alt {f.alt:5.1f}{' (bound)' if f.bound else ''}  "
            f"yaw {solution['yaw']:6.2f}  pitch {solution['pitch']:5.2f}  "
            f"tilt {solution['tilt_mag']:4.1f}@{solution['tilt_dir']:5.1f}  "
            f"rms {solution['rms']:4.2f}  n {solution['n']} dof {solution['dof']:+d}"
            + (f"  spread {spread:.2f}" if spread is not None else "  spread -"),
            flush=True,
        )
        if stable:
            log(f"yaw settled within {yaw_tol:g} deg over {window} refits; stopping", flush=True)
            return solution, steps

    # Falling out of the loop means the budget ran out, not that anything
    # converged — and an unsettled fit reads exactly like a settled one if
    # nobody says otherwise. It is still the best estimate available, so it is
    # returned rather than withheld; what must not happen is it being MISTAKEN
    # for a converged answer.
    if solution is not None:
        _, spread = planner.is_stable(history, window=window, yaw_tol=yaw_tol)
        steps.append(Step(None, note="did not settle"))
        # `spread` is None until there have been window+1 refits, and that is the
        # commonest way to arrive here: the run was too short to have an opinion
        # at all. Formatting None as a number raised TypeError, turning an honest
        # "not enough evidence yet" into a crash.
        how_far = (
            f"the yaw was still moving by {spread:.2f} deg against a {yaw_tol:g} deg rule"
            if spread is not None
            else f"there were not yet {window + 1} refits to judge stability over"
        )
        log(
            f"STOPPED WITHOUT SETTLING after {len(measured)} columns: {how_far}. "
            "Measure more columns (--max-columns), or look at the largest residuals — "
            "a column the fit cannot reconcile keeps the answer moving.",
            flush=True,
        )
    return solution, steps


def replay(profiles, uncertainty=None, sky_ref=None):
    """A `measure(az)` that re-judges a saved sweep instead of observing.

    `profiles` is what every sweep already writes beside its mask:
    `{az: [[alt, lum], ...]}`, ordered from high altitude down. Re-judging them
    is not a toy — a column costs minutes of scope time and a clear night, and
    the decision made from its brightness column is a few lines of arithmetic
    that we keep changing. Being able to re-run the planner and the fit against a
    real night, without needing another one, is the difference between tuning
    against evidence and tuning against memory.

    The altitude resolution is the SWEEP's, not the bisection's: these are the
    coarse samples, so a replayed edge lands on the nearest recorded altitude
    rather than on the refined crossing. Good enough to exercise the loop and to
    compare planners; not a substitute for the original measurement.

    `sky_ref` gates the absolute step size. Defaults to the brightest sample in
    the whole run, which is what `run_sweep` converges on as it goes.
    """
    import numpy as np

    from .sweep import classify_no_edge, find_edge, night_find_edge

    # A profile carries its channel, because the numbers change meaning with
    # it: night medians are raw16 counts off the star-mode imaging channel, day
    # means are RGB off the scenery stream, and judging one with the other's
    # judge is confidently wrong (F-13). Night columns arrive as
    # {"channel": "star4800", "profile": [[alt, lum], ...]}; a plain list is
    # the day shape, so profiles files from before the night channel replay
    # unchanged.
    prof, night_cols = {}, set()
    for az, rows in profiles.items():
        if isinstance(rows, dict):
            channel = rows.get("channel")
            if channel != "star4800":
                raise ValueError(f"az {az}: unknown profile channel {channel!r}")
            night_cols.add(int(az) % 360)
            rows = rows.get("profile") or []
        prof[int(az) % 360] = [(float(a), float(lum)) for a, lum in rows]
    if sky_ref is None:
        # THE MEDIAN OF THE PER-COLUMN PEAKS, not the brightest sample anywhere.
        # The maximum picks whatever is brightest in the whole run, and in
        # daylight that is a sunlit wall rather than open sky: on 2026-08-06 it
        # chose 253.4 where the run itself had measured 119.3 near the zenith,
        # more than doubling the absolute-step gate and turning two real edges
        # (az 40 and az 275) into false bounds. The median is robust to a bright
        # surface in one or two columns, which the maximum is not. Night columns
        # stay out of it: raw16 counts are not in the day reference's units.
        peaks = [
            max(lum for _, lum in rows)
            for az, rows in prof.items()
            if rows and az not in night_cols
        ]
        sky_ref = float(np.median(peaks)) if peaks else None

    def measure(az):
        rows = prof.get(int(az) % 360)
        if not rows:
            return None  # not attempted that night: different from finding nothing
        ceiling = max(a for a, _ in rows)
        if int(az) % 360 in night_cols:
            # Re-judged by the CURRENT night detector, exactly as orient's
            # checkpoint resume does (D-16): the stored verdict is what an old
            # judge thought, the profile is what the sky did. The edge lands on
            # the coarse bracket's midpoint — the bisection samples were never
            # saved, so that is the honest resolution.
            if len(rows) < 3:
                return None
            idx, verdict = night_find_edge(rows)
            if idx is None:
                if verdict == "blocked":
                    return None, ceiling, uncertainty
                return None  # open, or unreadable: no constraint the fit can use
            mid = round((rows[idx][0] + rows[idx + 1][0]) / 2.0, 1)
            return {"alt": mid}, ceiling, uncertainty
        k, _step, snr = find_edge(rows, sky_ref)
        if k is None:
            # The SAME question the live path asks, answered the same way. It was
            # not: this returned a bound for every non-detection while
            # `scan_horizon` had learned to demand contrast first, so replaying a
            # night manufactured exactly the false bounds the live path had
            # stopped producing — and a false bound is a lever on the fit.
            verdict = classify_no_edge(rows, sky_ref)
            if verdict == "blocked":
                return None, ceiling, uncertainty
            return None  # open, or unreadable: no constraint the fit can use
        return {"alt": rows[k][0], "snr": snr}, ceiling, uncertainty

    return measure
