"""The horizon as a fisheye: one self-contained page you can hand to somebody.

The equirectangular exports (`landscape.py`) are what Stellarium and Sky Safari
eat. This is the view for a person: the whole sky at once, north up, the
photograph carried through the solved rotation so the site appears where the
solution claims it is. A wrong yaw stops being a number and becomes the
neighbour's house in the wrong place.

WHY A DISC AND NOT THE PANORAMA. An equirectangular strip is the natural frame
for the data and a poor one for judging it: azimuth wraps at an arbitrary seam,
and the eye cannot see that a horizon closes. On a disc the horizon is a closed
ring, so a discontinuity is obvious, and "the tall thing is north" is one glance
rather than an arithmetic exercise.

THE DISC REACHES BELOW THE HORIZON. `floor` defaults to -20 degrees, not 0.
Stopping at the horizon crops away the deck, the fence and the near ground —
exactly the foreground that tells a person whether the registration is right —
and it hides that the mosaic has no data down there, which is a real property of
the measurement worth seeing.

THE LAYERS TOGGLE, and that is not decoration. The horizon ring, the telescope's
own columns, and the unphotographed region are three different claims about the
same sky, and the question "does the yellow line follow the roofline" cannot be
answered while the yellow line is drawn on top of it.
"""

import base64
import html
import io
import math

import numpy as np

from .orient import rotate_inverse

# How far below the horizon the disc reaches. The outermost altitude ring sits
# exactly on the rim, so the edge of the picture is a labelled quantity rather
# than wherever the image happened to stop.
FLOOR_DEG = -20.0
SIZE = 1200

# Kept in one place because the page and the caption must agree about them.
EDGE_COLOUR = "#48e078"
BOUND_COLOUR = "#ff5c5c"
HORIZON_COLOUR = "#ffd640"
GAP_COLOUR = (255, 92, 92, 120)
VOID_RGB = (18, 21, 27)
BACKDROP_RGB = (11, 13, 17)


def project(image, coverage, solution, size=SIZE, floor=FLOOR_DEG):
    """Reproject an equirectangular panorama into a polar disc in TRUE azimuth.

    Returns (rgb, gap): the picture, and a boolean marking disc pixels that no
    frame covered. Inverse-mapped — every output pixel asks the panorama what is
    behind it — so the only holes are real ones. See `orient.rotate_inverse`.
    """
    src = np.asarray(image)
    cov = np.asarray(coverage)
    if cov.shape[:2] != src.shape[:2]:
        raise ValueError(
            f"coverage {cov.shape[:2]} does not match the panorama {src.shape[:2]}; "
            "they must come from the same mosaic run"
        )
    sh, sw = cov.shape[:2]
    span = 90.0 - floor
    centre = size // 2
    radius = centre - 1

    ys, xs = np.mgrid[0:size, 0:size]
    dx, dy = xs - centre, ys - centre
    rad = np.hypot(dx, dy)
    inside = rad <= radius
    true_alt = 90.0 - (rad / radius) * span
    # Screen y grows downward and azimuth grows clockwise from north, which is
    # up: atan2(dy, dx) is zero at east, so north needs the +90.
    true_az = (np.degrees(np.arctan2(dy, dx)) + 90.0) % 360.0

    phi, theta = rotate_inverse(
        true_az, true_alt, solution["pitch"], solution["tilt_mag"], solution["tilt_dir"]
    )
    phi = (phi - solution["yaw"]) % 360.0

    sx = np.clip(phi / 360.0 * sw, 0, sw - 1).astype(int)
    sy = np.clip((90.0 - theta) / 180.0 * sh, 0, sh - 1).astype(int)
    have = inside & (cov[sy, sx] > 0)

    rgb = np.zeros((size, size, 3), np.uint8)
    rgb[:] = BACKDROP_RGB
    rgb[inside & ~have] = VOID_RGB
    rgb[have] = src[sy[have], sx[have]][..., :3]
    return rgb, inside & ~have


def disc_xy(az, alt, size=SIZE, floor=FLOOR_DEG):
    """Where an (azimuth, altitude) lands on the disc, in pixels."""
    centre = size // 2
    radius = centre - 1
    r = radius * (90.0 - max(floor, min(90.0, alt))) / (90.0 - floor)
    t = math.radians(az - 90.0)
    return centre + r * math.cos(t), centre + r * math.sin(t)


def _data_uri(array, mode, fmt, **kw):
    from PIL import Image

    buf = io.BytesIO()
    Image.fromarray(array, mode).save(buf, fmt, **kw)
    kind = "jpeg" if fmt == "JPEG" else "png"
    return f"data:image/{kind};base64," + base64.b64encode(buf.getvalue()).decode()


PAGE = """<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
  :root {{ color-scheme: dark; --ink:#e6e9ef; --dim:#98a0b0; --bg:#0b0d11; --rule:#262c36;
           --accent:{horizon}; }}
  body {{ margin:0; padding:28px 20px 56px; background:var(--bg); color:var(--ink);
         font:16px/1.6 ui-sans-serif,system-ui,"Segoe UI",Helvetica,Arial,sans-serif; }}
  .wrap {{ max-width:{size}px; margin:0 auto; }}
  h1 {{ font-size:21px; margin:0 0 4px; font-weight:650; letter-spacing:-.01em; }}
  .sub {{ color:var(--dim); font-size:14px; margin:0 0 18px;
         font-family:ui-monospace,Menlo,Consolas,monospace; }}
  .stage {{ position:relative; line-height:0; border:1px solid var(--rule); border-radius:4px;
           overflow:hidden; background:#000; }}
  .stage img, .stage svg {{ display:block; width:100%; height:auto; }}
  .stage img.base {{ border-radius:50%; }}
  .stage .ov {{ position:absolute; inset:0; transition:opacity .18s ease; opacity:1; }}
  .stage .ov.off {{ opacity:0; }}
  @media (prefers-reduced-motion:reduce) {{ .stage .ov {{ transition:none; }} }}
  .ring circle {{ fill:none; stroke:rgba(255,255,255,.34); stroke-width:1.5; }}
  .rl text {{ fill:rgba(255,255,255,.62); font:500 19px ui-monospace,monospace; }}
  text.cd {{ fill:#fff; font:700 30px ui-sans-serif,system-ui,sans-serif;
            paint-order:stroke; stroke:rgba(0,0,0,.5); stroke-width:4px; }}
  polygon.hz {{ fill:rgba(255,214,64,.10); stroke:{horizon}; stroke-width:3.5; }}
  circle.edg {{ fill:none; stroke:{edge}; stroke-width:4; }}
  g.bnd path {{ fill:none; stroke:{bound}; stroke-width:4; stroke-linecap:round; }}
  .bar {{ display:flex; flex-wrap:wrap; gap:9px; padding:14px 0 0; }}
  button {{ font:600 13px ui-sans-serif,system-ui,sans-serif; cursor:pointer; padding:7px 14px;
           border-radius:999px; border:1px solid var(--accent); background:var(--accent);
           color:#000; }}
  button[aria-pressed="false"] {{ background:transparent; color:var(--accent); }}
  button:focus-visible {{ outline:2px solid #fff; outline-offset:2px; }}
  .key {{ display:flex; flex-wrap:wrap; gap:6px 20px; color:var(--dim); font-size:13px;
         margin:16px 0 0; font-family:ui-monospace,Menlo,monospace; }}
  .key i {{ display:inline-block; width:11px; height:11px; border-radius:50%; margin-right:7px;
           vertical-align:baseline; }}
  footer {{ color:var(--dim); font-size:13px; margin-top:22px; padding-top:14px;
           border-top:1px solid var(--rule); }}
  details.cols {{ margin-top:18px; }}
  details.cols summary {{ cursor:pointer; font:600 14px ui-sans-serif,system-ui,sans-serif;
                          color:var(--dim); }}
  .cols table {{ border-collapse:collapse; margin-top:10px; width:100%;
                font:13px/1.5 ui-monospace,Menlo,Consolas,monospace; }}
  .cols th {{ text-align:left; color:var(--dim); font-weight:600; padding:3px 14px 3px 0;
             border-bottom:1px solid var(--rule); }}
  .cols td {{ padding:3px 14px 3px 0; border-bottom:1px solid var(--rule);
             vertical-align:top; }}
  .cols tr.unused td {{ color:var(--dim); }}
</style>
<div class="wrap">
<h1>{title}</h1>
<p class="sub">{subtitle}</p>
<div class="stage" id="stage">
  <img class="base" src="{base}" alt="{alt_text}">
  <img class="ov off" data-layer="gap" src="{gap}" alt="">
  <svg class="ov" data-layer="grid" viewBox="0 0 {size} {size}" aria-hidden="true">
    <g class="ring">{rings}</g><g class="rl">{ring_labels}</g>{cardinals}
  </svg>
  <svg class="ov" data-layer="hz" viewBox="0 0 {size} {size}" aria-hidden="true">
    <polygon class="hz" points="{horizon_points}"/>
  </svg>
  <svg class="ov" data-layer="pts" viewBox="0 0 {size} {size}" aria-hidden="true">{markers}</svg>
</div>
<div class="bar">
  <button data-t="hz" aria-pressed="true">Horizon</button>
  <button data-t="pts" aria-pressed="{pts_pressed}">Telescope columns</button>
  <button data-t="gap" aria-pressed="false">Not photographed</button>
  <button data-t="grid" aria-pressed="true">Altitude grid</button>
</div>
<p class="key">
  <span><i style="background:{horizon}"></i>horizon from the photographs</span>
  <span><i style="background:{edge}"></i>telescope edge</span>
  <span><i style="background:{bound}"></i>at the scope's tilt ceiling: horizon is <em>at least</em> this high</span>
</p>
{columns}
<footer>{footer}</footer>
</div>
<script>
(function(){{
  var stage = document.getElementById('stage');
  // classList, not .hidden: SVGElement does not inherit HTMLElement's hidden
  // property, so setting el.hidden silently does nothing to an <svg> layer.
  function apply(name, on) {{
    var el = stage.querySelector('[data-layer="' + name + '"]');
    if (el) el.classList.toggle('off', !on);
  }}
  Array.prototype.forEach.call(document.querySelectorAll('button[data-t]'), function (b) {{
    apply(b.dataset.t, b.getAttribute('aria-pressed') === 'true');
    b.addEventListener('click', function () {{
      var on = b.getAttribute('aria-pressed') !== 'true';
      b.setAttribute('aria-pressed', String(on));
      apply(b.dataset.t, on);
    }});
  }});
}})();
</script>
"""


def _columns_table(meta):
    """The fit's own fiducial record, as a table a person can audit.

    Built from `meta.fit_fiducials` — the record `orient` writes of every
    column it was offered: the value, whether it was one-sided, whether the
    fit used it, and the reason when it did not (terminus-53). A mask without
    the record (pre-record runs, plain sweeps) simply has no table; inventing
    rows from the horizon block would show numbers the fit never saw.
    """
    fids = meta.get("fit_fiducials") or []
    if not fids:
        return ""
    body = []
    used_n = sum(1 for f in fids if f.get("used"))
    for f in sorted(fids, key=lambda d: float(d.get("az", 0.0))):
        alt = f.get("alt")
        kind = "bound" if f.get("bound") else ("edge" if alt is not None else "&#8212;")
        res = f.get("residual")
        if f.get("used"):
            status, note = "used", (f"{float(res):+.2f}&deg;" if res is not None else "&#8212;")
        else:
            status = "excluded" if f.get("excluded_by") == "mask" else "not used"
            note = html.escape(str(f.get("reason") or ""))
        cls = "" if f.get("used") else ' class="unused"'
        alt_s = f"{float(alt):.1f}" if alt is not None else "&#8212;"
        body.append(
            f"<tr{cls}><td>{float(f.get('az', 0.0)):g}</td><td>{alt_s}</td>"
            f"<td>{kind}</td><td>{status}</td><td>{note}</td></tr>"
        )
    return (
        f'<details class="cols" open><summary>Telescope columns '
        f"({used_n} used of {len(fids)} offered)</summary><table>"
        "<tr><th>az</th><th>alt</th><th>kind</th><th>in fit</th>"
        "<th>residual / reason</th></tr>" + "".join(body) + "</table></details>"
    )


def page(rows, solution, image=None, coverage=None, fiducials=(), meta=None,
         size=SIZE, floor=FLOOR_DEG, title="terminus horizon"):  # fmt: skip
    """One self-contained HTML page. No network, no assets, no build step.

    `rows` is the ORIENTED mask — already in true azimuth — so the ring is drawn
    from the numbers a planner would consume, not from a re-derivation that could
    drift from them. `image`/`coverage` are optional: without them the disc is a
    flat backdrop, which is all a scope-only sweep can honestly support.
    """
    meta = dict(meta or {})
    rings, ring_labels = [], []
    centre = size // 2
    radius = centre - 1
    for alt in (60, 30, 0, int(floor)):
        r = radius * (90.0 - alt) / (90.0 - floor)
        rings.append(f'<circle cx="{centre}" cy="{centre}" r="{r:.1f}"/>')
        ring_labels.append(f'<text x="{centre + 6}" y="{centre - r - 6:.1f}">{alt}</text>')

    cardinals = []
    for label, az in (("N", 0), ("E", 90), ("S", 180), ("W", 270)):
        x, y = disc_xy(az, floor + 6.0, size, floor)
        cardinals.append(f'<text class="cd" x="{x:.1f}" y="{y:.1f}">{label}</text>')

    pts = []
    for az, alt in sorted((float(a), float(al)) for a, al, *_ in rows):
        x, y = disc_xy(az, alt, size, floor)
        pts.append(f"{x:.1f},{y:.1f}")

    markers = []
    for f in fiducials:
        x, y = disc_xy(float(f.az), float(f.alt), size, floor)
        if getattr(f, "bound", False):
            # An upward double chevron, not a closed marker: the scope stopped at
            # its tilt ceiling, so the claim is "at least this high" and a dot
            # would assert a measurement nobody made.
            markers.append(
                f'<g class="bnd"><path d="M{x:.1f} {y - 6:.1f} l 12 14 M{x:.1f} {y - 6:.1f} l -12 14"/>'
                f'<path d="M{x:.1f} {y + 5:.1f} l 12 14 M{x:.1f} {y + 5:.1f} l -12 14"/></g>'
            )
        else:
            markers.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="9" class="edg"/>')

    if image is not None and coverage is not None:
        rgb, gap = project(image, coverage, solution, size, floor)
        base = _data_uri(rgb, "RGB", "JPEG", quality=84, optimize=True)
        overlay = np.zeros((size, size, 4), np.uint8)
        overlay[gap] = GAP_COLOUR
        gap_uri = _data_uri(overlay, "RGBA", "PNG", optimize=True)
        gap_pct = f"{100.0 * gap.sum() / max(1, (gap | (rgb[..., 0] > 0)).sum()):.1f}"
        alt_text = (
            "The site photographed from the observing position, reprojected so north is up "
            f"and the rim is {abs(floor):.0f} degrees below the horizon."
        )
    else:
        flat = np.zeros((size, size, 3), np.uint8)
        flat[:] = VOID_RGB
        base = _data_uri(flat, "RGB", "JPEG", quality=70, optimize=True)
        gap_uri = _data_uri(np.zeros((size, size, 4), np.uint8), "RGBA", "PNG", optimize=True)
        gap_pct = "0.0"
        alt_text = "The measured horizon, drawn as a ring around the observing position."

    bits = []
    if "yaw" in meta:
        bits.append(f"yaw {float(meta['yaw']):.2f}")
    if meta.get("fit_rms") is not None:
        bits.append(f"rms {float(meta['fit_rms']):.2f}")
    bits.append(f"rim at {floor:+.0f}")
    subtitle = "  ".join(bits) + " degrees"

    footer = html.escape(
        f"Measured with terminus from {meta.get('lat', '?')},{meta.get('lon', '?')} "
        f"on {meta.get('measured', 'an unrecorded date')}. Position-specific: a near "
        "obstruction shifts by degrees for a few metres of observer displacement. "
        f"{gap_pct}% of the disc has no photograph behind it."
    )

    return PAGE.format(
        title=html.escape(title),
        subtitle=html.escape(subtitle),
        alt_text=html.escape(alt_text),
        size=size,
        base=base,
        gap=gap_uri,
        rings="".join(rings),
        ring_labels="".join(ring_labels),
        cardinals="".join(cardinals),
        horizon_points=" ".join(pts),
        markers="".join(markers),
        pts_pressed="true" if markers else "false",
        columns=_columns_table(meta),
        footer=footer,
        horizon=HORIZON_COLOUR,
        edge=EDGE_COLOUR,
        bound=BOUND_COLOUR,
    )


def write_page(path, rows, solution, **kw):
    """Write the page and return its path."""
    with open(path, "w") as fh:
        fh.write(page(rows, solution, **kw))
    return path


def solution_from_meta(meta):
    """Pull the rotation out of an oriented mask's `meta` block.

    `cmd_orient` already records yaw, pitch and both tilt components there, so an
    oriented mask carries everything needed to reproject the photograph and no
    second file has to be kept in step with it.
    """
    missing = [k for k in ("yaw", "pitch", "tilt_mag", "tilt_dir") if meta.get(k) is None]
    if missing:
        raise ValueError(
            "this mask has no solved orientation in its meta (missing "
            + ", ".join(missing)
            + "). Run `terminus orient` first; an unoriented mask has no true azimuth "
            "to draw."
        )
    return {k: float(meta[k]) for k in ("yaw", "pitch", "tilt_mag", "tilt_dir")}


__all__ = ["project", "disc_xy", "page", "write_page", "solution_from_meta", "FLOOR_DEG", "SIZE"]
