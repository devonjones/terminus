"""terminus command line: measure a local horizon with a Seestar and export it.

  terminus preflight            connect, show mode + Sun, list Sun-blocked azimuths
  terminus point AZ ALT         goto one az/alt (Sun-guarded), verify the landing
  terminus classify             capture at the current pointing, report sky/veg/structure
  terminus sweep                full horizon sweep -> mask YAML (+ review frames)
  terminus export MASK          write N.I.N.A. .hrz and Stellarium .txt from a mask
  terminus polar MASK           one-page fisheye view of the horizon, layers toggleable

Global: --config PATH (default ./config.toml). sweep: --az-start --az-end --out
--frames --no-export --dry-run.
"""

import argparse
import json
import math
import os
import sys
import time

from . import polar
from .client import Seestar, SeestarError
from .config import ConfigError, load_config
from .export import (
    MaskError,
    _column,
    default_meta,
    export_all,
    is_oriented,
    load_columns,
    write_mask,
)
from .mosaic import MIN_CONTROL_POINTS, MosaicError
from .sweep import (
    DAY_REF_FLOOR,
    MAX_POINTING_MISSES,
    NIGHT_SUN_ALT,
    Pointer,
    PointingError,
    Sky,
    SunGuard,
    classify,
    column_touches_sun,
    night_find_edge,
    obstruction_type,
    run_sweep,
    scan_horizon,
    scan_horizon_night,
    set_channel,
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


def _start_locked(sc, sw):
    """Start the scenery view with exposure locked, in the order that works.

    The ordering knowledge (lock only while the view runs; cycle the view to
    clear an old lock) lives on `sweep.set_channel`, which owns channel
    switching for the sweep's own day/night transitions. This wrapper is the
    day-only entry point the CLI commands share.
    """
    set_channel(sc, night=False, sw=sw)


def is_stowed(rd):
    """Is the mount parked with its arm closed?

    Devon: Dec -90 tells you it is stowed. The stow position IS the south
    celestial pole, so this is readable from the pointing alone — no second call,
    and true whatever the firmware chooses to report elsewhere.

    Worth checking because the failure it causes is so misleading. A stowed mount
    answers every query happily and simply never moves, so each goto waits out
    GOTO_TIMEOUT and reports "did not arrive; mount may be closed, parked, or not
    tracking". Three of those in a row trip MAX_POINTING_MISSES and abandon the
    run. On 2026-08-05 that reading cost a session, and this morning it cost ten
    minutes before the Dec was noticed.
    """
    return rd is not None and abs(abs(float(rd[1])) - 90.0) < 0.5


def _pointer(sc, sky, sw, dry):
    """A Pointer with the mount's below-horizon floor applied.

    How far below the horizon this mount can point is not known — it was seen at
    -1.1 degrees and no further, which proves only that below the horizon is
    reachable. The safe-transit corridor needs more than that whenever the Sun is
    low, so the floor is configurable and defaults to something barely past what
    was observed, rather than to a number that would be convenient. Measure it
    and set `min_alt_deg`; see terminus-64.
    """
    ptr = Pointer(sc, sky, sw["sun_cone_deg"], sw["slew_step_deg"], dry)
    if sw.get("min_alt_deg") is not None:
        ptr.MIN_ALT_DEG = float(sw["min_alt_deg"])
    return ptr


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
        if is_stowed(rd):
            print(
                "  !! STOWED — the arm is closed and the mount will not slew. Every goto will\n"
                "     report 'did not arrive' after waiting out its timeout, which reads like a\n"
                "     broken mount and is not one. Open the arm in the Seestar app first."
            )
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
    ptr = _pointer(sc, sky, cfg["sweep"], args.dry_run)
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


def _sun_deadline(sky, stop_above):
    """A `should_stop()` that ends a run once the Sun reaches `stop_above`.

    None when no cutoff was asked for, so the caller passes None and nothing is
    checked. The Sun is read fresh each time BECAUSE that is the whole point:
    terminus-17 was a run enforcing its deadline before launch and overrunning
    it by fourteen minutes, and the reason it overran is that the sky brightened
    toward dawn, more columns resolved, and it slowed down exactly as the
    deadline approached. An estimate made at the start degrades in the direction
    that matters.
    """
    if stop_above is None:
        return None

    def should_stop():
        _, alt = sky.sun()
        if alt >= stop_above:
            print(
                f"the Sun has reached {alt:.1f} deg, at or above the {stop_above:g} deg "
                "cutoff for this run",
                file=sys.stderr,
            )
            # THE DEADLINE REMEMBERS THAT IT FIRED, so a caller can tell a
            # truncated run from a finished one. Without this the two are
            # indistinguishable downstream: `run_sweep` returns normally either
            # way, the mask is written and exported the same, and the process
            # exits 0. A scheduler — the deployment this flag exists for — could
            # not tell "the window closed and columns are missing" from "the
            # sweep measured everything asked of it" without parsing log text.
            should_stop.fired = True
            return True
        return False

    should_stop.fired = False

    return should_stop


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
    # `stopped_early` describes THIS run, so it is neither inherited nor dropped.
    # Starting from `dict(prior_meta)` got it wrong in both directions: a mask
    # once truncated stayed flagged forever however many complete patches
    # followed, and a patch that WAS truncated lost the flag entirely because
    # `fresh` is discarded on this path. Same shape as the `skipped_az` bug this
    # function was written for — meta contradicting the data it describes.
    if fresh.get("stopped_early"):
        meta["stopped_early"] = True
    else:
        meta.pop("stopped_early", None)
    return meta


def cmd_sweep(sc, cfg, args):
    if not sc.is_eq_mode() and not args.dry_run:
        raise SeestarError("not in EQ mode; terminus needs a polar-aligned EQ mount")
    sky = _sky(sc, cfg)
    out = args.out or "horizon_mask.yaml"
    frames = args.frames or (os.path.splitext(out)[0] + "_frames")
    # The view is NOT started here: run_sweep owns the channel now, choosing
    # scenery or the star-mode imaging channel per column from the Sun's
    # altitude, and re-choosing at the twilight boundary mid-run.
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
    # Bound to a name rather than passed inline: after the run we ask it whether
    # it fired, which is how a truncated sweep is told from a finished one.
    should_stop = _sun_deadline(sky, getattr(args, "stop_above_sun_alt", None))
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
            should_stop=should_stop,
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
    # A run the window closed on is NOT a finished sweep, and the mask has to say
    # so itself. The log line is not enough: the file outlives the terminal, and
    # the next tool to read it — or future-you — has no other way to know that
    # the missing azimuths are missing because time ran out rather than because
    # nobody asked for them.
    stopped_early = bool(getattr(should_stop, "fired", False))
    if stopped_early:
        fresh["stopped_early"] = True
    # The scope reads type from colour, so a column it typed was typed in
    # daylight — after sunset `obstruction_type` returns "" rather than guessing.
    # Stamped here rather than in run_sweep so its (alt, type) contract, which
    # several callers and tests depend on, stays as it was.
    mask = {
        az: dict(_column(col), type_source=("scope" if _column(col)["type"] else None))
        for az, col in mask.items()
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
    if stopped_early:
        # A DISTINCT code, because it is a distinct outcome. 2 means the mount
        # stopped answering and the run was abandoned; 3 means the run did
        # exactly what it was told and the clock beat it. A scheduler should
        # retry the second tomorrow night, not treat it as a fault.
        print(
            "\nSWEEP INCOMPLETE: the observing window closed before every column", file=sys.stderr
        )
        print(
            f"the {len(mask)} columns measured were saved; re-run to cover the rest",
            file=sys.stderr,
        )
        raise SystemExit(3)


def cmd_polar(sc, cfg, args):  # sc unused; polar is offline
    """Write the fisheye page. Reads the mask; touches no hardware.

    The photograph is OPTIONAL and the command degrades rather than refuses: a
    scope-only sweep still gets a disc with its horizon on it, which is the same
    bargain `landscape.py` strikes between its synthetic and photo modes. What it
    will not do is draw an unoriented mask without being told to, because a
    fisheye labelled N/E/S/W is a much stronger claim about north than a column
    listing is, and it is the one people screenshot.
    """
    import numpy as np
    from PIL import Image

    from . import orient as orient_mod
    from . import polar
    from .export import is_oriented, load_mask

    meta, rows = load_mask(args.mask)
    if not is_oriented(meta) and not args.allow_unoriented:
        raise MaskError(
            f"{args.mask} is not oriented: its azimuth is the panorama's own, not true north.\n"
            "Run `terminus orient` first, or pass --allow-unoriented to draw it anyway "
            "(the compass labels will be wrong)."
        )
    solution = polar.solution_from_meta(meta)

    image = coverage = None
    source = args.image or meta.get("source")
    if source and os.path.exists(source):
        Image.MAX_IMAGE_PIXELS = None
        image = Image.open(source).convert("RGB")
        cov_path = args.coverage
        if cov_path is None:
            # `terminus mosaic` writes <base>.png beside <base>.coverage.npy, so
            # the sibling is a good guess and a missing one is not an error —
            # it only costs the "not photographed" layer.
            guess = os.path.splitext(source)[0] + ".coverage.npy"
            cov_path = guess if os.path.exists(guess) else None
        if cov_path:
            coverage = np.load(cov_path)
        else:
            coverage = np.ones(np.asarray(image).shape[:2])
            print("no coverage array: every pixel will be treated as photographed", file=sys.stderr)
    elif source:
        print(f"panorama {source} not found; drawing the horizon without it", file=sys.stderr)

    fiducials = ()
    if args.fiducials:
        fid_meta, _ = load_mask(args.fiducials)
        doc = _mask_entries(args.fiducials)
        ceiling = args.ceiling
        if ceiling is None:
            search = fid_meta.get("alt_search") or []
            ceiling = float(search[1]) if len(search) > 1 else None
        try:
            fiducials = orient_mod.from_mask(doc, ceiling=ceiling)
        except ValueError as e:
            # A malformed hand-edited `exclude` gets the clean sentence (E-12).
            raise MaskError(f"{args.fiducials}: {e}") from e
        n_bound = sum(1 for f in fiducials if f.bound)
        print(f"{len(fiducials)} fiducials from {args.fiducials} ({n_bound} at the ceiling)")

    out = args.out or os.path.splitext(args.mask)[0] + "_polar.html"
    _write_or_explain(
        out,
        lambda: polar.write_page(
            out,
            rows,
            solution,
            image=image,
            coverage=coverage,
            fiducials=fiducials,
            meta=meta,
            size=args.size,
            floor=args.floor,
            title=args.title or f"terminus horizon — {os.path.basename(args.mask)}",
        ),  # fmt: skip
    )
    print(f"wrote {out} ({os.path.getsize(out) // 1024} KB, self-contained)")


def _mask_entries(path):
    """The raw `horizon:` block of a mask, as `from_mask` wants it.

    `load_mask` returns rows for drawing; the fit needs the per-column dict with
    its `type` and `bound` fields intact, and re-reading is cheaper than widening
    a return type every caller already unpacks.
    """
    import yaml

    with open(path) as fh:
        doc = yaml.safe_load(fh) or {}
    block = doc.get("horizon") or doc.get("mask") or {}
    return {int(az): entry for az, entry in block.items()}


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
    finally:
        # However the run ended. A failure that leaves the instrument streaming
        # is its own small fault, and last night's timeout did exactly that —
        # stop_view had to be issued by hand afterwards.
        if not args.dry_run and sc is not None:
            try:
                sc.stop_view()
            except Exception as e:  # noqa: BLE001 - cleanup must not mask the real error
                print(f"warning: could not stop the view ({e})", file=sys.stderr)


def _write_or_explain(path, write):
    """Run a write, and blame the file only when the file is actually at fault.

    This used to be a blanket `except OSError` around the whole command. A
    socket timeout is an OSError, so a scope that stopped responding mid-run was
    reported as "could not read or write beside photo_mask.yaml" — a file that
    was perfectly fine. Wrapping only the write means the message can name a
    cause it actually knows.
    """
    try:
        write()
    except OSError as e:
        raise MaskError(f"could not write {path}: {e}") from e


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

    fiducials = getattr(args, "fiducials", None)
    excluded_by_mask = {}
    if fiducials:
        measure, reachable, excluded_by_mask = _fiducial_source(fiducials, args.uncertainty)
        should_stop = None
        state = {"profiles": {}}
        print(f"orienting against {len(fiducials)} fiducial mask(s): no telescope, no sky")
    elif args.replay:
        measure = _replay_source(args)
        reachable = should_stop = None
        state = {"profiles": {}}
        print(f"replaying {args.replay}: no telescope, no sky")
    else:
        measure, reachable, should_stop, state = _scope_measure(sc, cfg, args)

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
    from .orient import yaw_uncertainty

    # How well the yaw is actually pinned, with pitch and tilt free to absorb it.
    # Reporting the value alone is what let two runs look like they disagreed by
    # 30 degrees when both were really saying "somewhere around here, give or
    # take fifteen".
    fids = [s.fiducial for s in steps if s.fiducial is not None]
    half = yaw_uncertainty(fids, guide.photo_sample(rows), solution, step=2.0)
    spread = f"+/- {half:.0f} deg" if half else "NOT BOUNDED within 60 deg"
    print(
        f"\nyaw {solution['yaw']:.2f} ({spread})  pitch {solution['pitch']:.2f}  "
        f"tilt {solution['tilt_mag']:.2f} toward {solution['tilt_dir']:.1f}  "
        f"rms {solution['rms']:.2f} over {solution['n']} columns "
        f"({solution['n_bound']} of them bounds, {solution.get('dof', 0):+d} degrees of freedom)"
    )
    if half is None or half > 5.0:
        print(
            "   the yaw is not well determined. More columns, spread widely and chosen\n"
            "   where the horizon is steep, is what narrows it — see the residuals below.",
            file=sys.stderr,
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
    if state.get("profiles"):
        prof_path = os.path.splitext(out)[0] + "_profiles.json"

        def _write_profiles():
            with open(prof_path, "w") as f:
                json.dump({str(az): p for az, p in state["profiles"].items()}, f, indent=1)

        _write_or_explain(prof_path, _write_profiles)
        print(f"wrote {prof_path} ({len(state['profiles'])} columns, replayable)")
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
            # THE SET, not just its size. Without this a rerun cannot reproduce
            # the fit and cannot even tell which columns it disagrees about: the
            # published run used 16 of 30 and nothing on disk says which, so its
            # 0.69 deg is unreachable and incomparable (terminus-53, F-25).
            fit_fiducials=_fit_fiducials_record(solution, excluded_by_mask),
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


def _fit_fiducials_record(solution, excluded_by_mask):
    """The written form of the fit's fiducial set, mask exclusions included.

    Rounded, because this is a record for a person to read and diff, not a
    serialisation format. None-valued fields are OMITTED, reason included: the
    meta this lands in is serialised via repr, where a literal None round-trips
    through YAML as the STRING 'None' — an absent key is the honest spelling of
    "no reason: used cleanly" (E-06). Columns the MASK excluded appear too,
    marked excluded_by, because a record of the fit's inputs that silently
    omits the inputs someone removed is exactly the unreproducibility this
    field exists to end.
    """
    return [
        {k: (round(v, 3) if isinstance(v, float) else v) for k, v in f.items() if v is not None}
        for f in solution.get("fiducials", [])
    ] + [
        {
            "az": float(az),
            **({"alt": round(float(e["alt"]), 3)} if e.get("alt") is not None else {}),
            "used": False,
            "reason": e["reason"],
            "excluded_by": "mask",
        }
        for az, e in sorted(excluded_by_mask.items())
    ]


def _fiducial_source(paths, uncertainty):
    """`measure(az)` from telescope columns that were ALREADY measured.

    The third way in, beside a live sweep and `--replay`. Replay re-judges raw
    brightness profiles, so it needs the profiles; this reads masks that already
    hold judged columns — which is what a finished sweep leaves behind, and what
    the 2026-08-03 evening sweeps are.

    Without it the orientation fit had no CLI path at all: `orient.from_mask` and
    `orient.fit` were reachable only from Python, so the one step that turns a
    photo mask into a true-north one could not be run by the tool that produces
    the mask. PANORAMA-PIPELINE.md says so in as many words.

    Several masks may be given and are merged, first one wins, because a sweep
    is routinely split across arcs and nights — az 70-250 in one file and 260-350
    in another is the shape actually on disk.

    Returns (measure, reachable, excluded). `reachable` confines the planner to
    azimuths a fiducial exists for, so the adaptive chooser still does its real
    job of ordering them by information gain rather than being handed a fixed
    list. `excluded` is {az: {"alt", "reason"}} for the columns the mask itself
    removed — returned rather than merely printed, because the caller writes
    the fit record and an exclusion that never reaches the file defeats the
    record's whole purpose (terminus-53).
    """
    import yaml

    from .orient import CEILING_EPS, exclusion_reason

    columns = {}
    excluded = {}
    for path in paths:
        try:
            with open(path) as fh:
                doc = yaml.safe_load(fh) or {}
        except OSError as e:
            raise MaskError(f"could not read the fiducials {path}: {e}") from e
        block = doc.get("horizon") or doc.get("mask") or {}
        if not block:
            raise MaskError(
                f"{path} has no `horizon:` block. --fiducials wants a mask a sweep wrote, "
                "not a profiles.json (that is --replay)."
            )
        search = (doc.get("meta") or {}).get("alt_search") or []
        ceiling = float(search[1]) if len(search) > 1 else None
        for az, entry in block.items():
            # FIRST FILE TO SPEAK ABOUT AN AZIMUTH WINS — whatever it said.
            # `columns` alone had first-wins while `excluded` overwrote, so two
            # masks disagreeing about one column left it simultaneously excluded
            # and live, and the written record carried both verdicts (round 2's
            # P1). The two maps are one namespace: a column is a measurement OR
            # an exclusion, never both, and the earlier file's verdict stands —
            # exactly the rule the accepted columns already follow.
            if int(az) in columns or int(az) in excluded:
                continue
            # AN EXCLUDED COLUMN IS NOT A CANDIDATE. Leaving it in and refusing
            # it at measure time makes the planner spend a slot discovering what
            # the mask already said — and `reachable` would be lying, which is
            # the ordering SAFE-03 exists to get right: filter for feasibility
            # BEFORE ranking, so the criterion picks the best feasible
            # configuration rather than the best infeasible one plus a fallback.
            try:
                reason = exclusion_reason(entry)
            except ValueError as e:
                # The typo gets the clean sentence too (E-12): main() reports
                # MaskError; a bare ValueError would be the one failure with a
                # five-second fix arriving as a raw traceback.
                raise MaskError(f"{path}: az {az}: {e}") from e
            if reason is not None:
                excluded[int(az)] = {"alt": entry.get("alt"), "reason": reason}
                continue
            columns[int(az)] = (entry, ceiling)

    def measure(az):
        got = columns.get(int(az))
        if got is None:
            return None
        entry, ceiling = got
        alt = entry.get("alt")
        typ = str(entry.get("type", "") or "")
        if alt is None or typ == "unknown":
            return None  # measured and found nothing; not an edge, not a bound
        if typ == "open":
            # Open to the search floor bounds the horizon from BELOW, which
            # Fiducial cannot express. Scoring it as an exact edge at the floor
            # was worth 40 degrees of yaw on real data.
            return None
        alt = float(alt)
        # A column at its ceiling is a BOUND however it is typed: the scope
        # cannot tilt past its search ceiling, so the horizon is at least that
        # high, not exactly that high (M-09). Masks written before the explicit
        # `bound` field record these as ordinary edges.
        bound = (
            bool(entry.get("bound", False))
            or bool(entry.get("clipped", False))
            or typ.startswith("blocked")
            or (ceiling is not None and alt >= ceiling - CEILING_EPS)
        )
        if bound:
            return None, alt, uncertainty
        return {"alt": alt}, ceiling, uncertainty

    if excluded:
        for az, exc in sorted(excluded.items()):
            short = " ".join(exc["reason"].split())
            if len(short) > 88:
                short = short[:85] + "..."
            print(f"az {az:3d}: excluded by the mask — {short}", file=sys.stderr)
    return measure, (lambda az: int(az) in columns), excluded


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
    if not args.dry_run and is_stowed(sc.equ_coord()):
        raise SeestarError(
            "the mount is stowed (Dec -90) and will not slew. Every column would wait out "
            "its goto timeout and report 'did not arrive', which reads like a broken mount. "
            "Open the arm in the Seestar app and try again."
        )
    sky = _sky(sc, cfg)
    sw = cfg["sweep"]
    ptr = _pointer(sc, sky, sw, args.dry_run)
    state = {"sky_ref": None, "misses": 0, "profiles": {}}

    # START THE VIEW. `orient` never did, and got away with it because a sweep
    # run earlier in the same session had left the scenery stream up — so it
    # worked every time it was tested and failed the first time it ran on its
    # own, with "Connection refused" on the RTSP port and nothing to say why.
    # Locked before the view starts, for the same reason as in `cmd_sweep`: with
    # auto-exposure the camera renormalises every frame and the sky/terrain
    # difference this depends on disappears.
    night = False
    if not args.dry_run:
        night = sky.sun()[1] < NIGHT_SUN_ALT
        if night:
            print(f"night (sun {sky.sun()[1]:.1f} deg): star-mode imaging channel, 2 s frames")
            set_channel(sc, night=True)
        else:
            _start_locked(sc, sw)
    # A column costs about two and a half minutes of clear sky. Keeping nothing
    # from it meant every column orient measured was spent and gone — and it
    # defeated --replay, the feature this loop was built around, since replay
    # re-judges a saved night and orient was the one command saving nothing.
    frames_dir = getattr(args, "frames", None) or (
        os.path.splitext(getattr(args, "out", None) or getattr(args, "mask", "orient"))[0]
        + "_frames"
    )

    # Seeded before the first column, exactly as run_sweep does. Without it
    # scan_horizon returns "no_reference" for EVERY column — it cannot tell dark
    # terrain from a dim sky with nothing to compare against — and an
    # inconclusive column that was then recorded as a bound would feed the fit a
    # measurement nobody made.
    if not args.dry_run and not night:
        try:
            saz, _ = sky.sun()
            ptr.point_to((saz + 180.0) % 360.0, 75.0)
            state["sky_ref"] = sky_reference(sc.capture_rgb(warmup=0.3))
            print(f"sky reference: {state['sky_ref']:.1f}")
        except (SunGuard, PointingError, OSError) as e:
            print(f"could not seed the sky reference ({e}); columns will be inconclusive",
                  file=sys.stderr)  # fmt: skip

    # EVERY COLUMN IS CHECKPOINTED AS IT COMPLETES, and a prior run's columns
    # are served from the checkpoint instead of re-observed (terminus-58). A
    # guided run on 2026-08-06 measured two columns, was refused on the rest,
    # ended without a fit, and threw both away — each ~2.5 minutes of clear
    # sky, and the run consumed an observing window to produce nothing. The
    # file is append-only, one line per ATTEMPT with its conditions, because
    # the first prototype of this fix checkpointed only successes and its very
    # first crash left nothing behind.
    ckpt_path = (
        os.path.splitext(getattr(args, "out", None) or getattr(args, "mask", "orient"))[0]
        + "_fiducials.jsonl"
    )
    cache = {}
    if os.path.exists(ckpt_path):
        for line in open(ckpt_path):
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if "az" not in d or d.get("verdict") in (None, "failed"):
                continue
            # NIGHT VERDICTS ARE RE-JUDGED FROM THEIR SAVED PROFILES: the
            # verdict in the file is what an old detector thought, the profile
            # is what the sky did, and re-judging turned two wrong verdicts
            # into right ones the night this was built. Day profiles keep their
            # stored verdict — their judge needs a sky reference that is not in
            # the record.
            prof = [(a, lum) for a, lum in (d.get("profile") or [])]
            # A DAY VERDICT FROM A DEAD SKY IS DISCARDED, NOT SERVED. Three
            # twilight columns (2026-08-07, ref 21.3) came back as confident
            # low edges in a treeline the sweeps put above 60 deg; serving
            # them from the checkpoint would feed the night resume the very
            # numbers the floor exists to refuse (M-08, M-13). Dropped from
            # the cache entirely so the current channel re-measures them.
            if (
                d.get("channel") != "star4800"
                and d.get("verdict") in ("edge", "blocked")
                and d.get("sky_ref") is not None
                and float(d["sky_ref"]) < DAY_REF_FLOOR
            ):
                print(f"az {d['az']:3d}: checkpoint {d['verdict']} discarded - measured at "
                      f"sky_ref {d['sky_ref']}, below the day floor {DAY_REF_FLOOR:g}; "
                      "will re-measure", file=sys.stderr)  # fmt: skip
                continue
            if d.get("channel") == "star4800" and len(prof) >= 3:
                idx, verdict = night_find_edge(prof)
                if verdict != d["verdict"]:
                    print(f"az {d['az']:3d}: checkpoint verdict {d['verdict']} -> {verdict} "
                          "under the current detector", file=sys.stderr)  # fmt: skip
                    d = dict(d, verdict=verdict)
                    if verdict == "edge":
                        # The bisection never ran: this is the coarse bracket's
                        # midpoint, and the record says so rather than passing
                        # it off as a refined value.
                        d["alt"] = round((prof[idx][0] + prof[idx + 1][0]) / 2.0, 1)
                        d["coarse_only"] = True
            cache[int(d["az"])] = d
        if cache:
            print(f"resuming {len(cache)} columns from {os.path.basename(ckpt_path)}: "
                  f"{sorted(cache)}")  # fmt: skip

    def checkpoint(rec):
        with open(ckpt_path, "a") as fh:
            fh.write(json.dumps(rec) + "\n")

    def cached_result(d):
        if d["verdict"] == "edge":
            return {"alt": d["alt"], "snr": None}, d.get("ceiling", sw["alt_max"]), args.uncertainty
        if d["verdict"] == "blocked":
            return None, d.get("ceiling", sw["alt_max"]), args.uncertainty
        return None  # open / inconclusive: measured, no constraint

    def measure(az):
        if int(az) in cache:
            d = cache[int(az)]
            print(f"az {int(az):3d}: from checkpoint ({d['verdict']} {d.get('alt') or ''})")
            return cached_result(d)
        t0 = time.time()
        try:
            if night:
                try:
                    alt, status, _typ, profile = scan_horizon_night(
                        ptr, sc, az, sw["alt_min"], sw["alt_max"], sw.get("coarse_step", 5.0),
                        sw["alt_tol"],
                        frames_dir=(f"{frames_dir}/scan" if not args.dry_run else None),
                    )  # fmt: skip
                except (ConnectionError, OSError, TimeoutError, SeestarError) as e:
                    # THE SECOND SOCKET DEATH MUST NOT LOSE THE NIGHT. The client
                    # reopens the imaging socket once on its own; when that also
                    # fails the scope has usually torn down the whole star
                    # session after a burst of failed gotos (I-16, seen twice on
                    # 2026-08-06) — so restart the view, and give the COLUMN one
                    # more try. A second failure checkpoints as failed and the
                    # loop moves on: one lost column, not a crashed run needing
                    # a manual rerun, which is the exact loss terminus-58
                    # exists to prevent.
                    print(f"az {az:3d}: imaging channel died ({str(e)[:60]}); "
                          "restarting the star view", file=sys.stderr)  # fmt: skip
                    try:
                        sc.close_imaging()
                        sc.stop_view()
                        time.sleep(1)
                        sc.start_view("star")
                        time.sleep(6)
                        alt, status, _typ, profile = scan_horizon_night(
                            ptr, sc, az, sw["alt_min"], sw["alt_max"],
                            sw.get("coarse_step", 5.0), sw["alt_tol"],
                            frames_dir=(f"{frames_dir}/scan" if not args.dry_run else None),
                        )  # fmt: skip
                    except (ConnectionError, OSError, TimeoutError, SeestarError) as e2:
                        # SeestarError included in BOTH tuples: the recovery's own
                        # stop_view/start_view route through client.call, which
                        # raises SeestarError when re-authentication fails after a
                        # reconnect — and a scope that tore down the star session
                        # plausibly took the control channel with it. Catching
                        # only the socket types reintroduced "the same bug one
                        # exception class over" that the daytime sweep's cleanup
                        # already documents.
                        if not args.dry_run:
                            checkpoint(
                                {
                                    "az": int(az),
                                    "t": time.strftime("%H:%M:%S"),
                                    "verdict": "failed",
                                    "error": str(e2)[:200],
                                }
                            )
                        print(f"az {az:3d}: failed after view restart ({str(e2)[:80]})",
                              file=sys.stderr)  # fmt: skip
                        return None
            else:
                if state["sky_ref"] is not None and state["sky_ref"] < DAY_REF_FLOOR:
                    # Below the floor the day judge does not refuse, it invents
                    # (M-19's worst case, seen live tonight). Checkpointed as
                    # failed so a resume RE-ATTEMPTS it - by then the Sun may
                    # have crossed NIGHT_SUN_ALT and the night eye can answer.
                    if not args.dry_run:
                        checkpoint(
                            {
                                "az": int(az),
                                "t": time.strftime("%H:%M:%S"),
                                "verdict": "failed",
                                "error": f"day reference {state['sky_ref']:.1f} below floor "
                                f"{DAY_REF_FLOOR:g}: too dark for the day channel",
                            }
                        )
                    print(f"az {az:3d}: too dark for the day channel (ref "
                          f"{state['sky_ref']:.1f} < {DAY_REF_FLOOR:g}); wait for the night "
                          "channel (sun < -12)", file=sys.stderr)  # fmt: skip
                    return None
                try:
                    alt, status, _typ, profile = scan_horizon(
                        ptr, sc, az, sw["alt_min"], sw["alt_max"], sw.get("coarse_step", 5.0),
                        sw["alt_tol"], state["sky_ref"], repeats=sw.get("samples_per_point", 1),
                        sun_alt=sky.sun()[1],
                        frames_dir=(f"{frames_dir}/scan" if not args.dry_run else None),
                    )  # fmt: skip
                except (ConnectionError, OSError, TimeoutError, SeestarError) as e:
                    # THE DAY CHANNEL DIES TOO. On 2026-08-07 the scenery RTSP
                    # stream stopped serving mid-run and the ffmpeg timeout
                    # crashed the whole orient at column ten — nine measured
                    # columns stranded in the checkpoint, an observing window
                    # spent for no fit. Same recovery as the night branch:
                    # cycle the view (which also re-locks the exposure, M-06),
                    # give the COLUMN one more try, and a second failure is one
                    # lost column rather than a dead run (D-12).
                    print(f"az {az:3d}: scenery stream died ({str(e)[:60]}); "
                          "restarting the view", file=sys.stderr)  # fmt: skip
                    try:
                        _start_locked(sc, sw)
                        alt, status, _typ, profile = scan_horizon(
                            ptr, sc, az, sw["alt_min"], sw["alt_max"],
                            sw.get("coarse_step", 5.0), sw["alt_tol"], state["sky_ref"],
                            repeats=sw.get("samples_per_point", 1), sun_alt=sky.sun()[1],
                            frames_dir=(f"{frames_dir}/scan" if not args.dry_run else None),
                        )  # fmt: skip
                    except (ConnectionError, OSError, TimeoutError, SeestarError) as e2:
                        if not args.dry_run:
                            checkpoint(
                                {
                                    "az": int(az),
                                    "t": time.strftime("%H:%M:%S"),
                                    "verdict": "failed",
                                    "error": str(e2)[:200],
                                }
                            )
                        print(f"az {az:3d}: failed after view restart ({str(e2)[:80]})",
                              file=sys.stderr)  # fmt: skip
                        return None
        except SunGuard as e:
            print(f"az {az:3d}: skipped ({e})", file=sys.stderr)
            return None
        except PointingError as e:
            state["misses"] += 1
            if not args.dry_run:
                checkpoint(
                    {
                        "az": int(az),
                        "t": time.strftime("%H:%M:%S"),
                        "verdict": "failed",
                        "error": str(e)[:200],
                    }
                )
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
        verdict = ("edge" if status.startswith("edge")
                   else "blocked" if status == "blocked_above"
                   else "open" if status.startswith("open")
                   else "inconclusive")  # fmt: skip
        rec = {"az": int(az), "t": time.strftime("%H:%M:%S"),
               "secs": round(time.time() - t0, 1), "verdict": verdict,
               "alt": (alt if verdict == "edge" else None), "ceiling": sw["alt_max"],
               "channel": ("star4800" if night else "scenery"),
               "sun_alt": round(sky.sun()[1], 1),
               "sky_ref": (round(state["sky_ref"], 1) if state["sky_ref"] else None),
               "profile": profile}  # fmt: skip
        if not args.dry_run:
            checkpoint(rec)
        cache[int(az)] = rec
        if profile:
            state["profiles"][int(az)] = profile
        if profile and not night:
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

        At night, columns near due north are excluded outright: the walk crosses
        declinations where RA will not converge (dec ~ 90 - |alt - lat|), and on
        2026-08-06 a burst of those failed gotos made the scope reset every
        connection it held. One column was unrecoverable at az 0 and cost 206
        seconds discovering it. Daytime sweeps have measured az 0 successfully,
        so the exclusion is night-only until the difference is understood.

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
        if night and min(az % 360.0, 360.0 - az % 360.0) <= 8.0:
            return False
        return not column_touches_sun(sky, az, sw["alt_min"], sw["alt_max"], sw["sun_cone_deg"])

    return measure, reachable, _sun_deadline(sky, getattr(args, "stop_above_sun_alt", None)), state


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
        print(f"  dropped {name}: {counts.get(name, 0)} control points (need {args.min_points})")
    if not kept:
        raise mosaic.MosaicError("no frame could be constrained; nothing to render")

    tiffs, final = mosaic.render(pto, work, width=args.width, height=args.height)
    img, coverage, gains = mosaic.composite(tiffs, args.width, args.height)
    Image.fromarray(img).save(base + ".png")
    np.save(base + ".coverage.npy", coverage)
    figure_path = None
    if getattr(args, "photometric", False):
        # The SAME geometric solve, rendered a second time with Hugin's
        # photometric model applied — for figures and polar backdrops. The
        # measurement outputs above are already written and never touch this
        # (terminus-52: it changes pixel values, and every published residual
        # was produced without it).
        fig_tiffs, _photo_pto = mosaic.render_photometric(final, work)
        fig, _fig_cov, _fig_gains = mosaic.composite(fig_tiffs, args.width, args.height)
        figure_path = base + ".figure.png"
        Image.fromarray(fig).save(figure_path)
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
        photometric_figure=(os.path.abspath(figure_path) if figure_path else None),
    )
    if args.segment:
        _segment_frames(args, mosaic, final, base, work)
    covered = float((coverage > 0).any(axis=0).mean()) * 100.0
    print(f"wrote {base}.png ({args.width}x{args.height}, {covered:.0f}% of azimuth covered)")
    print(f"wrote {base}.coverage.npy and {base}.manifest.json")
    if figure_path:
        print(
            f"wrote {figure_path} (photometric FIGURE render: use it for polar "
            "backdrops and papers; measurements keep coming from the plain render)"
        )


def _segment_frames(args, mosaic, final, base, work):
    """Segment each FRAME, then warp the labels through the same solve.

    The other order — stitch, then segment the panorama — is what this replaces,
    and it asks the model to do something it was never trained for. A finished
    equirectangular canvas is eighteen 12-megapixel photographs resampled down to
    one 2880x1440 image, blended across seams, stretched without limit toward the
    poles, and black where nobody pointed. SegFormer reads photographs. Each
    frame still IS one, at full resolution, in the projection the camera made.

    So the labels are computed where the model is at home and then follow their
    own pixels into the panorama, nearest-neighbour so a class index is never
    interpolated into a class that does not exist.
    """
    import numpy as np
    from PIL import Image

    from . import skymask

    if not skymask.available("segment"):
        raise SeestarError(
            "--segment needs torch, torchvision and transformers.\n"
            "Install them, or drop --segment and let `terminus skymask` read the "
            "stitched panorama instead (worse: the model was trained on photographs, "
            "not on equirectangular projections)."
        )
    names = mosaic.source_images(final)
    label_dir = os.path.join(work, "labels")
    os.makedirs(label_dir, exist_ok=True)
    label_for = {}
    for i, name in enumerate(names, 1):
        src = os.path.join(os.path.dirname(final), name)
        print(f"  segmenting frame {i}/{len(names)}: {os.path.basename(name)}", flush=True)
        classes = skymask.segment_classes(Image.open(src).convert("RGB"))
        # The class id in all three channels: nona remaps RGB, and reading one
        # channel back is simpler than persuading it to carry a palette.
        lab = np.repeat(classes.astype(np.uint8)[:, :, None], 3, axis=2)
        path = os.path.join(label_dir, f"{i:03d}.png")
        Image.fromarray(lab).save(path)
        label_for[name] = os.path.relpath(path, os.path.dirname(final))
    layers = mosaic.remap_labels(final, work, label_for)
    classes = mosaic.combine_labels(layers, args.width, args.height)
    np.save(base + ".classes.npy", classes)
    named = int((classes >= 0).sum())
    print(f"wrote {base}.classes.npy ({named * 100 // classes.size}% of pixels labelled)")


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

    seg = None
    if args.classes:
        seg = np.load(args.classes)
        if seg.shape != (h, w):
            raise SeestarError(
                f"classes {seg.shape} do not match the image {(h, w)}; they must come "
                "from the same mosaic run"
            )

    if seg is not None:
        # THE ALTITUDES COME FROM THE FRAMES TOO, not just the obstruction type.
        # For a long time only the type did, and the two were indistinguishable
        # from outside: a mask written before the per-frame labels existed and
        # one written after had byte-identical altitudes and differed only in
        # `type`. D-01 chose individual photographs over a stitched panorama on
        # mechanism — a 358-degree panorama has no meaningful focal length, and
        # SegFormer reads photographs — and that reasoning is about where the
        # HORIZON comes from, not merely what it is made of.
        sky = seg == skymask.SKY_CLASS_ADE20K
        backend = "segment (per frame)"
        # UNLABELLED IS NOT GROUND. `horizon_rows` takes anything that is not
        # positively sky as terrain, so leaving -1 in would turn "no frame voted
        # here" into a solid obstruction — a plausible wrong number where the
        # honest answer is a gap (M-19). Dropping it from `valid` removes those
        # pixels from the column instead.
        labelled = seg >= 0
        valid = labelled if valid is None else (valid & labelled)
    else:
        sky, backend = skymask.sky_mask(image, backend=args.backend, report=True)
    # `backend` is now what RAN, not what was asked for. Everything below keys
    # off it — the printed line, the segmentation-only type pass, and the mask
    # meta — so a silent fallback cannot be recorded as a segment run.
    band = skymask.horizon_band(sky, valid=valid, run=args.run)
    px_per_deg = w / 360.0
    # IMAGE PROCESSING EMITS THE FAITHFUL SKYLINE (terminus-55). The envelope
    # only ever RAISES the horizon, so at this layer it deleted detail nobody
    # could get back — and it deleted it before anyone knew which way was up,
    # since the rotation onto the sky is solved later from telescope fiducials.
    # Filling a gap the telescope cannot point through is a real requirement; it
    # now happens in `export`, where the instrument is known. Default 0 here.
    top = (
        skymask.upper_envelope(band["top"], half_deg=args.envelope, px_per_deg=px_per_deg)
        if args.envelope
        else band["top"]
    )

    classes = np.full(w, -1, dtype=int)
    print(f"backend used: {backend}")
    if seg is not None:
        print(f"horizon and types from {args.classes} (segmented per frame)")
        classes = skymask.obstruction_classes(seg, band["top"], valid=valid)
    elif backend == "segment":
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
OFFLINE = {"mosaic", "skymask", "export", "polar"}


def _is_offline(args):
    """Does this invocation need neither scope nor config.toml?

    `orient` is the one command that answers differently depending on its flags:
    with `--replay` it re-judges a saved night and touches nothing, without it
    the telescope measures. Deciding by subcommand alone would force a config
    file on someone replaying a run on a laptop, which is precisely the case
    replay exists to serve.
    """
    if args.cmd == "orient":
        return bool(args.replay or getattr(args, "fiducials", None))
    return args.cmd in OFFLINE


def _needs_scope(args):
    if args.cmd == "orient":
        return not (args.replay or getattr(args, "fiducials", None))
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
    sw = sub.add_parser(
        "sweep",
        help="full horizon sweep -> mask YAML (day or night)",
        description="Full horizon sweep -> mask YAML (+ review frames). The "
        "measurement channel follows the Sun per column: above -12 deg the "
        "scenery stream with a locked exposure, below it the star-mode imaging "
        "channel with the night detector (the scenery stream is blind after "
        "dark), switching at the boundary mid-run. Night columns carry no "
        "obstruction type, and their profiles are saved with the channel so "
        "`orient --replay` judges them with the night judge.",
    )
    sw.add_argument("--az-start", type=float, default=0)
    sw.add_argument("--az-end", type=float, default=350)
    sw.add_argument("--out", default=None)
    sw.add_argument("--frames", default=None)
    sw.add_argument(
        "--azimuths",
        default=None,
        help="comma list of azimuths to measure instead of a range; merges into --out if it exists",
    )
    sw.add_argument("--no-export", action="store_true")
    sw.add_argument(
        "--stop-above-sun-alt",
        type=float,
        default=None,
        help="stop before any column once the Sun reaches this altitude, checked "
        "inside the loop (try -18 for astronomical twilight)",
    )
    sw.add_argument("--dry-run", action="store_true")
    orp = sub.add_parser(
        "orient",
        help="solve where a photo horizon sits on the sky, column by column",
        description="Solve where a photo horizon sits on the sky, column by column. "
        "With the scope, the measurement channel is chosen from the Sun's altitude "
        "at run start: below -12 deg the run measures on the star-mode imaging "
        "channel with the night detector (the scenery stream is blind after dark), "
        "otherwise on the scenery stream with a locked exposure. The choice is per "
        "run, not per column, so do not start a run that will straddle the "
        "twilight boundary. Every attempt is checkpointed to <out>_fiducials.jsonl "
        "as it completes; rerunning resumes from it, and saved night profiles are "
        "re-judged by the current detector.",
    )
    orp.add_argument("mask", help="the UNORIENTED photo mask from `terminus skymask`")
    orp.add_argument("--out", help="where to write the oriented mask")
    orp.add_argument("--frames", help="where to save the measured frames (default: beside --out)")
    orp.add_argument(
        "--replay",
        help="re-judge a saved sweep's <mask>_profiles.json instead of observing "
        "(no telescope, no night)",
    )
    orp.add_argument(
        "--fiducials",
        action="append",
        metavar="MASK",
        help="orient against telescope columns ALREADY measured, from a mask a sweep "
        "wrote. Repeatable, and merged first-wins, because a sweep is routinely split "
        "across arcs and nights. No telescope, no night",
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

    po = sub.add_parser("polar", help="write a one-page fisheye view of the horizon")
    po.add_argument("mask", help="an ORIENTED mask; its meta carries the solved rotation")
    po.add_argument("--out", default=None, help="output .html (default: <mask>_polar.html)")
    po.add_argument(
        "--image",
        default=None,
        help="equirectangular panorama to reproject (default: the mask's own meta.source)",
    )
    po.add_argument("--coverage", default=None, help="the mosaic's .coverage.npy")
    po.add_argument(
        "--fiducials",
        default=None,
        help="a telescope mask to draw as measured columns over the photograph",
    )
    po.add_argument(
        "--ceiling",
        type=float,
        default=None,
        help="the fiducial sweep's altitude ceiling; columns reaching it are drawn as bounds",
    )
    po.add_argument(
        "--floor",
        type=float,
        default=polar.FLOOR_DEG,
        help="how far below the horizon the disc reaches (default %(default)s)",
    )
    po.add_argument("--size", type=int, default=polar.SIZE, help="pixels across the disc")
    po.add_argument("--title", default=None)
    po.add_argument(
        "--allow-unoriented",
        action="store_true",
        help="draw a mask whose azimuth is not yet true north",
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
    mo.add_argument(
        "--segment",
        action="store_true",
        help="segment each FRAME and warp the labels through the same solve, writing "
        "<out>.classes.npy for `terminus skymask --classes`. Needs torch, torchvision "
        "and transformers",
    )
    mo.add_argument(
        "--photometric",
        action="store_true",
        help="ALSO write <out>.figure.png: the same geometric solve rendered with "
        "Hugin's photometric model (exposure, vignetting, response) applied, so "
        "frame boundaries stop showing exposure steps. For figures and polar "
        "backdrops only - measurements always come from the plain render, and "
        "seams stay hard (a visible seam is how you check the registration)",
    )

    sk = sub.add_parser("skymask", help="read a horizon off a panorama (UNORIENTED)")
    sk.add_argument("image")
    sk.add_argument("--backend", choices=("auto", "segment", "heuristic"), default="auto")
    sk.add_argument(
        "--coverage",
        default=None,
        help="the .coverage.npy from `terminus mosaic`; without it, "
        "uncovered pixels read as terrain",
    )
    sk.add_argument(
        "--classes",
        default=None,
        help="the .classes.npy from `terminus mosaic --segment`: obstruction types "
        "read from the frames themselves rather than from the stitched panorama",
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
        "--envelope",
        type=float,
        default=0.0,
        help="raise the skyline to the highest terrain within +-N degrees. Default 0: "
        "the mask records what the photograph shows. The usable-gap envelope now "
        "lives in `terminus export`, which knows the instrument (terminus-55)",
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
        "polar": cmd_polar,
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
