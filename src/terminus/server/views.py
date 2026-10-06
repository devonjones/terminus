"""What the app shows of a site, computed by the engine's own functions.

The disc geometry comes from `polar.disc_xy` and the photo from `polar.project`,
so the app draws exactly what `terminus polar` draws and no projection is
re-implemented in JavaScript (where it could drift into a mirrored picture).
"""

import io
import math
import os
import re

from .. import polar
from ..export import is_oriented, load_columns
from .sites import SiteError, mask_path

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
        # The actual horizon is an outline, not one altitude per azimuth: it is
        # drawn as a raster (outline_png). Only the planning line is per azimuth.
        "planning": [_xy(c["az"], c["planning"]) for c in cols if "planning" in c],
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
    shown = os.path.join(d, "equirect.display.jpg")  # colour-matched and feathered
    if os.path.isfile(shown):
        with open(shown, "rb") as fh:
            return fh.read()
    Image.MAX_IMAGE_PIXELS = None  # our own panorama, not a decompression bomb
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
    shown = os.path.join(d, "equirect.display.jpg")  # the feathered blend, for the eye
    if os.path.isfile(shown):
        pano = shown
    Image.MAX_IMAGE_PIXELS = None  # our own panorama, not a decompression bomb
    with Image.open(pano) as im:
        image = im.convert("RGB")
    cov_path = os.path.join(d, "equirect.coverage.npy")
    coverage = np.load(cov_path) if os.path.isfile(cov_path) else np.ones(image.size[::-1])
    rgb, _gap = polar.project(image, coverage, sol, SIZE, FLOOR)
    return _jpeg(Image.fromarray(rgb))


def frames(d):
    """The photos of a curatable site: where each sits on the panorama, which are
    off, and which the stitch dropped. None until a reblend has run."""
    import json

    path = os.path.join(d, "equirect.frames.json")
    if not os.path.isfile(path):
        return None
    with open(path) as fh:
        return json.load(fh)


def _frame(d, name):
    """One frame's record; refuses any name the site does not list."""
    for f in (frames(d) or {}).get("frames", ()):
        if f["name"] == name:
            return f
    raise SiteError("no such photo in this site")


def _png(image):
    buf = io.BytesIO()
    image.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def footprint_png(d, name):
    """The frame's coverage on the panorama, cropped to its box: white where it
    covers, transparent elsewhere. None for a frame with no layer (off at the
    last stitch, or dropped by it)."""
    import numpy as np
    from PIL import Image

    from .. import mosaic

    f = _frame(d, name)
    if not f["layer"]:
        return None
    _box, mask = mosaic.footprint(os.path.join(d, "work", f["layer"]))
    alpha = np.where(mask, 255, 0).astype(np.uint8)
    return _png(Image.merge("LA", (Image.new("L", alpha.shape[::-1], 255), Image.fromarray(alpha))))


def thumb_jpg(d, name, size=192):
    from PIL import Image

    # Any photo of the site, listed or not: a build in progress has no list yet.
    if name not in os.listdir(os.path.join(d, "photos")):
        raise SiteError("no such photo in this site")
    with Image.open(os.path.join(d, "photos", name)) as im:
        im.thumbnail((size, size))
        return _jpeg(im.convert("RGB"))


def disagree_png(d):
    """Where the frames outvoted each other on sky, as a red veil: stronger where
    the vote was closer. None until a reblend has run."""
    import numpy as np
    from PIL import Image

    path = os.path.join(d, "equirect.disagree.npy")
    if not os.path.isfile(path):
        return None
    dis = np.load(path).astype(np.float32)  # 0 agreed .. 0.5 split down the middle
    alpha = (np.clip(dis * 2.0, 0.0, 1.0) * 220).astype(np.uint8)
    rgba = np.zeros(dis.shape + (4,), np.uint8)
    rgba[..., 0], rgba[..., 1], rgba[..., 2], rgba[..., 3] = 255, 48, 48, alpha
    return _png(Image.fromarray(rgba, "RGBA"))


LAYER = re.compile(r"^layer\d{4}\.tif$")


def _layer_path(d, layer):
    if not isinstance(layer, str) or not LAYER.fullmatch(layer):
        raise SiteError("not a layer name")
    path = os.path.join(d, "work", layer)
    return path if os.path.isfile(path) else None


def layer_webp(d, layer):
    """One remapped photo, half size with its transparency: the live canvas
    places it at its box as the build lays it down."""
    from PIL import Image

    path = _layer_path(d, layer)
    if path is None:
        return None
    with Image.open(path) as im:
        rgba = im.convert("RGBA")
    rgba = rgba.resize((max(1, rgba.width // 2), max(1, rgba.height // 2)))
    buf = io.BytesIO()
    rgba.save(buf, "WEBP", quality=70)
    return buf.getvalue()


def verdict_png(d, layer):
    """Where this one photo judged sky, as a half-size mask (white, opaque on
    sky). None until the vote has reached it."""
    from PIL import Image

    if _layer_path(d, layer) is None:
        return None
    path = os.path.join(d, "work", "sky_" + layer[: -len(".tif")] + ".png")
    if not os.path.isfile(path):
        return None
    with Image.open(path) as im:
        sky = im.convert("L")
    sky = sky.resize((max(1, sky.width // 2), max(1, sky.height // 2)))
    return _png(Image.merge("LA", (Image.new("L", sky.size, 255), sky)))


def outline_png(d):
    """The actual horizon: the outline of the contiguous sky, projected pixel by
    pixel through the photo's own mapping (polar.sky_layers) so it folds back at
    roof corners and turns with the photo. None until reblend has written the
    class maps, or when nothing places the photo."""
    import numpy as np
    from PIL import Image

    classes = os.path.join(d, "equirect.terrain.classes.npy")
    coverage = os.path.join(d, "equirect.coverage.npy")
    _, meta, _ = _load(d)
    sol = solution(meta) if meta is not None else None
    if sol is None or not (os.path.isfile(classes) and os.path.isfile(coverage)):
        return None
    outline, _pockets = polar.sky_layers(
        np.load(classes), np.load(coverage), sol, None, SIZE, FLOOR
    )
    return _png(Image.fromarray(outline, "RGBA"))


def progress_png(d):
    """The horizon as far as a running build has judged, in panorama space."""
    path = os.path.join(d, "work", "progress.png")
    if not os.path.isfile(path):
        return None
    with open(path, "rb") as fh:
        return fh.read()
