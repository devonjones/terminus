"""A site's telescope columns: the measured edges the orientation is fitted to.

    <site>/scope/columns.json   the columns, each with how it was measured
    <site>/scope/frames/        the frames each column was read from

A column is {az, alt, uncertainty, method, included, tags, frames, note}:
`method` is how `alt` was measured (focused, coarse, frame or clicked) and
`frames` are [{alt, sky, file}] down that column. ("frame", a reading by the
judge, is for stage 4's scans.) `refit` solves the rotation
from every included column at once, never a guided subset.
"""

import glob
import json
import os
import re
import shutil
import statistics

import numpy as np
import yaml

from . import guide, orient
from .export import load_columns, load_mask

COLUMNS = os.path.join("scope", "columns.json")
FRAMES = os.path.join("scope", "frames")
PHOTO_MASK = "photo_mask.yaml"
DEFAULT_UNCERTAINTY = 0.5


class ColumnError(ValueError):
    """A request about the columns that cannot be done, said in the user's terms."""


FRAME_NAME = re.compile(r"az(\d{3})_alt([\d.]+)_sky(\d{3})\.(jpe?g|png)$")


def load(site):
    path = os.path.join(site, COLUMNS)
    if not os.path.isfile(path):
        return {"note": "", "columns": []}
    with open(path) as fh:
        return json.load(fh)


def save(site, doc):
    path = os.path.join(site, COLUMNS)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(doc, fh, indent=1)
    os.replace(tmp, path)


def _frames_for(site, az, frames_dir):
    """Copy a column's frames (focus_refine names: az020_alt29.50_sky000.jpg) in."""
    out = []
    for src in sorted(glob.glob(os.path.join(frames_dir, f"az{az:03d}_alt*"))):
        m = FRAME_NAME.search(os.path.basename(src))
        if not m:
            continue
        rel = os.path.join(FRAMES, f"az{az:03d}", os.path.basename(src))
        os.makedirs(os.path.join(site, os.path.dirname(rel)), exist_ok=True)
        shutil.copyfile(src, os.path.join(site, rel))
        out.append({"alt": float(m.group(2)), "sky": int(m.group(3)) / 100.0, "file": rel})
    return out


def import_run(site, fiducial_files, frames_dir=None, note=""):
    """Columns from CLI fiducial masks, read exactly as `orient --fiducials` reads
    them (orient.from_mask, each file's search ceiling), merged first-wins, with
    each column's frames copied in. A column with frames was focused.

    What from_mask will not fit (open to the floor, a failed measurement, no
    altitude) comes in as not usable, with its reason; a bound keeps its bound."""
    columns = {}
    for path in fiducial_files:
        with open(path) as fh:
            doc = yaml.safe_load(fh) or {}
        horizon = doc.get("horizon") or {}
        search = (doc.get("meta") or {}).get("alt_search") or []
        reasons = {}
        fids = {
            int(f.az): f
            for f in orient.from_mask(
                horizon, ceiling=float(search[1]) if len(search) > 1 else None, excluded=reasons
            )
        }
        for az, e in horizon.items():
            az = int(az)
            if az in columns or not isinstance(e, dict):
                continue
            f = fids.get(az)
            columns[az] = {
                "az": float(az),
                "alt": f.alt if f else (float(e["alt"]) if e.get("alt") is not None else None),
                "bound": bool(f and f.bound),
                "ceiling": f.ceiling if f else None,
                "uncertainty": float(e.get("uncertainty") or DEFAULT_UNCERTAINTY),
                "usable": f is not None,
                "included": f is not None,
                "tags": [],
                "note": "" if f else reasons.get(az, "not a measurement the fit can use"),
            }
    for az, col in columns.items():
        col["frames"] = _frames_for(site, az, frames_dir) if frames_dir else []
        col["method"] = "focused" if col["frames"] else "coarse"
    doc = {"note": note, "columns": [columns[az] for az in sorted(columns)]}
    save(site, doc)
    return doc


def fiducials(doc):
    return [
        orient.Fiducial(
            c["az"], c["alt"], c.get("ceiling"), bound=c.get("bound", False),
            sigma=c.get("uncertainty") or DEFAULT_UNCERTAINTY,
        )  # fmt: skip
        for c in doc["columns"]
        if c.get("included") and c.get("usable", True) and c.get("alt") is not None
    ]


def refit(site, doc=None, near=None):
    """Solve the rotation from every included column against the site's photo mask.

    `near`, a previous solution, makes it a quick local refit (orient.fit).
    Returns {"solution", "yaw_pm", "columns", "summary"}, or None with fewer than
    four included columns (the fit's own minimum)."""
    doc = doc if doc is not None else load(site)
    fids = fiducials(doc)
    if len(fids) < 4:
        return None
    mask = os.path.join(site, PHOTO_MASK)
    _, rows = load_mask(mask)
    pockets = {az: c["pockets"] for az, c in load_columns(mask)[1].items() if c.get("pockets")}
    sample = guide.photo_sample(rows, pockets)
    sol = orient.fit(fids, sample, near=near)
    half = orient.yaw_uncertainty(fids, sample, sol, step=2.0)
    res = sol["residuals"]
    cols = [
        {**{k: c[k] for k in ("az", "alt", "method", "included")}, "residual": res.get(c["az"])}
        for c in doc["columns"]
    ]
    r = [abs(v) for v in res.values()]
    summary = {
        "n": len(r),
        "rms": sol["rms"],
        "median": statistics.median(r) if r else None,
        "max": max(r) if r else None,
        "within_2": sum(1 for v in r if v <= 2.0),
    }
    keys = ("yaw", "pitch", "tilt_mag", "tilt_dir")
    return {
        "solution": {k: sol[k] for k in keys},
        "yaw_pm": half,
        "columns": cols,
        "summary": summary,
    }


TAGS = ("false edge", "pocket", "near object")


def view(site):
    """The columns for the app, each with its residual from the stored fit, and
    its frames by name only. None when the site has no telescope columns."""
    doc = load(site)
    if not doc["columns"]:
        return None
    fit = doc.get("fit")
    res = {c["az"]: c["residual"] for c in (fit or {}).get("columns", ())}
    cols = [
        {
            **{k: c.get(k) for k in ("az", "alt", "uncertainty", "method", "included", "note")},
            "bound": bool(c.get("bound")),
            "usable": c.get("usable", True),
            "tags": list(c.get("tags") or []),
            "residual": res.get(c["az"]),
            "frames": [
                {"alt": f["alt"], "sky": f["sky"], "name": os.path.basename(f["file"])}
                for f in c.get("frames", ())
            ],
        }
        for c in doc["columns"]
    ]
    out = {k: v for k, v in fit.items() if k != "columns"} if fit else None
    return {"note": doc.get("note", ""), "columns": cols, "fit": out}


def _column(doc, az):
    for c in doc["columns"]:
        if c["az"] == az:
            return c
    raise ColumnError(f"no telescope column at az {az:g}")


def _refit_near_last(site, doc):
    """Keep the change, then refit near the last fit, if there is one. A change
    that would leave the fit under its four columns is refused, not saved."""
    if doc.get("fit") and len(fiducials(doc)) < 4:
        raise ColumnError("that leaves fewer than four columns in the fit; it needs four")
    save(site, doc)  # the measurement survives a refit that fails
    last = (doc.get("fit") or {}).get("solution")
    if last:
        doc["fit"] = refit(site, doc, near=last)
        save(site, doc)
    return view(site)


def edit(site, az, included=None, tags=None):
    """Include or exclude a column, or tag it, then refit near the last fit, if any."""
    doc = load(site)
    c = _column(doc, az)
    if included and not c.get("usable", True):
        raise ColumnError(f"az {az:g} is not a measurement the fit can use: {c.get('note')}")
    if included is not None:
        c["included"] = bool(included)
    if tags is not None:
        bad = [t for t in tags if t not in TAGS]
        if bad:
            raise ColumnError(f"unknown tags {bad}; known: {', '.join(TAGS)}")
        c["tags"] = list(tags)
    return _refit_near_last(site, doc)


def fit_all(site):
    """The full search: after an import, or when a local refit may be lost. Minutes."""
    doc = load(site)
    if not doc["columns"]:
        raise ColumnError("this site has no telescope columns to fit")
    fit = refit(site, doc)
    if fit is None:
        raise ColumnError("fewer than four columns are included; the fit needs four")
    doc["fit"] = fit
    save(site, doc)
    return view(site)


def frame_path(site, az, name):
    """The file of one of a column's frames, by its name; None if there is none."""
    if not FRAME_NAME.fullmatch(name):
        return None
    path = os.path.join(site, FRAMES, f"az{int(az):03d}", name)
    return path if os.path.isfile(path) else None


def sky_fraction(size, p1, p2, sky):
    """Share of a frame on `sky`'s side of the line through p1 and p2.

    Points are (x, y) in 0-1 image fractions; `size` is the image's (w, h) in
    pixels, so the area is measured in pixels, not in stretched fractions."""
    w, h = size
    ys, xs = np.mgrid[0:200, 0:200]
    px, py = (xs + 0.5) / 200 * w, (ys + 0.5) / 200 * h
    (x1, y1), (x2, y2), (sx, sy) = ((x * w, y * h) for x, y in (p1, p2, sky))
    side = lambda x, y: (x2 - x1) * (y - y1) - (y2 - y1) * (x - x1)  # noqa: E731
    if side(sx, sy) == 0 or (x1, y1) == (x2, y2):
        raise ColumnError("the sky point must sit off the edge line")
    return float((np.sign(side(px, py)) == np.sign(side(sx, sy))).mean())


def click(site, az, name, p1, p2, sky):
    """The edge as clicked in one frame: it becomes the column's measurement."""
    from PIL import Image

    from .judge import FRAME_DEG

    doc = load(site)
    c = _column(doc, az)
    frame = next((f for f in c.get("frames", ()) if os.path.basename(f["file"]) == name), None)
    if frame is None:
        raise ColumnError(f"az {az:g} has no frame {name}")
    with Image.open(os.path.join(site, frame["file"])) as im:
        frac = sky_fraction(im.size, p1, p2, sky)
    c["alt"] = round(frame["alt"] - (frac - 0.5) * FRAME_DEG, 3)
    c.update(method="clicked", bound=False, ceiling=None, usable=True, note="")
    return _refit_near_last(site, doc)


def _record(c, residual):
    """A column as the fit's record, with no None in it: the mask writes meta by
    repr, and a None would land as the string 'None' (E-06)."""
    r = {"az": c["az"], "used": c["included"]}
    if c.get("alt") is not None:
        r["alt"] = c["alt"]
    if residual is not None:
        r["residual"] = residual
    if c.get("bound"):
        r["bound"] = True
    if not c["included"]:
        r["reason"] = c.get("note") or "excluded"
    return r


def write_orientation(site, path):
    """The stored fit as a mask meta `terminus horizon --solution` reads, with the
    columns it used so the disc can draw them."""
    doc = load(site)
    fit = doc.get("fit")
    if not fit:
        raise ColumnError("fit the columns before applying an orientation")
    res = {c["az"]: c["residual"] for c in fit["columns"]}
    meta = {
        **fit["solution"],
        "oriented": True,
        "fit_settled": True,
        "fit_fiducials": [_record(c, res.get(c["az"])) for c in doc["columns"]],
        "note": f"telescope fit applied in the app; {doc.get('note', '')}".strip("; "),
    }
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        yaml.safe_dump({"meta": meta}, fh, sort_keys=False)
    os.replace(tmp, path)
