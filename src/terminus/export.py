"""Read/write the measured horizon mask and export to planning-tool formats.

Mask YAML (terminus's own durable artifact, hand-editable after review):

    meta: {measured: '...', lat: .., lon: .., ...}
    horizon:
      0: {alt: 12.3, type: tree}
      5: {alt: 18.0, type: structure}
      10: {alt: 60.0, type: tree, clipped: True, porosity: 0.4, uncertainty: 4.2}
      ...

`alt` and `type` are always present. The rest appear only when a photo-derived
mask supplies them, so a scope-measured mask is byte-for-byte what it always
was. `clipped` marks a column whose obstruction ran off the top of the data — a
lower bound recording where the frame was cropped, never a measurement.

Exports:
  N.I.N.A.  .hrz   -- "az alt" per line, ascending azimuth, '#' comments
  Stellarium .txt  -- same content (referenced from a landscape.ini)
Both conventions: azimuth 0 = true north, increasing toward east; altitude deg.
"""

import datetime

import yaml

# Fields a column may carry beyond altitude and type. All optional, and all
# written only when supplied, so a scope-measured mask looks exactly as it
# always did and every existing reader keeps working.
#
#   clipped      the obstruction reached the top of the data. This records where
#                the FRAME was cropped, not where the horizon is, and is never a
#                measurement — a consumer must treat it as "at least this high".
#   porosity     fraction of the band between first and top obstruction that is
#                still sky. A wall is 0; a gappy canopy is high, and its single
#                altitude misrepresents it in both directions.
#   uncertainty  per-column altitude uncertainty in degrees, already widened for
#                vegetation by type and porosity.
COLUMN_FIELDS = ("clipped", "porosity", "uncertainty")


def _column(value):
    """Normalise a mask value to a dict. Accepts (alt, type) or a full record."""
    if isinstance(value, dict):
        return dict(value)
    alt, typ = value
    return {"alt": alt, "type": typ}


def write_mask(path, mask, skipped, meta):
    """Write the mask YAML. `mask` is {az: (alt, type)} or {az: {...}}."""
    # A photo-derived mask is in the panorama's own azimuth until `terminus
    # orient` solves the yaw, and saying otherwise in the header would invite a
    # planner to point at a horizon rotated by an unknown amount.
    oriented = meta.get("oriented", True) if isinstance(meta, dict) else True
    lines = [
        "# terminus horizon mask (Seestar S50, EQ mode, RA/Dec goto).",
        (
            "# azimuth/altitude are TRUE (polar-aligned); altitude = lowest clear sky."
            if oriented
            else "# !! UNORIENTED: azimuth is the panorama's own, NOT true north. Solve the\n"
            "# !! orientation with `terminus orient` before any planner consumes this."
        ),
        "# type: tree (green/yellow; SEASONAL) | structure (permanent) | open.",
        "# clipped: true = obstruction ran off the top of the data; a lower BOUND,",
        "#          not a measurement. porosity: sky fraction within the canopy",
        "#          band. uncertainty: degrees, already widened for vegetation.",
        f"meta: {meta}",
        "horizon:",
    ]
    for az in sorted(mask):
        col = _column(mask[az])
        parts = [f"alt: {col['alt']}", f"type: {col.get('type', '')}"]
        for key in COLUMN_FIELDS:
            if col.get(key) is not None:
                v = col[key]
                parts.append(f"{key}: {v!r}" if isinstance(v, bool) else f"{key}: {v}")
        lines.append(f"  {az}: {{{', '.join(parts)}}}")
    if skipped:
        lines.append(f"# skipped azimuths (Sun): {sorted(skipped)}")
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")


def load_mask(path):
    """(meta, [(az, alt, type)]) — the shape every exporter consumes.

    Deliberately still three-tuples. The extra per-column fields are reachable
    through `load_columns`; putting them here would change a contract that four
    exporters and the Horizon class already depend on.
    """
    meta, cols = load_columns(path)
    return meta, sorted((az, c["alt"], c.get("type", "")) for az, c in cols.items())


def load_columns(path):
    """(meta, {az: {alt, type, clipped?, porosity?, uncertainty?}}).

    The full record, for consumers that must not treat a clipped column as a
    measurement or must widen a target's margin by a column's uncertainty.
    """
    with open(path) as f:
        doc = yaml.safe_load(f)
    horizon = doc.get("horizon") or {}
    cols = {}
    for az, v in horizon.items():
        if isinstance(v, dict):
            col = {"alt": float(v["alt"]), "type": v.get("type", "") or ""}
            for key in COLUMN_FIELDS:
                if v.get(key) is not None:
                    col[key] = bool(v[key]) if key == "clipped" else float(v[key])
        else:  # bare "az: alt"
            col = {"alt": float(v), "type": ""}
        cols[int(az)] = col
    return doc.get("meta", {}), cols


# Vegetation is not a hard edge and not a fixed one. Its boundary is a band
# rather than a line — gaps with more canopy above them — and it is seasonal: a
# deciduous crown measured in August is not the horizon you get in January. A
# roofline is neither, so it needs no margin.
TREE_BUFFER_DEG = 3.0


def apply_tree_buffer(rows, buffer_deg=TREE_BUFFER_DEG, types=("tree", "vegetation", "plant")):
    """Raise vegetation columns by a margin before export.

    Planning tools take the horizon literally: N.I.N.A. will start an imaging
    run at an altitude the mask calls clear. Around foliage that confidence is
    not warranted, so the exported horizon is deliberately pessimistic there —
    losing a little sky beats losing a night's subframes to a branch.
    """
    out = []
    for az, alt, typ in rows:
        t = (typ or "").lower()
        if any(k in t for k in types):
            alt = min(90.0, alt + buffer_deg)
        out.append((az, alt, typ))
    return out


def _ascending_pairs(rows, tree_buffer=TREE_BUFFER_DEG):
    """(az, alt) ascending, guaranteeing endpoints at 0 and 360 for full wrap.

    The vegetation buffer is applied HERE, not in the callers. Every exporter
    routes through this function, and it is the last point at which a row still
    carries its type — past it, rows are bare (az, alt) numbers. Applying the
    buffer in `export_all` instead left `to_nina_hrz` and `to_stellarium_txt`
    exporting raw altitudes whenever they were imported directly, which is a
    documented public path. The margin is not meant to be optional.
    """
    if tree_buffer:
        rows = apply_tree_buffer(rows, tree_buffer)
    pairs = [(az, alt) for az, alt, _ in rows]
    if not pairs:
        raise ValueError("mask has no horizon points")
    if pairs[0][0] != 0:
        pairs.insert(0, (0, pairs[0][1]))
    if pairs[-1][0] != 360:
        pairs.append((360, pairs[0][1]))  # wrap: 360 mirrors 0
    return pairs


def to_nina_hrz(rows, meta=None, tree_buffer=TREE_BUFFER_DEG):
    pairs = _ascending_pairs(rows, tree_buffer)
    out = ["# terminus horizon for N.I.N.A. (az alt), true-north azimuth."]
    if tree_buffer:
        out.append(f"# vegetation columns raised {tree_buffer:g} deg (seasonal, gappy)")
    if meta:
        out.append(
            f"# measured {meta.get('measured','?')} at {meta.get('lat','?')},{meta.get('lon','?')}"
        )
    out += [f"{az} {alt:g}" for az, alt in pairs]
    return "\n".join(out) + "\n"


def to_stellarium_txt(rows, tree_buffer=TREE_BUFFER_DEG):
    # Stellarium polygonal landscape: same "az alt" list, ascending, no header needed.
    return "\n".join(f"{az} {alt:g}" for az, alt in _ascending_pairs(rows, tree_buffer)) + "\n"


def export_all(mask_path, base_out, tree_buffer=TREE_BUFFER_DEG):
    # Deliberately does NOT buffer here: the exporters do it themselves, so a
    # caller reaching past this wrapper still gets the margin. Buffering in both
    # places would apply it twice.
    meta, rows = load_mask(mask_path)
    hrz, txt = base_out + ".hrz", base_out + ".stellarium.txt"
    with open(hrz, "w") as f:
        f.write(to_nina_hrz(rows, meta, tree_buffer))
    with open(txt, "w") as f:
        f.write(to_stellarium_txt(rows, tree_buffer))
    return hrz, txt


def default_meta(sky_lat, sky_lon, cfg, skipped):
    return {
        "measured": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "lat": sky_lat,
        "lon": sky_lon,
        "az_step": cfg["az_step"],
        "alt_search": [cfg["alt_min"], cfg["alt_max"]],
        "sun_cone_deg": cfg["sun_cone_deg"],
        "clear_thresh": cfg["clear_thresh"],
        "skipped_az": sorted(skipped),
    }
