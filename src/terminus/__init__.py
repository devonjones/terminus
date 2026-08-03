"""terminus — measure your local horizon with a Seestar S50 and export it for
planning tools (N.I.N.A., Stellarium) or query it directly.

Two sides:

  Measuring (needs the scope):
    from terminus import Seestar, Sky, run_sweep
    sc = Seestar(host, pem); sc.authenticate()
    sky = Sky(lat, lon, elev_m)
    mask, skipped, profiles = run_sweep(sc, sky, sweep_cfg)

  Planning (no scope, no network — this is what a planner like SSC consumes):
    from terminus import Horizon
    h = Horizon.from_mask("horizon_mask.yaml")
    h.altitude_at(120)              # obstruction altitude at azimuth 120
    h.is_above(120, 30)            # does (az,alt) clear the horizon?
    h.is_visible(ra_h, dec, sky)  # does an RA/Dec target clear it right now?

Exporting:
    from terminus import export_all         # mask -> .hrz (N.I.N.A.) + .txt (Stellarium)
"""

from .client import Seestar, SeestarError
from .config import ConfigError, load_config
from .export import (
    export_all,
    load_mask,
    to_nina_hrz,
    to_stellarium_txt,
    write_mask,
)
from .horizon import Horizon
from .sweep import Pointer, Sky, SunGuard, ang_sep, classify, obstruction_type, run_sweep

__version__ = "0.1.0"
__all__ = [
    "Seestar",
    "SeestarError",
    "Sky",
    "run_sweep",
    "Pointer",
    "SunGuard",
    "classify",
    "obstruction_type",
    "ang_sep",
    "Horizon",
    "export_all",
    "write_mask",
    "load_mask",
    "to_nina_hrz",
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
