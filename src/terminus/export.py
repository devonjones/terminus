"""Read/write the measured horizon mask and export to planning-tool formats.

Mask YAML (terminus's own durable artifact, hand-editable after review):

    meta: {measured: '...', lat: .., lon: .., ...}
    horizon:
      0: {alt: 12.3, type: tree}
      5: {alt: 18.0, type: structure}
      ...

Exports:
  N.I.N.A.  .hrz   -- "az alt" per line, ascending azimuth, '#' comments
  Stellarium .txt  -- same content (referenced from a landscape.ini)
Both conventions: azimuth 0 = true north, increasing toward east; altitude deg.
"""

import datetime

import yaml


def write_mask(path, mask, skipped, meta):
    lines = [
        "# terminus horizon mask (Seestar S50, EQ mode, RA/Dec goto).",
        "# azimuth/altitude are TRUE (polar-aligned); altitude = lowest clear sky.",
        "# type: tree (green/yellow; SEASONAL) | structure (permanent) | open.",
        f"meta: {meta}",
        "horizon:",
    ]
    for az in sorted(mask):
        alt, typ = mask[az]
        lines.append(f"  {az}: {{alt: {alt}, type: {typ}}}")
    if skipped:
        lines.append(f"# skipped azimuths (Sun): {sorted(skipped)}")
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")


def load_mask(path):
    with open(path) as f:
        doc = yaml.safe_load(f)
    horizon = doc.get("horizon") or {}
    # {az: {alt, type}} -> sorted list of (az, alt, type)
    rows = []
    for az, v in horizon.items():
        if isinstance(v, dict):
            rows.append((int(az), float(v["alt"]), v.get("type", "")))
        else:  # bare "az: alt"
            rows.append((int(az), float(v), ""))
    return doc.get("meta", {}), sorted(rows)


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
