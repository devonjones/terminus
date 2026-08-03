"""Load terminus configuration from a TOML file (see config.example.toml).

Only [scope] is required; [site] may be omitted and read from the scope, and
[sweep] falls back to the defaults below. Keeping every runtime value in a file
(not code) is what lets the same checkout run on a laptop or a Raspberry Pi.
"""

import os
import tomllib

SWEEP_DEFAULTS = {
    "az_step": 5,
    "alt_min": 0.0,
    "alt_max": 45.0,
    "alt_tol": 0.7,
    "coarse_step": 5.0,
    "az_refine_deg": 5,  # finest azimuth spacing the refinement pass may reach
    "refine_threshold_deg": 10.0,  # neighbour disagreement that triggers refinement
    "refine_max_columns": 24,  # cap on extra columns, so refinement cannot run away
    "sun_cone_deg": 30.0,
    "clear_thresh": 0.85,
    "slew_step_deg": 5.0,
}


class ConfigError(RuntimeError):
    pass


def load_config(path):
    path = os.path.expanduser(path)
    try:
        with open(path, "rb") as f:
            doc = tomllib.load(f)
    except FileNotFoundError:
        raise ConfigError(f"config not found: {path} (copy config.example.toml)") from None
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"bad TOML in {path}: {e}") from e

    scope = doc.get("scope") or {}
    if not scope.get("host") or not scope.get("pem"):
        raise ConfigError("[scope] needs both 'host' and 'pem'")

    sweep = dict(SWEEP_DEFAULTS)
    sweep.update(doc.get("sweep") or {})
    return {
        "host": scope["host"],
        "pem": scope["pem"],
        "site": doc.get("site") or {},  # optional lat/lon/elev_m; else from scope
        "sweep": sweep,
    }
