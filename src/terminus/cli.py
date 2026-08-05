"""terminus command line: measure a local horizon with a Seestar and export it.

  terminus preflight            connect, show mode + Sun, list Sun-blocked azimuths
  terminus point AZ ALT         goto one az/alt (Sun-guarded), verify the landing
  terminus classify             capture at the current pointing, report sky/veg/structure
  terminus sweep                full horizon sweep -> mask YAML (+ review frames)
  terminus export MASK          write N.I.N.A. .hrz and Stellarium .txt from a mask

Global: --config PATH (default ./config.toml). sweep: --az-start --az-end --out
--frames --no-export --dry-run.
"""

import argparse
import json
import math
import os
import sys
import time

from .client import Seestar, SeestarError
from .config import ConfigError, load_config
from .export import (
    MaskError,
    default_meta,
    export_all,
    is_oriented,
    load_columns,
    write_mask,
)
from .mosaic import MIN_CONTROL_POINTS, MosaicError
from .sweep import (
    MAX_POINTING_MISSES,
    Pointer,
    PointingError,
    Sky,
    SunGuard,
    classify,
    column_touches_sun,
    obstruction_type,
    run_sweep,
    scan_horizon,
    sky_reference,
)


def _connect(cfg):
    sc = Seestar(cfg["host"], cfg["pem"])
    if not sc.authenticate():
        raise SeestarError("authentication failed — check the interop key path")
    return sc


def _sky(sc, cfg):
    site = cfg["site"]
    if "lat" in site and "lon" in site:
        return Sky(site["lat"], site["lon"], site.get("elev_m", 0.0))
    loc = sc.location()
    if not loc:
        raise SeestarError("no site: add [site] lat/lon to config or set location on the scope")
    return Sky(loc[1], loc[0], site.get("elev_m", 0.0))  # scope gives (lon, lat)


def cmd_preflight(sc, cfg, args):
    sky = _sky(sc, cfg)
    eq = sc.is_eq_mode()
    saz, salt = sky.sun()
    rd = sc.equ_coord()
    print(f"connected {cfg['host']} | EQ mode: {eq}")
    if not eq:
        print(
            "  !! not in EQ mode — terminus points by RA/Dec goto and needs a polar-aligned EQ mount"
        )
    if rd:
        az, alt = sky.radec_to_altaz(*rd)
        print(f"pointing: az {az:.1f} alt {alt:.1f}")
    print(f"Sun: az {saz:.1f} alt {salt:.1f} (avoid within {cfg['sweep']['sun_cone_deg']} deg)")
    s = cfg["sweep"]
    blocked = [
        a
        for a in range(0, 360, s["az_step"])
        if column_touches_sun(sky, a, s["alt_min"], s["alt_max"], s["sun_cone_deg"])
    ]
    print(f"azimuths touching the Sun cone: {blocked or 'none'}")


def cmd_point(sc, cfg, args):
    sky = _sky(sc, cfg)
    ptr = Pointer(
        sc, sky, cfg["sweep"]["sun_cone_deg"], cfg["sweep"]["slew_step_deg"], args.dry_run
    )
    try:
        faz, falt = ptr.point_to(args.az, args.alt)
        print(f"target ({args.az},{args.alt}) -> landed az {faz:.1f} alt {falt:.1f}")
    except SunGuard as e:
        print("SUN GUARD:", e)
    except PointingError as e:
        print("POINTING FAILED:", e)
        sys.exit(2)


def cmd_classify(sc, cfg, args):
    sc.stop_view()
    time.sleep(1)
    sc.start_view("scenery")
    time.sleep(3)
    s, v, st, lum = classify(sc.capture_rgb())
    verdict = (
        "SKY" if s > cfg["sweep"]["clear_thresh"] else f"OBSTRUCTION ({obstruction_type(v, st)})"
    )
    print(f"sky {s:.2f}  veg {v:.2f}  structure {st:.2f}  (lum {lum:.0f}) -> {verdict}")
    sc.stop_view()


# Loose on purpose: the mask rounds lat/lon to four decimals (~8 m by itself)
# and a GPS fix wanders. This catches crossing the garden, not nudging the
# tripod.
MERGE_POSITION_TOLERANCE_M = 30.0


def _check_mergeable(prior_meta, sky):
    """Refuse to merge two runs that do not describe the same sky.

    A merge silently mixes runs, and the mask is a durable artifact other tools
    consume, so the mixing has to be checked rather than assumed.

    Orientation: a photo-derived mask is in the panorama's own azimuth until the
    orientation is solved. Merging true-north scope columns into it produces a
    file where some columns are true and some are not, with nothing to say
    which — and the header still claims one frame for all of them.

    Position: the horizon belongs to where the tripod stood. A 2 m fence at 5 m
    moves 11.9 degrees for 2 m of observer displacement, so merging across a
    move is merging two different horizons.

    The comparison is in METRES, not degrees. Comparing a degree threshold
    against both lat and lon makes the longitude test 23 per cent tighter at
    this latitude, because a degree of longitude shrinks by cos(lat) — 0.0003
    deg is 33 m north-south and 26 m east-west at 39.8. That asymmetry is not
    what anyone means by "the same spot".

    The tolerance is deliberately loose: the mask rounds lat/lon to four
    decimals (about 8 m on its own) and a GPS fix wanders, so this catches
    crossing the garden rather than shuffling the tripod.

    A mask with NO recorded position is refused rather than waved through. An
    absent value is not agreement, and passing it would be self-perpetuating —
    the merged file would still carry no position, so every later patch would
    skip the check as well.
    """
    if not is_oriented(prior_meta):
        raise MaskError(
            "refusing to merge: the existing mask is UNORIENTED (its azimuth is the "
            "panorama's own) and the scope measures in true azimuth. Merging would "
            "produce a mask that is partly one frame and partly the other. Solve the "
            "orientation first, or write to a different --out."
        )
    plat, plon = prior_meta.get("lat"), prior_meta.get("lon")
    if plat is None or plon is None:
        # No position recorded is not "the position matches". A mask that never
        # said where it stood cannot be checked against this run, and merging
        # anyway would leave the merged file just as positionless — so the hole
        # never closes and every later patch skips the check too.
        raise MaskError(
            "refusing to merge: the existing mask records no lat/lon, so there is no "
            "way to tell whether it was measured from this spot. A near obstruction "
            "shifts by degrees for a few metres. If you know it was the same position, "
            "add lat and lon to the mask header and re-run; otherwise write to a "
            "different --out."
        )
    lat_now, lon_now = sky.loc.lat.deg, sky.loc.lon.deg
    dnorth = (float(plat) - lat_now) * 111320.0
    # Wrapped, or two points 2 m apart either side of the antimeridian read as
    # 39,466 km and a legitimate merge is refused. It fails closed rather than
    # corrupting anything, but the person it fails for cannot do much about
    # their longitude.
    dlon = ((float(plon) - lon_now + 180.0) % 360.0) - 180.0
    deast = dlon * 111320.0 * math.cos(math.radians(lat_now))
    moved = math.hypot(dnorth, deast)
    if moved > MERGE_POSITION_TOLERANCE_M:
        raise MaskError(
            f"refusing to merge: the existing mask was measured at {plat},{plon} and "
            f"this run is at {lat_now:.4f},{lon_now:.4f}, about {moved:.0f} m away. The "
            "horizon belongs to one position — a near obstruction shifts by degrees for "
            "a few metres — so these are two different horizons. Write to a different "
            "--out."
        )


def _az_list(value, field):
    """Azimuths from a meta field a human may have hand-edited.

    A bare `skipped_az: 190` is a reasonable shorthand to write and is accepted.
    A string is not: iterating "190" yields the characters, so `int(a) for a in
    ...` quietly produces {1, 9, 0} — three wrong columns rather than an error.
    That is the failure worth being loud about, so it raises MaskError with the
    correction rather than a bare TypeError from somewhere deeper.
    """
    if value is None:
        return set()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return {int(value)}
    if isinstance(value, str) or not isinstance(value, (list, tuple, set)):
        raise MaskError(
            f"the mask's {field} is {value!r}; it must be a list of azimuths "
            f"(for example {field}: [190, 195]) or a single number."
        )
    try:
        return {int(a) for a in value}
    except (TypeError, ValueError) as exc:
        raise MaskError(f"the mask's {field} contains a non-numeric azimuth: {value!r}") from exc


def _merge_column(prior_col, fresh_col):
    """One column re-measured: take the new altitude, keep the better type.

    A whole-record replace was wrong, and in a way that only shows up at night.
    Re-measure a column at 2 a.m. that the photo had segmented as `tree` and the
    fresh record carries `type: ""` — the scope cannot name anything after
    sunset — so the replace silently threw away a real, in-focus segmentation
    and the column exported without its seasonal buffer. A targeted re-measure,
    which is meant to IMPROVE a column, made it worse.

    The two fields come from two instruments and are not interchangeable, which
    is the whole point of `type_source`. Altitude is the scope's answer and the
    fresh one is always better. Type is whichever instrument could actually name
    it, so a fresh naming replaces an old one and a fresh SILENCE does not.
    """
    if not prior_col:
        return fresh_col
    out = dict(fresh_col)
    if not out.get("type") and prior_col.get("type"):
        out["type"] = prior_col["type"]
        out["type_source"] = prior_col.get("type_source")
    # Photo fields survive a re-measure only if they describe the OBSTRUCTION
    # rather than the altitude. `gap_fraction` is how gappy the canopy is and
    # `uncertainty` is how far that boundary moves — both are properties of the
    # thing, and a second look at it from a different instrument does not change
    # them.
    for key in ("gap_fraction", "uncertainty"):
        if out.get(key) is None and prior_col.get(key) is not None:
            out[key] = prior_col[key]
    # `clipped` is not such a property. It means THIS ALTITUDE IS A LOWER BOUND,
    # because the obstruction ran off the top of the photo. Once the scope
    # supplies a real crossing the altitude is no longer that bound, so carrying
    # the flag forward makes the file assert something false about a number it
    # no longer describes — the same bug this function fixes for `type`, one
    # field over. Dropped rather than carried.
    #
    # A scope re-measure that hit its OWN ceiling is a different statement and
    # the mask cannot express it yet: run_sweep returns a bare (alt, type) and
    # the boundedness lives only in the status string. That is terminus-7's
    # territory, not something to paper over by reusing the photo's flag.
    return out


def _merge_meta(prior_meta, fresh, measured, skipped, present=None):
    """Meta for a merged mask, which describes BOTH runs.

    `dict.update` was wrong: it let a two-column patch overwrite the whole file's
    description. A patch that skipped nothing replaced `skipped_az: [190, 195]`
    with `[]`, so those columns stayed absent from the mask with the record of
    WHY they were missing destroyed.

    Skips are unioned, minus every column the merged mask actually HOLDS —
    `present`, not just the columns this run measured. Subtracting only this
    run's measurements let a column be listed as skipped while its altitude sat
    in the file: measure az 190 in one patch, re-request it in the next and have
    the Sun block it, and `skipped_az` said 190 was never measured while
    `horizon:` still carried it from before. The meta then contradicts the data
    it describes. Nothing skipped this time erases what was already measured.

    The original measurement date is kept and the patch date recorded
    separately, because a mask whose `measured` reads today when most of its
    columns are from Tuesday is a false record.
    """
    meta = dict(prior_meta)
    have = set(measured) if present is None else set(present)
    prior_skipped = _az_list(prior_meta.get("skipped_az"), "skipped_az")
    meta["skipped_az"] = sorted((prior_skipped | set(skipped)) - have)
    meta["measured"] = prior_meta.get("measured", fresh["measured"])
    meta["patched"] = sorted(set(prior_meta.get("patched") or []) | {fresh["measured"]})
    meta["patched_columns"] = sorted(
        _az_list(prior_meta.get("patched_columns"), "patched_columns") | {int(a) for a in measured}
    )
    return meta


def cmd_sweep(sc, cfg, args):
    if not sc.is_eq_mode() and not args.dry_run:
        raise SeestarError("not in EQ mode; terminus needs a polar-aligned EQ mount")
    sky = _sky(sc, cfg)
    out = args.out or "horizon_mask.yaml"
    frames = args.frames or (os.path.splitext(out)[0] + "_frames")
    if not args.dry_run:
        sc.stop_view()
        time.sleep(1)
        # Lock exposure BEFORE the view starts: with auto-exposure the camera
        # renormalises every frame and the sky/terrain difference disappears.
        locked = sc.lock_exposure(exp_ms=cfg["sweep"].get("exp_ms"), gain=cfg["sweep"].get("gain"))
        print(f"exposure locked: {locked}")
        sc.start_view("scenery")
        time.sleep(3)
    # getattr, matching cmd_export: several tests build an args namespace by
    # hand, and a new flag should not break tests of unrelated behaviour.
    requested = getattr(args, "azimuths", None)
    azimuths = None
    if requested:
        azimuths = [float(a) for a in requested.replace(" ", "").split(",") if a]
        if not azimuths:
            raise SeestarError("--azimuths was given but parsed to nothing")
        print(f"measuring {len(azimuths)} explicit columns: {sorted(azimuths)}")

    # Merge, never replace. A targeted re-measure of four columns must not
    # discard the thirty that were already good — that is the whole point of
    # measuring a list rather than a range.
    prior, prior_meta = {}, {}
    if azimuths and os.path.exists(out):
        prior_meta, prior_cols = load_columns(out)
        _check_mergeable(prior_meta, sky)
        prior = dict(prior_cols)
        print(f"merging into {out} ({len(prior)} existing columns)")

    aborted = None
    try:
        mask, skipped, profiles = run_sweep(
            sc,
            sky,
            cfg["sweep"],
            args.az_start,
            args.az_end,
            save_dir=frames,
            dry=args.dry_run,
            azimuths=azimuths,
        )
    except PointingError as e:
        # A sweep runs for hours. Losing every column already measured because
        # the mount stalled near the end is a worse outcome than the fault being
        # reported, so save what was measured and then fail loudly. Without this
        # the abort exits through main() as a bare traceback and writes nothing.
        aborted = e
        # `partial` is None when the failure happened before any sweep state
        # existed — a goto that never arrived, raised from inside the slew.
        mask, skipped, profiles = e.partial or ({}, [], {})
    if not args.dry_run:
        try:
            sc.stop_view()
        except (OSError, SeestarError) as e:
            # Never let this skip the save below. A scenery view left running is
            # the documented precondition for the frozen-RTSP failure, so it is
            # worth attempting and worth reporting — but the measurement matters
            # more.
            #
            # BOTH types are needed. stop_view goes through client.call, which
            # reconnects on a dropped socket; that path raises OSError from the
            # socket itself but SeestarError when re-authentication fails or the
            # re-entrancy guard trips. Catching only OSError left the second one
            # skipping write_mask — the same bug one exception class over, and it
            # would also have exited 1 rather than the 2 that means "abandoned
            # but saved".
            print(f"warning: could not stop the view ({e})", file=sys.stderr)
    if profiles:
        prof_path = os.path.splitext(out)[0] + "_profiles.json"
        with open(prof_path, "w") as f:
            json.dump(profiles, f, indent=1)
        print(f"wrote {prof_path} (raw column brightness profiles)")
    lat = sky.loc.lat.deg
    lon = sky.loc.lon.deg
    fresh = default_meta(round(lat, 4), round(lon, 4), cfg["sweep"], skipped)
    # The scope reads type from colour, so a column it typed was typed in
    # daylight — after sunset `obstruction_type` returns "" rather than guessing.
    # Stamped here rather than in run_sweep so its (alt, type) contract, which
    # several callers and tests depend on, stays as it was.
    mask = {
        az: {"alt": alt, "type": typ, "type_source": "scope" if typ else None}
        for az, (alt, typ) in mask.items()
    }
    if prior:
        merged = dict(prior)
        for az, col in mask.items():
            merged[az] = _merge_column(prior.get(az), col)
        meta = _merge_meta(
            prior_meta, fresh, measured=set(mask), skipped=skipped, present=set(merged)
        )
        print(f"wrote {len(mask)} measured, {len(merged) - len(mask)} preserved")
        mask = merged
    else:
        meta = fresh
    write_mask(out, mask, skipped, meta)
    print(
        f"\nwrote {out} ({len(mask)} azimuths, {len(skipped)} skipped); review frames in {frames}/"
    )
    if not args.no_export and mask:
        hrz, txt = export_all(out, os.path.splitext(out)[0])
        print(f"exported {hrz} and {txt}")
    if aborted is not None:
        # Non-zero exit: the mask on disk is real but incomplete, and nothing
        # downstream should treat a truncated sweep as a finished one.
        print(f"\nSWEEP ABANDONED: {aborted}", file=sys.stderr)
        print("the partial mask above was saved; re-run to cover the rest", file=sys.stderr)
        raise SystemExit(2)


def cmd_export(sc, cfg, args):  # sc unused; export is offline
    allow = getattr(args, "allow_unoriented", False)
    base = os.path.splitext(args.mask)[0]
    # The whole command is wrapped, not just the picture half. A read-only
    # directory or a full disk is an ordinary thing to hit and reached the user
    # as a traceback, while a mask with a typo'd flag got a clean sentence —
    # backwards, since the path is the one with the obvious fix. Wrapping only
    # the new exporters would have left `terminus export` alone still tracing
    # back on exactly the same failure, which is the kind of half-fix that reads
    # as done.
    try:
        _export(args, base, allow)
    except OSError as e:
        raise SeestarError(f"could not write the export beside {args.mask}: {e}") from e


def _export(args, base, allow):
    from .export import load_mask

    hrz, txt = export_all(args.mask, base, allow_unoriented=allow)
    print(f"wrote {hrz}\nwrote {txt}")
    if args.pvsyst:
        from .export import to_pvsyst_hor

        meta, rows = load_mask(args.mask)
        hor = base + ".HOR"
        with open(hor, "w") as f:
            f.write(to_pvsyst_hor(rows, meta, allow_unoriented=allow))
        print(f"wrote {hor} (set rotation Clockwise, north azimuth 0 on import)")
    if not (args.skysafari or args.landscape):
        # Checked here rather than in `_texture`, which this return would skip
        # past. Silently ignoring --texture is the bad outcome: the person
        # believes they rendered a photo-real horizon and got the plain .hrz
        # they already had, with nothing said about it.
        if args.texture or args.coverage:
            raise SeestarError(
                "--texture and --coverage only affect the pictures; "
                "add --skysafari and/or --landscape"
            )
        return

    from .landscape import to_skysafari_png, write_landscape

    meta, rows = load_mask(args.mask)
    texture, coverage = _texture(args)
    if args.skysafari:
        png = to_skysafari_png(
            rows, base + ".skysafari.png", meta, texture=texture, coverage=coverage,
            allow_unoriented=allow,
        )  # fmt: skip
        print(f"wrote {png} (Settings -> Horizon & Sky -> Panoramic Image)")
    if args.landscape:
        d = write_landscape(
            base + "_landscape", rows, meta, name=os.path.basename(base),
            texture=texture, coverage=coverage, allow_unoriented=allow,
        )  # fmt: skip
        kind = "spherical" if texture is not None else "polygonal"
        print(f"wrote {d}/ ({kind}); copy it into Stellarium's landscapes/ folder")


def _texture(args):
    """The mosaic panorama and its coverage, or (None, None) for a silhouette.

    Coverage is optional but strongly wanted with a texture: without it every
    pixel below the horizon is drawn opaque, including the parts of the canvas
    the phone never photographed, which turns a gap in the data into black
    ground the viewer has no reason to doubt.
    """
    if not args.texture:
        if args.coverage:
            raise SeestarError("--coverage describes a --texture; pass one or neither")
        return None, None
    import numpy as np
    from PIL import Image

    # Lifted for the same reason as in `cmd_mosaic`: a full-sphere panorama is
    # legitimately past Pillow's bomb threshold, and the file is the user's own
    # mosaic rather than something fetched.
    Image.MAX_IMAGE_PIXELS = None
    try:
        with Image.open(args.texture) as im:
            texture = np.asarray(im.convert("RGB"))
    except OSError as e:
        # Covers a missing file, a directory, a truncated PNG and a file that is
        # not an image at all — UnidentifiedImageError subclasses OSError.
        raise SeestarError(f"could not read the texture {args.texture}: {e}") from e
    if not args.coverage:
        print(
            "warning: --texture without --coverage; uncovered sky will be drawn "
            "as ground. Pass the mosaic's .coverage.npy to show it as a gap.",
            file=sys.stderr,
        )
        return texture, None
    try:
        coverage = np.load(args.coverage)
    except (OSError, ValueError) as e:
        raise SeestarError(f"could not read the coverage {args.coverage}: {e}") from e
    if coverage.shape != texture.shape[:2]:
        raise SeestarError(
            f"coverage {coverage.shape} does not match texture {texture.shape[:2]}; "
            "they must come from the same mosaic run"
        )
    return texture, coverage


def cmd_orient(sc, cfg, args):
    try:
        _orient(sc, cfg, args)
    except OSError as e:
        raise MaskError(f"could not read or write beside {args.mask}: {e}") from e


def _orient(sc, cfg, args):
    """Solve where the photo horizon sits on the sky, one chosen column at a time.

    Two measurement sources, and the loop cannot tell them apart. `--replay`
    re-judges a saved sweep's profiles, which needs no telescope and no night —
    that is how this was built and how a planner change gets compared against a
    real run. Without it the scope measures, Sun-guarded like every other slew.
    """
    from . import guide
    from .export import load_mask, write_mask

    meta, rows = load_mask(args.mask)
    if not rows:
        raise MaskError(f"{args.mask} has no columns to orient")

    if args.replay:
        measure = _replay_source(args)
        reachable = should_stop = None
        print(f"replaying {args.replay}: no telescope, no sky")
    else:
        measure, reachable, should_stop = _scope_measure(sc, cfg, args)

    solution, steps = guide.run(
        rows,
        measure,
        seed=args.seed,
        max_columns=args.max_columns,
        window=args.window,
        yaw_tol=args.yaw_tol,
        reachable=reachable,
        should_stop=should_stop,
        min_headroom=args.min_headroom,
    )
    if solution is None:
        raise MaskError(
            f"only {sum(1 for s in steps if s.fiducial)} columns were measured and the fit "
            "needs four. Nothing was written — a mask oriented on too little is worse than "
            "one that says it is not oriented."
        )
    settled = not any(s.note == "did not settle" for s in steps)
    print(
        f"\nyaw {solution['yaw']:.2f}  pitch {solution['pitch']:.2f}  "
        f"tilt {solution['tilt_mag']:.2f} toward {solution['tilt_dir']:.1f}  "
        f"rms {solution['rms']:.2f} over {solution['n']} columns "
        f"({solution['n_bound']} of them bounds)"
    )
    worst = sorted(solution["residuals"].items(), key=lambda kv: -abs(kv[1]))[:3]
    if worst:
        print("largest residuals: " + ", ".join(f"az {a:g} {r:+.1f}" for a, r in worst))

    if not settled:
        print(
            "the yaw had not settled, so this mask is written UNORIENTED and will not "
            "export until you decide it is good enough (--allow-unoriented)",
            file=sys.stderr,
        )

    out = args.out or os.path.splitext(args.mask)[0] + "_oriented.yaml"
    oriented = guide.orient_mask(rows, solution)
    write_mask(
        out,
        {int(round(az)): {"alt": alt, "type": t} for az, alt, t in oriented},
        [],
        dict(
            meta,
            # NOT unconditionally True. Every exporter gates on this flag through
            # `require_oriented`, and `fit_settled` was write-only — nothing read
            # it — so a run that stopped with the yaw still moving wrote a mask
            # that exported silently, with no --allow-unoriented needed. The
            # warning went to stderr and the file said nothing. Reusing the flag
            # the enforcement already keys on is the whole fix: a provisional
            # orientation now refuses to export until someone says they know.
            oriented=settled,
            yaw=round(solution["yaw"], 3),
            pitch=round(solution["pitch"], 3),
            tilt_mag=round(solution["tilt_mag"], 3),
            tilt_dir=round(solution["tilt_dir"], 3),
            fit_rms=round(solution["rms"], 3),
            fit_columns=sorted(solution["residuals"]),
            fit_settled=settled,
            source_mask=os.path.abspath(args.mask),
        ),
    )
    print(
        f"wrote {out} "
        + (
            "(now in TRUE azimuth; export it)"
            if settled
            else "(marked UNORIENTED: the yaw was still moving when the run stopped)"
        )
    )


def _replay_source(args):
    """`measure(az)` from a saved sweep's profiles, with its failures explained.

    A missing file and a file of the wrong shape are the two most likely things
    to happen here — the path is typed by hand and the JSON is hand-editable —
    and both reached the user as tracebacks while a mask with too few columns
    got a clean sentence.
    """
    import json

    from . import guide

    try:
        with open(args.replay) as f:
            profiles = json.load(f)
    except OSError as e:
        raise MaskError(f"could not read the profiles {args.replay}: {e}") from e
    except ValueError as e:
        raise MaskError(f"{args.replay} is not valid JSON: {e}") from e
    if not isinstance(profiles, dict) or not profiles:
        raise MaskError(
            f"{args.replay} should be an object of azimuth -> [[alt, lum], ...], as "
            f"`terminus sweep` writes beside its mask; got {type(profiles).__name__}"
        )
    try:
        return guide.replay(profiles, uncertainty=args.uncertainty)
    except (TypeError, ValueError) as e:
        raise MaskError(f"{args.replay} is not shaped like a sweep's profiles: {e}") from e


def _scope_measure(sc, cfg, args):
    """A `measure(az)` that points the telescope, and the reachability predicate.

    Separated so the loop never learns which source it has. The predicate is
    injected rather than imported by `plan`, because feasibility is Sun geometry
    and changes with the clock — see `plan.partition`.
    """
    if not sc.is_eq_mode() and not args.dry_run:
        raise SeestarError("not in EQ mode; terminus needs a polar-aligned EQ mount")
    sky = _sky(sc, cfg)
    sw = cfg["sweep"]
    ptr = Pointer(sc, sky, sw["sun_cone_deg"], sw["slew_step_deg"], args.dry_run)
    state = {"sky_ref": None, "misses": 0}

    # Seeded before the first column, exactly as run_sweep does. Without it
    # scan_horizon returns "no_reference" for EVERY column — it cannot tell dark
    # terrain from a dim sky with nothing to compare against — and an
    # inconclusive column that was then recorded as a bound would feed the fit a
    # measurement nobody made.
    if not args.dry_run:
        try:
            saz, _ = sky.sun()
            ptr.point_to((saz + 180.0) % 360.0, 75.0)
            state["sky_ref"] = sky_reference(sc.capture_rgb(warmup=0.3))
            print(f"sky reference: {state['sky_ref']:.1f}")
        except (SunGuard, PointingError, OSError) as e:
            print(f"could not seed the sky reference ({e}); columns will be inconclusive",
                  file=sys.stderr)  # fmt: skip

    def measure(az):
        try:
            alt, status, _typ, profile = scan_horizon(
                ptr, sc, az, sw["alt_min"], sw["alt_max"], sw.get("coarse_step", 5.0),
                sw["alt_tol"], state["sky_ref"], repeats=sw.get("samples_per_point", 1),
                sun_alt=sky.sun()[1],
            )  # fmt: skip
        except SunGuard as e:
            print(f"az {az:3d}: skipped ({e})", file=sys.stderr)
            return None
        except PointingError as e:
            state["misses"] += 1
            print(f"az {az:3d}: did not arrive ({e})", file=sys.stderr)
            if state["misses"] >= MAX_POINTING_MISSES:
                raise SeestarError(
                    f"{state['misses']} slews in a row did not arrive. A mount that cannot "
                    "point will fail every remaining column too, and skipping them one by "
                    "one would spend the night proving it. Check the arm is open and the "
                    "mount is tracking."
                ) from e
            return None
        state["misses"] = 0
        if profile:
            peak = max(lum for _, lum in profile)
            state["sky_ref"] = peak if state["sky_ref"] is None else max(state["sky_ref"], peak)
        if status in ("no_reference", "inconclusive"):
            # The column was pointed at and photographed, and the result does not
            # say where the horizon is. Recording that as a bound would turn "I
            # could not tell" into "it is at least 60 degrees" — an unevidenced
            # value in a file the fit trusts. Not attempted is the honest shape.
            print(f"az {az:3d}: {status}", file=sys.stderr)
            return None
        if status.startswith("open"):
            # Open all the way to the search floor. That is a real constraint —
            # the horizon is at or BELOW alt_min — but it is the opposite
            # one-sided constraint from a ceiling bound, and `Fiducial` can only
            # express "at least this high". Recording it as an exact edge AT the
            # floor invents a measurement: "found nothing between 0 and 60" is
            # not "the horizon is at 0". `orient.from_mask` reaches the same
            # conclusion about the same status, and excludes it.
            #
            # So it is dropped, which loses information rather than inventing it.
            # terminus-50 tracks giving `Fiducial` a downward bound so the fit
            # can use these columns honestly.
            print(f"az {az:3d}: open to the search floor (no constraint the fit can use)",
                  file=sys.stderr)  # fmt: skip
            return None
        if status == "blocked_above":
            return None, sw["alt_max"], args.uncertainty
        return {"alt": alt, "snr": None}, sw["alt_max"], args.uncertainty

    def reachable(az):
        """Is this column worth PLANNING for? Endpoint geometry, not the slew.

        Deliberately the weaker `column_touches_sun` rather than sweep's
        path-aware `reachable_now`. This predicate only decides which candidates
        the D-optimality criterion gets to choose between, and the real slew is
        still refused by `Pointer.point_to`, which checks every waypoint. Using
        the path-aware test here would additionally depend on where the tube
        currently IS, so the candidate set would change with the order columns
        happened to be measured in — a planner whose answer depends on its own
        history is harder to reason about than one that occasionally proposes a
        column the mount then declines.
        """
        return not column_touches_sun(sky, az, sw["alt_min"], sw["alt_max"], sw["sun_cone_deg"])

    def should_stop():
        """Stop before a column that would run past the observing window.

        terminus-17: the cutoff was enforced by the caller, before launch, and a
        run overran by fourteen minutes because each column took longer than
        estimated — the sky brightened toward dawn, more columns resolved, and
        the run slowed exactly as the deadline approached. A caller starting an
        N-column run cannot know how long N columns take, and the estimate
        degrades in the direction that matters. So it is checked here, before
        each column, where the answer is current.
        """
        if args.stop_above_sun_alt is None:
            return False
        _, alt = sky.sun()
        if alt >= args.stop_above_sun_alt:
            print(
                f"stopping: the Sun is at {alt:.1f} deg, at or above the "
                f"{args.stop_above_sun_alt:g} deg cutoff for this run",
                file=sys.stderr,
            )
            return True
        return False

    return measure, reachable, should_stop


# ---- offline photo pipeline -----------------------------------------------
# Neither command touches the scope, the network, or config.toml. They are the
# desktop half: build a panorama from photographs, then read a horizon off it.
def cmd_mosaic(sc, cfg, args):  # sc, cfg unused: offline
    import numpy as np
    from PIL import Image

    from . import mosaic

    mosaic.require_hugin()  # raises MosaicError naming what to install
    work = args.work or os.path.join(args.image_dir.rstrip("/") + "_mosaic")
    os.makedirs(work, exist_ok=True)
    base = args.out or os.path.join(work, "equirect")

    pto, dropped = mosaic.solve(
        args.image_dir,
        work,
        lens=args.lens,
        min_points=args.min_points,
        celeste=not args.no_celeste,
    )
    counts = mosaic.control_point_counts(pto)
    kept = [n for n in counts if n not in dropped]
    print(f"registered {len(kept)} frames, dropped {len(dropped)}")
    # Report every dropped frame and its count. A frame that cannot be
    # constrained is silently missing sky, and autooptimiser will happily place
    # one with zero control points — that is how a garage umbrella ended up in
    # the sky and was blamed on the classifier.
    for name in sorted(dropped):
        print(
            f"  dropped {name}: {counts.get(name, 0)} control points " f"(need {args.min_points})"
        )
    if not kept:
        raise mosaic.MosaicError("no frame could be constrained; nothing to render")

    tiffs, final = mosaic.render(pto, work, width=args.width, height=args.height)
    img, coverage, gains = mosaic.composite(tiffs, args.width, args.height)
    Image.fromarray(img).save(base + ".png")
    np.save(base + ".coverage.npy", coverage)
    mosaic.write_manifest(
        base + ".manifest.json",
        image_dir=os.path.abspath(args.image_dir),
        pto=os.path.abspath(final),
        width=args.width,
        height=args.height,
        kept=sorted(kept),
        dropped=sorted(dropped),
        control_points=counts,
        min_points=args.min_points,
        gains={n: float(g) for n, g in zip(sorted(tiffs), gains, strict=False)},
    )
    covered = float((coverage > 0).any(axis=0).mean()) * 100.0
    print(f"wrote {base}.png ({args.width}x{args.height}, {covered:.0f}% of azimuth covered)")
    print(f"wrote {base}.coverage.npy and {base}.manifest.json")


def _type_name(cls):
    """ADE20K class -> the mask's vocabulary. -1 means nothing was segmented."""
    from .skymask import VEG_CLASSES_ADE20K

    if cls < 0:
        return ""
    return "tree" if cls in VEG_CLASSES_ADE20K else "structure"


def cmd_skymask(sc, cfg, args):  # sc, cfg unused: offline
    import numpy as np
    from PIL import Image

    from . import skymask
    from .export import write_mask

    Image.MAX_IMAGE_PIXELS = None
    image = Image.open(args.image).convert("RGB")
    w, h = image.size
    # Deliberately NOT resolved here. Pre-resolving 'auto' to 'segment' turns it
    # into an EXPLICIT request, which sky_mask is right to fail loudly on — so
    # the CLI would have defeated the very fallback it wants.
    if args.backend == "segment" and not skymask.available("segment"):
        raise SeestarError(
            "the segment backend needs torch, torchvision and transformers.\n"
            "Install them, or pass --backend heuristic (markedly worse: colour "
            "rules read an off-white wall as sky)."
        )
    print(f"backend requested: {args.backend} ({w}x{h})")

    valid = None
    if args.coverage:
        valid = np.load(args.coverage) > 0
        if valid.shape != (h, w):
            raise SeestarError(
                f"coverage {valid.shape} does not match image {(h, w)}; "
                "it must come from the same mosaic run"
            )

    sky, backend = skymask.sky_mask(image, backend=args.backend, report=True)
    # `backend` is now what RAN, not what was asked for. Everything below keys
    # off it — the printed line, the segmentation-only type pass, and the mask
    # meta — so a silent fallback cannot be recorded as a segment run.
    band = skymask.horizon_band(sky, valid=valid, run=args.run)
    px_per_deg = w / 360.0
    top = skymask.upper_envelope(band["top"], half_deg=args.envelope, px_per_deg=px_per_deg)

    classes = np.full(w, -1, dtype=int)
    print(f"backend used: {backend}")
    if backend == "segment":
        classes = skymask.obstruction_classes(
            skymask.segment_classes(image), band["top"], valid=valid
        )
    unc = skymask.type_uncertainty(classes, band["gap_fraction"])

    step = max(1, int(round(args.az_step * px_per_deg)))
    mask, clipped_n = {}, 0
    for x in range(0, w, step):
        if not np.isfinite(top[x]):
            continue
        az = int(round(x / px_per_deg)) % 360
        alt = 90.0 - (float(top[x]) / h) * 180.0
        is_clipped = bool(band["clipped"][x])
        clipped_n += is_clipped
        typ = _type_name(int(classes[x]))
        mask[az] = {
            "alt": round(alt, 2),
            "type": typ,
            # Only claim a source when there is a type to source. The heuristic
            # backend segments nothing, so `classes` is -1 throughout and every
            # column would otherwise be stamped "photo" for a type it never got.
            "type_source": "photo" if typ else None,
            "clipped": is_clipped,
            "gap_fraction": round(float(band["gap_fraction"][x]), 3),
            "uncertainty": round(float(unc[x]), 2),
        }
    if not mask:
        raise SeestarError("no column yielded a horizon; check the image and --coverage")

    out = args.out or os.path.splitext(args.image)[0] + "_mask.yaml"
    write_mask(
        out,
        mask,
        [],
        {
            "source": os.path.abspath(args.image),
            "backend": backend,
            "oriented": False,  # native panorama azimuth; yaw is not known yet
            "az_step": args.az_step,
            "width": w,
            "height": h,
        },
    )
    print(f"wrote {out}: {len(mask)} columns, {clipped_n} clipped")
    print(
        "NOTE: azimuth is the panorama's own, NOT true north. It must be oriented\n"
        "      against telescope-measured columns before a planner uses it."
    )
    print("not exporting: an unoriented mask must not be handed to a planner")


NEEDS_SCOPE = {"preflight", "point", "classify", "sweep"}
# Offline: no scope, no network, and no config.toml — a user with photographs
# and no telescope must not be made to write one.
# `export` belongs here too: it reads a mask file and writes two more, and
# never looks at cfg. Demanding config.toml for it meant a machine with no
# telescope could not re-export its own mask — and it was CI, which has no
# config.toml, that surfaced this rather than any local run.
OFFLINE = {"mosaic", "skymask", "export"}


def _is_offline(args):
    """Does this invocation need neither scope nor config.toml?

    `orient` is the one command that answers differently depending on its flags:
    with `--replay` it re-judges a saved night and touches nothing, without it
    the telescope measures. Deciding by subcommand alone would force a config
    file on someone replaying a run on a laptop, which is precisely the case
    replay exists to serve.
    """
    if args.cmd == "orient":
        return bool(args.replay)
    return args.cmd in OFFLINE


def _needs_scope(args):
    if args.cmd == "orient":
        return not args.replay
    return args.cmd in NEEDS_SCOPE


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="terminus", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--config", default="config.toml")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("preflight")
    pp = sub.add_parser("point")
    pp.add_argument("az", type=float)
    pp.add_argument("alt", type=float)
    pp.add_argument("--dry-run", action="store_true")
    sub.add_parser("classify")
    sw = sub.add_parser("sweep")
    sw.add_argument("--az-start", type=float, default=0)
    sw.add_argument("--az-end", type=float, default=350)
    sw.add_argument("--out", default=None)
    sw.add_argument("--frames", default=None)
    sw.add_argument(
        "--azimuths",
        default=None,
        help="comma list of azimuths to measure instead of a range; "
        "merges into --out if it exists",
    )
    sw.add_argument("--no-export", action="store_true")
    sw.add_argument("--dry-run", action="store_true")
    orp = sub.add_parser(
        "orient", help="solve where a photo horizon sits on the sky, column by column"
    )
    orp.add_argument("mask", help="the UNORIENTED photo mask from `terminus skymask`")
    orp.add_argument("--out", help="where to write the oriented mask")
    orp.add_argument(
        "--replay",
        help="re-judge a saved sweep's <mask>_profiles.json instead of observing "
        "(no telescope, no night)",
    )
    orp.add_argument("--seed", type=int, default=4, help="evenly spaced starting columns")
    orp.add_argument("--max-columns", type=int, default=12)
    orp.add_argument("--window", type=int, default=3, help="refits the yaw must hold still across")
    orp.add_argument("--yaw-tol", type=float, default=1.0, help="degrees, the stability rule")
    orp.add_argument(
        "--uncertainty",
        type=float,
        default=None,
        help="degrees a measured boundary may move; standardises the residual",
    )
    orp.add_argument(
        "--min-headroom",
        type=float,
        default=None,
        help="drop columns whose edge sits this close to their own search ceiling",
    )
    orp.add_argument(
        "--stop-above-sun-alt",
        type=float,
        default=None,
        help="stop before any column once the Sun reaches this altitude, checked "
        "inside the loop (try -18 for astronomical twilight)",
    )
    orp.add_argument(
        "--dry-run",
        action="store_true",
        help="rehearse the POINTING only. It cannot measure — every column reports "
        "open to the search floor and is dropped, so the run ends with too few "
        "columns to fit. Use --replay to rehearse the whole loop.",
    )

    ex = sub.add_parser("export")
    ex.add_argument("mask")
    ex.add_argument("--skysafari", action="store_true", help="also write a Sky Safari panorama PNG")
    ex.add_argument("--pvsyst", action="store_true", help="also write a PVsyst .HOR solar profile")
    ex.add_argument(
        "--landscape", action="store_true", help="also write a Stellarium landscape directory"
    )
    ex.add_argument("--texture", help="equirectangular panorama (the mosaic PNG) to draw")
    ex.add_argument("--coverage", help="the mosaic's .coverage.npy, so gaps render as sky")
    ex.add_argument(
        "--allow-unoriented",
        action="store_true",
        help="export a mask whose azimuth is not yet true north",
    )

    mo = sub.add_parser("mosaic", help="register photographs into an equirectangular panorama")
    mo.add_argument("image_dir")
    mo.add_argument("--work", default=None, help="scratch dir (default: <image_dir>_mosaic)")
    mo.add_argument("--out", default=None, help="output basename (default: <work>/equirect)")
    mo.add_argument("--lens", type=float, default=None, help="horizontal FOV hint, degrees")
    mo.add_argument(
        "--min-points",
        type=int,
        default=MIN_CONTROL_POINTS,
        help="drop a frame below this many control points",
    )
    mo.add_argument("--width", type=int, default=2880)
    mo.add_argument("--height", type=int, default=1440)
    mo.add_argument("--no-celeste", action="store_true", help="skip cpfind's sky filter")

    sk = sub.add_parser("skymask", help="read a horizon off a panorama (UNORIENTED)")
    sk.add_argument("image")
    sk.add_argument("--backend", choices=("auto", "segment", "heuristic"), default="auto")
    sk.add_argument(
        "--coverage",
        default=None,
        help="the .coverage.npy from `terminus mosaic`; without it, "
        "uncovered pixels read as terrain",
    )
    sk.add_argument("--out", default=None)
    sk.add_argument("--az-step", type=float, default=1.0)
    sk.add_argument(
        "--run",
        type=int,
        default=6,
        help="rows of sustained non-sky before the skyline is believed",
    )
    sk.add_argument(
        "--envelope", type=float, default=2.0, help="half-width in degrees for the upper envelope"
    )
    for sp in (sub.choices["preflight"], sub.choices["classify"]):
        sp.add_argument("--dry-run", action="store_true")  # harmless, keeps a uniform namespace
    args = p.parse_args(argv)
    args.dry_run = getattr(args, "dry_run", False)

    handlers = {
        "preflight": cmd_preflight,
        "point": cmd_point,
        "classify": cmd_classify,
        "sweep": cmd_sweep,
        "export": cmd_export,
        "mosaic": cmd_mosaic,
        "skymask": cmd_skymask,
        "orient": cmd_orient,
    }
    try:
        cfg = {} if _is_offline(args) else load_config(args.config)
        sc = _connect(cfg) if _needs_scope(args) else None
        try:
            handlers[args.cmd](sc, cfg, args)
        finally:
            if sc:
                sc.close()
    except (ConfigError, SeestarError, MosaicError, MaskError) as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
