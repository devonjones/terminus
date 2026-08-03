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
from .export import default_meta, export_all, write_mask
from .sweep import Pointer, Sky, SunGuard, classify, column_touches_sun, obstruction_type, run_sweep


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
        sc.start_view("scenery")
        time.sleep(3)
    mask, skipped, profiles = run_sweep(
        sc, sky, cfg["sweep"], args.az_start, args.az_end, save_dir=frames, dry=args.dry_run
    )
    if not args.dry_run:
        sc.stop_view()
    if profiles:
        prof_path = os.path.splitext(out)[0] + "_profiles.json"
        with open(prof_path, "w") as f:
            json.dump(profiles, f, indent=1)
        print(f"wrote {prof_path} (raw column brightness profiles)")
    lat = sky.loc.lat.deg
    lon = sky.loc.lon.deg
    write_mask(
        out, mask, skipped, default_meta(round(lat, 4), round(lon, 4), cfg["sweep"], skipped)
    )
    print(
        f"\nwrote {out} ({len(mask)} azimuths, {len(skipped)} skipped); review frames in {frames}/"
    )
    if not args.no_export and mask:
        hrz, txt = export_all(out, os.path.splitext(out)[0])
        print(f"exported {hrz} and {txt}")


def cmd_export(sc, cfg, args):  # sc unused; export is offline
    hrz, txt = export_all(args.mask, os.path.splitext(args.mask)[0])
    print(f"wrote {hrz}\nwrote {txt}")


NEEDS_SCOPE = {"preflight", "point", "classify", "sweep"}


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
    sw.add_argument("--no-export", action="store_true")
    sw.add_argument("--dry-run", action="store_true")
    ex = sub.add_parser("export")
    ex.add_argument("mask")
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
    }
    try:
        cfg = load_config(args.config)
        sc = _connect(cfg) if args.cmd in NEEDS_SCOPE else None
        try:
            handlers[args.cmd](sc, cfg, args)
        finally:
            if sc:
                sc.close()
    except (ConfigError, SeestarError) as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
