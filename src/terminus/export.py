"""Read/write the measured horizon mask and export to planning-tool formats.

Mask YAML (terminus's own durable artifact, hand-editable after review):

    meta: {measured: '...', lat: .., lon: .., ...}
    horizon:
      0: {alt: 12.3, type: tree}
      5: {alt: 18.0, type: structure}
      10: {alt: 60.0, type: tree, clipped: True, gap_fraction: 0.4, uncertainty: 4.2}
      ...

`alt` and `type` are always present. The rest appear only when the run that
wrote them had something to say: `clipped`, `gap_fraction` and `uncertainty`
come from a photo mask, and `type_source` from either instrument whenever it
named a type. A scope mask measured in daylight therefore carries `type_source`
where it once carried nothing — the file is no longer byte-for-byte what it was
before that field existed, which is the point of the field.

`clipped` marks a column whose obstruction ran off the top of the data — a
lower bound recording where the frame was cropped, never a measurement.

`type` may be empty. That means the column was measured but not named, which is
a different statement from `open` and must not be read as one.

Exports:
  N.I.N.A.  .hrz   -- "az alt" per line, ascending azimuth, '#' comments
  Stellarium .txt  -- same content (referenced from a landscape.ini)
  PVsyst   .HOR    -- "az height" per line, free-text comment header
Both conventions: azimuth 0 = true north, increasing toward east; altitude deg.

The picture exporters — a Sky Safari panorama and a complete Stellarium
landscape — live in `landscape.py`. They route through `_ascending_pairs` here,
so the image and the numbers beside it are one horizon rather than two.
"""

import datetime
import math

import yaml

# Fields a column may carry beyond altitude and type. All optional, and all
# written only when supplied, so a scope-measured mask looks exactly as it
# always did and every existing reader keeps working.
#
#   clipped      the obstruction reached the top of the data. This records where
#                the FRAME was cropped, not where the horizon is, and is never a
#                measurement — a consumer must treat it as "at least this high".
#   gap_fraction fraction of the band between first and top obstruction that is
#                still sky. A wall is 0; a gappy canopy is high, and its single
#                altitude misrepresents it in both directions. The field's term
#                (Jonckheere 2004); it was briefly called porosity, which is
#                windbreak vocabulary. Read on load as well, so a mask written
#                before the rename still opens.
#   uncertainty  per-column altitude uncertainty in degrees, already widened for
#                vegetation by type and gap_fraction.
#   type_source  which instrument named the obstruction: "photo" (in-focus
#                segmentation) or "scope" (colour, daylight only). Per column,
#                because a merged mask holds both and they are not equally able
#                to tell a tree from a wall.
COLUMN_FIELDS = ("clipped", "gap_fraction", "uncertainty", "type_source")
# Old spellings still accepted when reading, never written.
_RENAMED = {"gap_fraction": "porosity"}


def _column(value):
    """Normalise a mask value to a dict. Accepts (alt, type) or a full record."""
    if isinstance(value, dict):
        return dict(value)
    alt, typ = value
    return {"alt": alt, "type": typ}


def write_mask(path, mask, skipped, meta):
    """Write the mask YAML. `mask` is {az: (alt, type)} or {az: {...}}."""
    # A photo-derived mask is in the panorama's own azimuth until the yaw is
    # solved against telescope-measured columns, and saying otherwise in the
    # header would invite a planner to point at a horizon rotated by an unknown
    # amount.
    oriented = is_oriented(meta)
    columns = {az: _column(v) for az, v in mask.items()}

    def _present(*keys):
        return any(c.get(k) is not None for c in columns.values() for k in keys)

    photo_fields = _present("clipped", "gap_fraction", "uncertainty")
    sourced = _present("type_source")
    lines = [
        "# terminus horizon mask (Seestar S50, EQ mode, RA/Dec goto).",
        (
            "# azimuth/altitude are TRUE (polar-aligned); altitude = lowest clear sky."
            if oriented
            else "# !! UNORIENTED: azimuth is the panorama's own, NOT true north. Solve the\n"
            "# !! orientation before any planner consumes this."
        ),
        "# type: tree (green/yellow; SEASONAL) | structure (permanent) | open.",
        "#   Empty means the column was measured but NOT NAMED, which is not the",
        "#   same as `open`. Either instrument can leave it empty: the scope reads",
        "#   type from colour and every silhouette is neutral after sunset, and the",
        "#   photo heuristic backend segments no classes at all. See type_source.",
        "# POSITION-SPECIFIC: the horizon from where the tripod stood. Moving a",
        "#   couple of metres NEARER a close obstruction shifts it by degrees (a",
        "#   2 m fence at 5 m: +11.9 closer, -5.9 further); along it, not at all. A",
        "#   distant ridge does not care. Re-measure if you move nearer or further.",
    ]
    # Each group of fields is explained only when the file actually carries it.
    # A mask that has no photo columns should not be handed a paragraph about
    # canopy porosity, and the two groups appear independently now that a scope
    # sweep records its own type_source.
    if photo_fields:
        lines += [
            "# clipped: true = obstruction ran off the top of the data; a lower BOUND,",
            "#          not a measurement. gap_fraction: sky fraction within the canopy",
            "#          band. uncertainty: degrees, already widened for vegetation.",
        ]
    if sourced:
        lines += [
            "# type_source: photo (in-focus segmentation) | scope (daylight colour).",
            "#          Recorded per column, because a merged mask holds both and the",
            "#          two instruments are not equally able to tell tree from wall.",
        ]
    lines += [f"meta: {meta}", "horizon:"]
    for az in sorted(columns):
        col = columns[az]
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
    """(meta, {az: {alt, type, clipped?, gap_fraction?, uncertainty?}}).

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
                raw = v.get(key)
                if raw is None and key in _RENAMED:
                    raw = v.get(_RENAMED[key])  # a mask written before the rename
                if raw is not None:
                    # Each field has its own type: clipped is a flag, type_source
                    # is a name, the rest are numbers. Coercing everything with
                    # float() made a string field unreadable the moment one was
                    # added, which is the kind of breakage that only shows up in
                    # the next feature.
                    if key == "clipped":
                        col[key] = bool(raw)
                    elif key == "type_source":
                        col[key] = str(raw)
                    else:
                        col[key] = float(raw)
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
    # Checked here because this is where every exporter passes. A hand-edited
    # `alt: nan` loads without complaint — `float("nan")` is a valid float — and
    # then each format writes it out in its own way for someone else's parser to
    # mishandle. PVsyst is the sharp case: it treats any line containing text as
    # a comment, so `180 nan` is silently DROPPED and the profile comes back one
    # column short with nothing said. Refusing here is the difference between an
    # error and a quietly incomplete horizon.
    bad = [az for az, alt in pairs if not math.isfinite(alt)]
    if bad:
        raise MaskError(
            f"columns {bad} have a non-finite altitude. A horizon file is consumed by "
            "other software that will either reject it or, worse, drop the column and "
            "carry on. Fix or remove those columns in the mask."
        )
    if pairs[0][0] != 0:
        pairs.insert(0, (0, pairs[0][1]))
    if pairs[-1][0] != 360:
        pairs.append((360, pairs[0][1]))  # wrap: 360 mirrors 0
    return pairs


def to_nina_hrz(rows, meta=None, tree_buffer=TREE_BUFFER_DEG, allow_unoriented=False):
    # Also checked here, because this is a documented direct-import path and its
    # header is the thing that would lie: it declares "true-north azimuth".
    require_oriented(meta, allow_unoriented)
    pairs = _ascending_pairs(rows, tree_buffer)
    out = [
        "# terminus horizon for N.I.N.A. (az alt), true-north azimuth.",
        "# Measured from one spot. Moving NEARER a close obstruction shifts it by",
        "# degrees (2 m fence at 5 m: +11.9 for 2 m closer, -5.9 for 2 m further);",
        "# along it, not at all; and a distant ridge does not care.",
    ]
    if tree_buffer:
        out.append(f"# vegetation columns raised {tree_buffer:g} deg (seasonal, gappy)")
    if meta:
        out.append(
            f"# measured {meta.get('measured','?')} at {meta.get('lat','?')},{meta.get('lon','?')}"
        )
    out += [f"{az} {alt:g}" for az, alt in pairs]
    return "\n".join(out) + "\n"


def to_stellarium_txt(rows, meta=None, tree_buffer=TREE_BUFFER_DEG, allow_unoriented=False):
    # Gated for the same reason as to_nina_hrz, and more urgently: the Stellarium
    # format forbids comments, so an unoriented landscape cannot even carry a
    # warning in the file. It is exported beside to_nina_hrz in __all__, and the
    # first version of this guard covered to_nina_hrz and export_all but not
    # this — two of the three call sites the finding had named.
    require_oriented(meta, allow_unoriented)
    return "\n".join(f"{az} {alt:g}" for az, alt in _ascending_pairs(rows, tree_buffer)) + "\n"


def to_pvsyst_hor(rows, meta=None, tree_buffer=TREE_BUFFER_DEG, allow_unoriented=False):
    """Horizon profile for PVsyst and other solar-siting tools (.HOR).

    An honest nod as much as a feature: the solar industry was doing this
    commercially twenty years before this repo existed. The Solmetric SunEye
    210 — calibrated fisheye, compass, tilt sensor, GPS — has produced horizon
    altitude per degree of azimuth and exported it as .HOR since the 2000s. If
    terminus can hand a mask to that ecosystem it costs a dozen lines, and
    refusing to would be pretending the field started here.

    FORMAT, from PVsyst's own import documentation: text or CSV, one line per
    point, azimuth and height in degrees, columns separated by comma, semicolon,
    tab or space. "All lines containing text are considered comment lines", so
    the header needs no marker character — and a comment carrying latitude and
    longitude is read as the profile's metadata rather than discarded.

    AZIMUTH IS THE THING TO GET RIGHT, and it is not fixed by the format.
    PVsyst's import dialog asks for the rotation direction and the north azimuth
    angle, because its own internal convention is not ours. This writes OUR
    convention — 0 = true north, increasing clockwise toward east — and says so
    in the header, so the person setting that dialog has the answer in front of
    them instead of guessing and getting a mirrored horizon that still looks
    plausible.
    """
    require_oriented(meta, allow_unoriented)
    pairs = _ascending_pairs(rows, tree_buffer)
    out = [
        "Horizon profile measured with terminus.",
        "Azimuth 0 = true north, increasing clockwise toward east; height in degrees.",
        "On import set direction of rotation Clockwise and north azimuth angle 0.",
    ]
    if meta and meta.get("lat") is not None and meta.get("lon") is not None:
        # PVsyst reads a latitude/longitude comment as the profile's metadata.
        out.append(f"Latitude {meta['lat']}, Longitude {meta['lon']}")
    if tree_buffer:
        out.append(f"Vegetation columns raised {tree_buffer:g} degrees (seasonal, gappy).")
    # Fixed decimals, never %g. `format(1e-05, "g")` is "1e-05", which contains
    # a letter, which makes it a COMMENT to PVsyst rather than a malformed
    # number — the column vanishes instead of erroring. Two places rely on this:
    # `_ascending_pairs` refuses non-finite altitudes, and this refuses
    # scientific notation for the finite ones.
    out += [f"{az} {alt:.2f}" for az, alt in pairs]
    return "\n".join(out) + "\n"


class MaskError(ValueError):
    """Something is wrong with the mask file itself.

    Subclasses ValueError so existing `except ValueError` callers keep working,
    and gives the CLI one thing to catch: without it an unparseable flag reached
    the user as a raw traceback while an unoriented mask got a clean message,
    which is backwards — the typo is the one with an easy fix.
    """


class UnorientedMask(MaskError):
    """A mask still in the panorama's own azimuth was asked to be exported.

    Distinct from a plain MaskError so a caller can tell "this horizon is not in
    true azimuth yet" from "I cannot read this file" — the first is a workflow
    step, the second is a mistake.
    """


_TRUE = frozenset(("true", "yes", "y", "on", "1"))
_FALSE = frozenset(("false", "no", "n", "off", "0", ""))


def is_oriented(meta):
    """Is this mask's azimuth true north?

    Absent means yes: a scope-measured mask has no `oriented` key and has always
    been in true azimuth, so silence must keep meaning what it used to.

    Strings and numbers are interpreted rather than compared by identity. The
    mask is documented as hand-editable, so `oriented: 'false'` and `oriented: 0`
    are things a person will actually write, and `meta.get("oriented") is False`
    waves both straight through to a planner.

    A string that is neither RAISES rather than being guessed. Matching only the
    false words fails open — `oriented: flase` would read as true and export a
    horizon rotated by an unknown amount, which is the same defect this function
    exists to prevent, reached from the other side. A typo in a hand-edited file
    is not evidence of orientation, and the one thing that must not happen is
    quietly deciding it is.
    """
    if not isinstance(meta, dict) or "oriented" not in meta:
        return True
    v = meta["oriented"]
    if isinstance(v, str):
        word = v.strip().lower()
        if word in _TRUE:
            return True
        if word in _FALSE:
            return False
        raise MaskError(
            f"mask meta has oriented: {v!r}, which is neither true nor false. "
            f"Use one of {sorted(_TRUE)} or {sorted(_FALSE)} — guessing would risk "
            "exporting a horizon rotated by an unknown amount."
        )
    return bool(v)


def require_oriented(meta, allow_unoriented=False):
    """Refuse to export a mask whose azimuth is not true north.

    A photo-derived mask starts in the panorama's own azimuth and stays there
    until the orientation is solved. Exporting it produces a file that LOOKS
    like every other horizon — the .hrz even declares "true-north azimuth" in
    its header — while being rotated by an unknown amount. A planner then
    refuses targets that are clear and accepts targets that are behind a roof,
    with nothing anywhere to say why.

    The check lives here rather than in the CLI because `export_all` is the one
    place that has both the file and its meta. This is the same lesson as the
    vegetation buffer: a guard that only exists in one caller is not a guard —
    and every exporter must call it, not most of them.
    """
    if not is_oriented(meta) and not allow_unoriented:
        raise UnorientedMask(
            "this mask is UNORIENTED — its azimuth is the panorama's own, not true "
            "north. Solve the orientation against telescope-measured columns first, "
            "or pass --allow-unoriented (allow_unoriented=True) if you know the "
            "azimuths are already true."
        )


def export_all(mask_path, base_out, tree_buffer=TREE_BUFFER_DEG, allow_unoriented=False):
    # Deliberately does NOT buffer here: the exporters do it themselves, so a
    # caller reaching past this wrapper still gets the margin. Buffering in both
    # places would apply it twice.
    meta, rows = load_mask(mask_path)
    require_oriented(meta, allow_unoriented)
    hrz, txt = base_out + ".hrz", base_out + ".stellarium.txt"
    with open(hrz, "w") as f:
        # Forward the override: to_nina_hrz checks again for the benefit of
        # direct callers, and without this an explicit allow_unoriented would be
        # granted here and then refused one line later.
        f.write(to_nina_hrz(rows, meta, tree_buffer, allow_unoriented=True))
    with open(txt, "w") as f:
        f.write(to_stellarium_txt(rows, meta, tree_buffer, allow_unoriented=True))
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
