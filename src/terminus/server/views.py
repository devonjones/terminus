"""What the app shows of a site, computed by the engine's own functions.

The disc geometry comes from `polar.disc_xy` and the photo from `polar.project`,
so the app draws exactly what `terminus polar` draws and no projection is
re-implemented in JavaScript (where it could drift into a mirrored picture).
"""

import io
import math
import os

from .. import polar
from ..export import is_oriented, load_columns
from .sites import mask_path

SIZE, FLOOR = polar.SIZE, polar.FLOOR_DEG
# An unoriented mask is in the panorama's own azimuth: drawn with no rotation,
# and the user's rough spin turns the whole disc.
IDENTITY = {"yaw": 0.0, "pitch": 0.0, "tilt_mag": 0.0, "tilt_dir": 0.0}
COLUMN_KEYS = ("type", "planning", "fuzz", "bound", "clipped")


def _num(v):
    """A finite float, or None. The meta block is written as a Python literal, so
    an absent value comes back from YAML as the string "None"."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _fit_row(f):
    """One fit_fiducials record, keeping only fields that carry a real value."""
    row = {k: _num(f.get(k)) for k in ("az", "alt", "residual")}
    row.update({k: f[k] for k in ("bound", "used") if isinstance(f.get(k), bool)})
    row.update(
        {
            k: f[k]
            for k in ("reason", "excluded_by")
            if isinstance(f.get(k), str) and f[k] not in ("", "None")
        }
    )
    return {k: v for k, v in row.items() if v is not None}


def _load(d):
    path = mask_path(d)
    if path is None:
        return None, None, {}
    meta, cols = load_columns(path)
    return path, meta, cols


def solution(meta):
    """The rotation that places the photograph, or None when nothing places it.

    An unoriented photo mask is drawn in its own azimuth (identity). An oriented
    mask carries its solved rotation. A scope-only sweep is oriented but has no
    rotation and no photograph to place.
    """
    if not is_oriented(meta):
        return dict(IDENTITY)
    try:
        return polar.solution_from_meta(meta)
    except ValueError:
        return None


def horizon(d):
    path, meta, cols = _load(d)
    if path is None:
        return None
    columns = []
    for az in sorted(cols):
        c = cols[az]
        row = {"az": float(az), "alt": c["alt"]}
        row.update({k: c[k] for k in COLUMN_KEYS if k in c})
        if c.get("pockets"):
            row["pockets"] = [list(p) for p in c["pockets"]]
        columns.append(row)
    fit = [
        _fit_row(f)
        for f in (meta.get("fit_fiducials") or [])
        if isinstance(f, dict) and _num(f.get("az")) is not None
    ]
    settled = meta.get("fit_settled")
    return {
        "mask": os.path.basename(path),
        "oriented": is_oriented(meta),
        "solution": solution(meta),
        "columns": columns,
        "fit": fit,
        "settled": settled if isinstance(settled, bool) else None,
        # Which detector read the horizon. "heuristic" means no segmentation
        # model ran, and the user should know their horizon was read the rough way.
        "backend": meta.get("backend") if isinstance(meta.get("backend"), str) else None,
    }


def _xy(az, alt):
    x, y = polar.disc_xy(float(az), float(alt), SIZE, FLOOR)
    return [round(x, 1), round(y, 1)]


def disc(d):
    """Everything drawn over the photo on the disc, in disc pixels."""
    h = horizon(d)
    if h is None:
        return None
    centre, radius = SIZE // 2, SIZE // 2 - 1
    cols = h["columns"]
    return {
        "size": SIZE,
        "floor": FLOOR,
        "rings": [
            {"alt": a, "r": round(radius * (90.0 - a) / (90.0 - FLOOR), 1)}
            for a in (60, 30, 0, int(FLOOR))
        ],
        "centre": centre,
        "cardinals": [
            {"label": lab, "xy": _xy(az, FLOOR + 6.0)}
            for lab, az in (("N", 0), ("E", 90), ("S", 180), ("W", 270))
        ],
        "actual": [_xy(c["az"], c["alt"]) for c in cols],
        "planning": [_xy(c["az"], c["planning"]) for c in cols if "planning" in c],
        "pockets": [
            [_xy(c["az"], hi), _xy(c["az"], lo)] for c in cols for hi, lo in c.get("pockets", ())
        ],
        "fiducials": [
            {
                "az": f["az"],
                "alt": f["alt"],
                "bound": bool(f.get("bound")),
                "xy": _xy(f["az"], f["alt"]),
            }
            for f in h["fit"]
            if f.get("used") and f.get("alt") is not None
        ],
    }


def _jpeg(image, quality=85):
    buf = io.BytesIO()
    image.save(buf, "JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def _panorama(d):
    path = os.path.join(d, "equirect.png")
    return path if os.path.isfile(path) else None


def panorama_jpeg(d):
    from PIL import Image

    path = _panorama(d)
    if path is None:
        return None
    Image.MAX_IMAGE_PIXELS = None
    with Image.open(path) as im:
        return _jpeg(im.convert("RGB"))


def disc_jpeg(d):
    """The photograph reprojected onto the disc, or None if there is none to place."""
    import numpy as np
    from PIL import Image

    pano = _panorama(d)
    _, meta, _ = _load(d)
    sol = solution(meta) if meta is not None else None
    if pano is None or sol is None:
        return None
    Image.MAX_IMAGE_PIXELS = None
    with Image.open(pano) as im:
        image = im.convert("RGB")
    cov_path = os.path.join(d, "equirect.coverage.npy")
    coverage = np.load(cov_path) if os.path.isfile(cov_path) else np.ones(image.size[::-1])
    rgb, _gap = polar.project(image, coverage, sol, SIZE, FLOOR)
    return _jpeg(Image.fromarray(rgb))
