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
import os
import sys
import time

from .client import Seestar, SeestarError
from .config import ConfigError, load_config
from .export import MaskError, default_meta, export_all, load_columns, write_mask
from .mosaic import MIN_CONTROL_POINTS, MosaicError
from .sweep import (
    Pointer,
    PointingError,
    Sky,
    SunGuard,
    classify,
    column_touches_sun,
    obstruction_type,
    run_sweep,
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
        prior = {az: c for az, c in prior_cols.items()}
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
    if prior:
        merged = dict(prior)
        merged.update(mask)  # freshly measured columns win
        meta = dict(prior_meta)
        meta.update(default_meta(round(lat, 4), round(lon, 4), cfg["sweep"], skipped))
        print(f"wrote {len(mask)} measured, {len(merged) - len(mask)} preserved")
        mask = merged
    else:
        meta = default_meta(round(lat, 4), round(lon, 4), cfg["sweep"], skipped)
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
    hrz, txt = export_all(
        args.mask,
        os.path.splitext(args.mask)[0],
        allow_unoriented=getattr(args, "allow_unoriented", False),
    )
    print(f"wrote {hrz}\nwrote {txt}")


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
        mask[az] = {
            "alt": round(alt, 2),
            "type": _type_name(int(classes[x])),
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
    ex = sub.add_parser("export")
    ex.add_argument("mask")
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
    }
    try:
        cfg = {} if args.cmd in OFFLINE else load_config(args.config)
        sc = _connect(cfg) if args.cmd in NEEDS_SCOPE else None
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
