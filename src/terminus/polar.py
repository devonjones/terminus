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
import glob
import html
import io
import math
import os
import re

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
  .banner {{ margin:0 0 16px; padding:11px 14px; border-radius:5px; font-size:14px;
            border:1px solid; }}
  .banner.warn {{ border-color:#7a4b12; background:#241a0d; color:#f0c07a; }}
  .banner.ok {{ border-color:#2a4a2c; background:#111c12; color:#9fd0a2; }}
  .banner code {{ font-family:ui-monospace,Menlo,Consolas,monospace; font-size:13px; }}
  .diag {{ margin-top:26px; padding-top:16px; border-top:1px solid var(--rule); }}
  .diag h2 {{ font-size:15px; margin:0 0 4px; font-weight:650; }}
  .diag p.note {{ color:var(--dim); font-size:13px; margin:0 0 14px; }}
  .facts {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(210px,1fr)); gap:4px 22px;
           font:13px/1.7 ui-monospace,Menlo,Consolas,monospace; color:var(--dim);
           margin:0 0 18px; }}
  .facts b {{ color:var(--ink); font-weight:600; }}
  .strip {{ margin:0 0 18px; }}
  .strip h3 {{ font:600 13px ui-monospace,Menlo,Consolas,monospace; color:var(--ink);
              margin:0 0 6px; }}
  .strip .films {{ display:flex; gap:5px; overflow-x:auto; padding-bottom:6px; }}
  .strip figure {{ margin:0; flex:0 0 auto; }}
  .strip img {{ display:block; border-radius:3px; border:1px solid var(--rule); }}
  .strip figure.at img {{ border-color:{edge}; border-width:2px; }}
  .strip figcaption {{ font:11px/1.4 ui-monospace,Menlo,monospace; color:var(--dim);
                      text-align:center; margin-top:3px; }}
  .strip figure.at figcaption {{ color:{edge}; }}
</style>
<div class="wrap">
<h1>{title}</h1>
<p class="sub">{subtitle}</p>
{banner}
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
{diagnostics}
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


def _banner(meta):
    """The settle verdict, stated where nobody can miss it.

    A run that stopped with the yaw still moving rendered EXACTLY like a
    converged one — same disc, same subtitle, same table — because the verdict
    lived in a stderr line the reader never sees and a YAML key nobody opens.
    The 2026-08-12 night run stopped at spread 3.75 deg against a 1 deg rule
    and its page was indistinguishable from a good one.
    """
    settled = meta.get("fit_settled")
    if settled is None:
        return ""
    if settled:
        return '<p class="banner ok">The yaw settled: this fit met its stability rule.</p>'
    return (
        '<p class="banner warn"><strong>This run did not settle.</strong> The yaw was still '
        "moving when the run stopped, so the orientation below is provisional and the mask "
        "is marked UNORIENTED. Measure more columns (<code>--max-columns</code>), or look "
        "for a column the fit cannot reconcile among the residuals.</p>"
    )


FRAME_RE = re.compile(r"az(\d+)_alt(-?\d+(?:\.\d+)?)_lum(-?\d+(?:\.\d+)?)\.(?:png|jpg)$")


def _fmt(value, spec="", dash="&#8212;"):
    """Format a value, or an em dash when it is absent.

    Absent and zero are different claims and a bare f-string cannot tell them
    apart (M-19): `sky_ref: None` means nobody measured a reference, not that
    the sky was black.
    """
    if value is None or value == "":
        return dash
    try:
        return format(float(value), spec) if spec else html.escape(str(value))
    except (TypeError, ValueError):
        return html.escape(str(value))


def _is_night(attempt):
    """Was this attempt measured on the star-mode imaging channel?

    The channel decides which fields of a record mean anything: a night row has
    no open-sky reference and no obstruction type, and reading a day row's
    fields off it silently reports the wrong sky.
    """
    return str(attempt.get("channel") or "").startswith("star")


def _num(value):
    """A record field as a float, or None when it is not one.

    `_fmt` already treats an unparseable field as absent, but the statistics
    and the sort keys reach for `float()` directly and run FIRST — so a
    hand-edited `sky_ref: ""` or `sun_alt: "n/a"` raised out of the facts grid
    and took down a report that would have rendered those same fields fine.
    `_load_attempts` is deliberate about surviving a damaged checkpoint; this
    is the other half of that promise.
    """
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _run_facts(meta, attempts, private):
    """Provenance and the run's shape, as a fact grid.

    What a stranger's report has to answer before anything else can be judged:
    which code measured it, under what sky, on which channel, and how many
    attempts it took to get the columns the fit ended up using.
    """
    verdicts = {}
    for a in attempts:
        verdicts[a.get("verdict") or "?"] = verdicts.get(a.get("verdict") or "?", 0) + 1
    channels = sorted({a.get("channel") for a in attempts if a.get("channel")})
    suns = [v for v in (_num(a.get("sun_alt")) for a in attempts) if v is not None]
    # DAY ROWS ONLY. The night channel has no open-sky reference of its own, and
    # the run state carries the last DAY value across the boundary unchanged —
    # so a night row's `sky_ref` is a stale daylight number, and quoting it as
    # that column's conditions invents a measurement nobody made (M-19).
    # PAIRED AND IN ORDER. The reference and the Sun have to be read off the
    # SAME rows in the SAME sequence to say anything about how one moved with
    # the other; a min and a max carry no direction at all, and `sky_ref` is a
    # sawtooth by construction — the run ratchets it up over bright columns and
    # the staleness refresh lets it drop — so a brightening run has exactly the
    # same spread as a darkening one. The checkpoint is append-only, so list
    # order is time order.
    day = [
        (_num(a.get("sky_ref")), _num(a.get("sun_alt")))
        for a in attempts
        if _num(a.get("sky_ref")) is not None and not _is_night(a)
    ]
    refs = [r for r, _ in day]

    facts = [
        ("terminus", _fmt(meta.get("terminus_version"))),
        ("detector backend", _fmt(meta.get("backend"))),
        ("fit", f"yaw {_fmt(meta.get('yaw'), '.2f')}&deg; "
                f"pitch {_fmt(meta.get('pitch'), '.2f')}&deg; "
                f"tilt {_fmt(meta.get('tilt_mag'), '.2f')}&deg;"),  # fmt: skip
        ("rms / columns", f"{_fmt(meta.get('fit_rms'), '.2f')}&deg; over "
                          f"{len(meta.get('fit_columns') or [])}"),  # fmt: skip
        ("settled", "no" if meta.get("fit_settled") is False else
                    ("yes" if meta.get("fit_settled") else "&#8212;")),  # fmt: skip
    ]
    if not private:
        facts.append(("measured", _fmt(meta.get("measured"), dash="an unrecorded date")))
        facts.append(("site", f"{_fmt(meta.get('lat'), '.4f', '?')}, "
                              f"{_fmt(meta.get('lon'), '.4f', '?')}"))  # fmt: skip
    if attempts:
        facts.append(("attempts", f"{len(attempts)} for "
                                  f"{len(meta.get('fit_columns') or [])} used"))  # fmt: skip
        facts.append(("verdicts", ", ".join(f"{k} {v}" for k, v in sorted(verdicts.items()))))
        facts.append(("channels", ", ".join(html.escape(str(c)) for c in channels) or "&#8212;"))
    if suns:
        facts.append(("sun altitude", f"{min(suns):+.1f}&deg; to {max(suns):+.1f}&deg;"))
    if refs:
        # REPORTED, NOT DIAGNOSED. A large move in the open-sky reference is
        # worth a reader's attention — the day floor already refuses verdicts
        # taken below it — but it is not evidence of any particular cause, and
        # this line has been rewritten twice for claiming one it could not
        # support. The Sun's own travel over the same rows is the only
        # discrimination offered, because that much IS in the record; anything
        # further belongs to whoever looks at the frames.
        note = ""
        moved = refs[-1] - refs[0]
        if abs(moved) > 60:
            went = "fell" if moved < 0 else "rose"
            # The Sun over the SAME rows, or nothing. A day-only reference span
            # judged against every attempt's Sun would score the night half's
            # descent against it and call every mixed session "expected".
            sun_first, sun_last = day[0][1], day[-1][1]
            sun_moved = (
                sun_last - sun_first if sun_first is not None and sun_last is not None else None
            )
            together = sun_moved is not None and abs(sun_moved) > 5.0 and (sun_moved < 0) == (moved < 0)  # fmt: skip
            note = (
                f" ({went} while the Sun {'fell' if sun_moved < 0 else 'rose'} "
                f"{abs(sun_moved):.1f}&deg; with it &mdash; twilight does this)"
                if together
                else f" ({went} without the Sun accounting for it)"
            )
        facts.append(("sky reference", f"{refs[0]:.1f} to {refs[-1]:.1f} over the day columns" + note))  # fmt: skip
    return (
        '<div class="facts">' + "".join(f"<div><b>{k}</b> {v}</div>" for k, v in facts) + "</div>"
    )


def _trail_table(meta):
    """The yaw across refits: converging and thrashing look nothing alike.

    A single final yaw cannot distinguish a fit that walked steadily in from
    one that is still bouncing, and the stopping rule is defined on exactly
    this sequence — so the evidence for the rule's verdict belongs on the page
    next to it.
    """
    trail = meta.get("fit_trail") or []
    if not trail:
        return ""
    rows = "".join(
        f"<tr><td>{_fmt(s.get('az'), '.0f')}</td><td>{_fmt(s.get('yaw'), '.2f')}</td>"
        f"<td>{_fmt(s.get('rms'), '.2f')}</td><td>{_fmt(s.get('n'))}</td>"
        f"<td>{_fmt(s.get('spread'), '.2f')}</td></tr>"
        for s in trail
    )
    return (
        '<details class="cols" open><summary>Yaw across refits '
        f"({len(trail)} fits)</summary><table>"
        "<tr><th>after az</th><th>yaw</th><th>rms</th><th>n</th><th>spread</th></tr>"
        + rows
        + "</table></details>"
    )


def _attempts_table(attempts, private):
    """Every attempt the run made, including the ones that produced nothing.

    The fiducial table shows what the FIT used; this shows what the RUN did.
    2026-08-12 made 39 attempts and gave the fit 12 columns. Nine produced no
    value at all (6 inconclusive, 3 failed); six are the same azimuth measured
    again on a later night or after `--re-measure`; the rest produced a value
    the fit never used. All three kinds are evidence — the failures say whether
    the mount, the sky or the detector is at fault, the repeats are where one
    direction was answered two different ways, and a measured column the fit
    ignored is a question in itself.
    """
    if not attempts:
        return ""
    head = ["az", "verdict", "alt", "channel", "sun", "sky ref", "secs"]
    if not private:
        head.insert(1, "at")
    rows = []
    for a in sorted(attempts, key=lambda d: (str(d.get("t") or ""), _num(d.get("az")) or 0.0)):
        cells = [
            _fmt(a.get("az"), ".0f"),
            _fmt(a.get("verdict"), dash="?"),
            _fmt(a.get("alt"), ".1f"),
            # NOT html.escape of a default entity: escaping "&#8212;" yields the
            # literal "&amp;#8212;" on the page. The dash is markup the caller
            # supplies, the value is data that gets escaped — `_fmt` keeps the
            # two apart.
            _fmt(a.get("channel")),
            _fmt(a.get("sun_alt"), "+.1f"),
            # Night rows have no reference of their own; whatever sits in the
            # field crossed the twilight boundary from the last day column.
            _fmt(None if _is_night(a) else a.get("sky_ref"), ".1f"),
            _fmt(a.get("secs"), ".0f"),
        ]
        if not private:
            cells.insert(1, _fmt(a.get("t")))
        dim = "" if a.get("verdict") in ("edge", "blocked") else ' class="unused"'
        rows.append(f"<tr{dim}>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
    return (
        f'<details class="cols" open><summary>Every attempt ({len(attempts)})</summary><table>'
        "<tr>"
        + "".join(f"<th>{h}</th>" for h in head)
        + "</tr>"
        + "".join(rows)
        + "</table></details>"
    )


def _frame_strips(attempts, frames_dir, px=110, quality=72, budget_kb=6000):
    """The scan ladder for each column, as embedded thumbnails.

    The measurement is a brightness step, and a brightness step has no idea
    what made it. Cloud, canopy and a lit wall all make honest ones.

    2026-08-12 is why this is here rather than in a backlog. Three NE columns
    came back 7-11 deg above what the photograph puts there, a fourth beside
    them landed within 0.2, and the run never settled. Reading the numbers alone, three different stories fit — cloud at
    sunset, a detector fault, or a yaw error moving which photo column the
    residual is even measured against — and the record cannot separate them:
    az 41 (day, visible cloud in its frames) overshot 10.9, while az 39 (night,
    the same treeline, no cloud available) overshot 7.5 on its own.

    So the honest conclusion from the numbers is that they do not identify the
    fault, which is exactly when a person wants to look. The frames are what
    they look at, and a stranger cannot post them to us one at a time.

    The frame at the recorded edge is outlined, which is the whole question a
    reader is asking: is that where the terrain actually starts?

    `budget_kb` caps the embedded bytes by dropping WHOLE COLUMNS, never a
    ladder's middle: a partial ladder invites exactly the wrong reading, since
    the question is where along the ladder the sky stops. Whatever is dropped
    is named on the page (a cap nobody is told about reads as "this is all
    there was"), and `--frame-px`/`--no-frames` are the knobs either way.
    """
    if not frames_dir or not os.path.isdir(frames_dir):
        return ""
    from PIL import Image

    by_az = {}
    for path in glob.glob(os.path.join(frames_dir, "*.png")) + glob.glob(
        os.path.join(frames_dir, "*.jpg")
    ):
        m = FRAME_RE.search(os.path.basename(path))
        if m:
            by_az.setdefault(int(m.group(1)), []).append(
                (float(m.group(2)), float(m.group(3)), path)
            )
    if not by_az:
        return ""

    # LAST wins, not first. The checkpoint is append-only and its own rule is
    # that a later line supersedes (`--re-measure` exists to force exactly
    # that), so keeping the first would head the strip with the superseded
    # altitude and outline the wrong frame — on precisely the columns someone
    # re-measured because they distrusted the old answer.
    edge_at = {}
    for a in attempts:
        az_n, alt_n = _num(a.get("az")), _num(a.get("alt"))
        if az_n is not None and alt_n is not None:
            edge_at[int(round(az_n))] = alt_n

    strips = []
    shown = spent = 0
    dropped = []
    unreadable = set()
    for az in sorted(by_az):
        if spent >= budget_kb * 1024:
            dropped.append(az)
            continue
        frames = sorted(by_az[az], key=lambda t: -t[0])
        edge = edge_at.get(az)
        films = []
        for alt, lum, path in frames:
            try:
                im = Image.open(path).convert("RGB")
            except OSError:
                # Unreadable is not absent, and it is not "never scanned"
                # either. Counting only rendered frames stopped the summary
                # OVERCLAIMING; saying nothing at all makes a corrupt column
                # indistinguishable from one the run never visited. Same ledger
                # `_load_attempts` keeps: reject it, count it, state it.
                unreadable.add(az)
                continue
            w, h = im.size
            # reducing_gap lets PIL pre-shrink by an integer factor before the
            # good filter runs: measured 37% off a 412-frame render (23.6 s to
            # 14.7 s), for +0.3% bytes and a max channel difference of 2/255.
            # It is an approximation, not the same pixels — which is a fair
            # trade for a thumbnail and would not be for a measurement.
            im = im.resize((px, max(1, round(h * px / w))), Image.LANCZOS, reducing_gap=2.0)
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=quality, optimize=True)
            uri = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
            # THE BUDGET COUNTS WHAT LANDS ON THE PAGE. Charging the JPEG's own
            # bytes undercounts by the base64 4/3, so a page announcing a 6 MB
            # ceiling shipped 8.2 MB — and at --frame-px 800, 9.8 MB, which is
            # precisely the "page someone tries to mail" this cap exists for.
            spent += len(uri)
            # Bracket rather than match: the edge is bisected between samples,
            # so no frame sits exactly on it.
            at = ' class="at"' if edge is not None and abs(alt - edge) <= 2.6 else ""
            films.append(
                f'<figure{at}><img src="{uri}" width="{px}" '
                f'alt="az {az} alt {alt:.1f}" loading="lazy">'
                f"<figcaption>{alt:.0f}&deg;<br>{lum:.0f}</figcaption></figure>"
            )
        if films:
            shown += len(films)
            note = f" &mdash; edge at {edge:.1f}&deg;" if edge is not None else ""
            joined = "".join(films)
            strips.append(
                f'<div class="strip"><h3>az {az:03d}{note}</h3>'
                f'<div class="films">{joined}</div></div>'
            )
    lost = sorted(unreadable - {a for a in unreadable if any(f"az {a:03d}" in x for x in strips)})
    bad = (
        f'<p class="note">az {", ".join(str(a) for a in lost)}: '
        f"{'a frame is' if len(lost) == 1 else 'frames are'} on disk but could not be "
        "decoded, so nothing is shown for "
        f"{'it' if len(lost) == 1 else 'them'}.</p>"
        if lost
        else ""
    )
    cut = (
        f'<p class="note">Stopped at the {budget_kb / 1024:.0f} MB frame budget: '
        f"az {', '.join(str(a) for a in dropped)} "
        f"{'is' if len(dropped) == 1 else 'are'} not shown. A smaller --frame-px "
        "fits more columns under it; the full ladders are in the frames "
        "directory beside the mask.</p>"
        if dropped
        else ""
    )
    return (
        '<details class="cols" open><summary>Scan frames '
        f"({shown} from {len(strips)} columns, highest altitude "
        "first; the number under each is its measured brightness. A column "
        "measured more than once has every run's ladder here, so a repeated "
        "altitude is a repeated visit, not a duplicate)</summary>"
        + cut
        + bad
        + "".join(strips)
        + "</details>"
    )


def diagnostics(meta, attempts=(), frames_dir=None, frame_px=110, private=False, notes=()):
    """Everything needed to debug a run someone else made, on the same page.

    The report was a result viewer: it showed what the fit concluded and
    nothing about the conditions it concluded under. That is enough to admire
    a good run and useless for triaging a bad one, which is the case that
    actually needs another pair of eyes.
    """
    strips = _frame_strips(attempts, frames_dir, frame_px) if frames_dir else ""
    parts = [
        _run_facts(meta, attempts, private),
        _trail_table(meta),
        _attempts_table(attempts, private),
        strips,
    ]
    body = "".join(p for p in parts if p)
    if not body:
        return ""
    # DESCRIBE THE PAGE IN HAND, not the feature. Promising "the site, the
    # clock and the frames" on a page rendered --no-frames from a mask with no
    # site is the same class of lie the settle banner exists to stop.
    if private:
        note = "Redacted: no coordinates, dates or clock times."
    else:
        has = ["the site"] if meta.get("lat") is not None else []
        has += ["the clock"] if any(a.get("t") for a in attempts) else []
        has += ["the frames"] if strips else []
        note = (
            "Includes " + ", ".join(has) + ": hand this to someone who can help."
            if has
            else "Everything this run recorded: hand it to someone who can help."
        )
    warn = "".join(f'<p class="banner warn">{html.escape(str(n))}</p>' for n in notes if n)
    return (
        f'<section class="diag"><h2>Run diagnostics</h2><p class="note">{note}</p>'
        f"{warn}{body}</section>"
    )


def page(rows, solution, image=None, coverage=None, fiducials=(), meta=None,
         size=SIZE, floor=FLOOR_DEG, title="terminus horizon",
         attempts=(), frames_dir=None, frame_px=110, private=False, notes=()):  # fmt: skip
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

    where = "an undisclosed site" if private else f"{meta.get('lat', '?')},{meta.get('lon', '?')}"
    when = "an undisclosed date" if private else meta.get("measured", "an unrecorded date")
    footer = html.escape(
        f"Measured with terminus from {where} on {when}. Position-specific: a near "
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
        banner=_banner(meta),
        diagnostics=diagnostics(meta, attempts, frames_dir, frame_px, private, notes),
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
