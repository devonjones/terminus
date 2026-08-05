"""terminus — measure your local horizon and export it for planning tools
(N.I.N.A., Stellarium), or query it directly.

Three sides. The photographs are the mask; the telescope calibrates them.

  Calibrating — a handful of measured columns fix the three parameters that
  place a photo-derived horizon on the sky. `terminus orient` runs this, and
  `--replay` runs it against a night already measured, with no scope:
    from terminus import guided_orient, replay
    sol, steps = guided_orient(rows, replay(profiles))   # or a live measure()
    sol["yaw"], sol["pitch"], sol["tilt_mag"]            # and whether it settled

  The pieces, if you want the loop yourself — plan a column by information gain,
  measure it, refit, and stop when the yaw holds still rather than when the RMS
  looks small:
    from terminus import orient, plan
    az, _ = plan.next_column(done, cands, grad)
    fids.append(plan.as_fiducial(az, edge, ceiling, orient.Fiducial))
    sol   = orient.fit(fids, sample)
    ok, spread = plan.is_stable(history)

  Building the mask from photographs (no scope, no network):
    from terminus import mosaic, skymask
    pto, dropped = mosaic.solve(image_dir, work)   # frames that cannot be
    sky  = skymask.sky_mask(equirect)              # constrained are DROPPED
    rows, clipped = skymask.horizon_rows(sky)

  Measuring with the scope alone (the fallback, and what `terminus sweep` runs):
    from terminus import Seestar, Sky, run_sweep
    sc = Seestar(host, pem); sc.authenticate()
    mask, skipped, profiles = run_sweep(sc, Sky(lat, lon, elev), sweep_cfg)

  Planning (what a scheduler consumes):
    from terminus import Horizon
    h = Horizon.from_mask("horizon_mask.yaml")
    h.is_visible(ra_h, dec, sky)   # does this target clear the horizon now?

Exporting:
    from terminus import export_all   # mask -> .hrz (N.I.N.A.) + .txt (Stellarium)

Optional dependencies stay optional. Importing terminus never pulls in torch or
transformers, and never shells out to Hugin; `skymask.available()` and
`mosaic.hugin_available()` report what is installed, and the segmentation model
loads only on first use. Prefer the module-qualified spelling shown above —
`orient.fit`, `mosaic.solve` — since several of these names are generic on their
own.
"""

# The photo-first modules. Exported both as modules (the documented spelling)
# and by name below. None of them imports torch, transformers or Hugin at module
# level, so this stays cheap and dependency-free.
#
# This line is load bearing and only LOOKS redundant. Four of the five are also
# bound as a side effect of the `from .mosaic import ...` style lines below, so
# deleting their names here appears harmless — until the flat exports are
# narrowed, which has already happened once, and the module quietly stops being
# reachable. `skymask` has no such line and depends on this one alone.
from . import mosaic, night, orient, plan, skymask
from .client import Seestar, SeestarError
from .config import ConfigError, load_config
from .export import (
    export_all,
    load_mask,
    to_nina_hrz,
    to_pvsyst_hor,
    to_stellarium_txt,
    write_mask,
)
from .guide import orient_mask, photo_sample, replay
from .guide import run as guided_orient
from .horizon import Horizon
from .landscape import render, to_skysafari_png, write_landscape
from .mosaic import MosaicError, hugin_available, write_manifest
from .night import fit_skyglow, mask_lights
from .orient import Fiducial
from .plan import (
    as_fiducial,
    horizon_gradient,
    is_stable,
    next_column,
    rank_columns,
    residual_targets,
    seed_columns,
)
from .sweep import Pointer, Sky, SunGuard, ang_sep, classify, obstruction_type, run_sweep

__version__ = "0.1.0"
__all__ = [
    # measuring, with the scope
    "Seestar",
    "SeestarError",
    "Sky",
    "run_sweep",
    "Pointer",
    "SunGuard",
    "classify",
    "obstruction_type",
    "ang_sep",
    # the photo-first modules; prefer these over the flat names below
    "skymask",
    "mosaic",
    "orient",
    "plan",
    "night",
    # calibrating
    "Fiducial",
    "next_column",
    "rank_columns",
    "seed_columns",
    "is_stable",
    "residual_targets",
    "horizon_gradient",
    "as_fiducial",
    # building the mask from photographs
    "MosaicError",
    "hugin_available",
    "write_manifest",
    # after dark
    "fit_skyglow",
    "mask_lights",
    # planning and export
    "Horizon",
    "export_all",
    "guided_orient",
    "write_landscape",
    "write_mask",
    "load_mask",
    "orient_mask",
    "photo_sample",
    "render",
    "replay",
    "to_nina_hrz",
    "to_pvsyst_hor",
    "to_skysafari_png",
    "to_stellarium_txt",
    "load_config",
    "ConfigError",
    "__version__",
]


def measure_horizon(config_path="config.toml", az_start=0, az_end=350, out="horizon_mask.yaml"):
    """High-level convenience: connect, sweep, write the mask, export. Returns
    (mask, skipped). For scripted/one-shot use; the CLI wraps the same flow."""
    import os

    from .export import default_meta

    cfg = load_config(config_path)
    sc = Seestar(cfg["host"], cfg["pem"])
    if not sc.authenticate():
        raise SeestarError("authentication failed")
    try:
        site = cfg["site"]
        if "lat" in site and "lon" in site:
            sky = Sky(site["lat"], site["lon"], site.get("elev_m", 0.0))
        else:
            lon, lat = sc.location()
            sky = Sky(lat, lon, site.get("elev_m", 0.0))
        sc.stop_view()
        sc.start_view("scenery")
        mask, skipped, _profiles = run_sweep(
            sc, sky, cfg["sweep"], az_start, az_end, save_dir=os.path.splitext(out)[0] + "_frames"
        )
        sc.stop_view()
        write_mask(
            out,
            mask,
            skipped,
            default_meta(
                round(sky.loc.lat.deg, 4), round(sky.loc.lon.deg, 4), cfg["sweep"], skipped
            ),
        )
        if mask:
            export_all(out, os.path.splitext(out)[0])
        return mask, skipped
    finally:
        sc.close()
