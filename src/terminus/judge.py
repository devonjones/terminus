"""The horizon judge: where the sky ends, read inside single frames.

A column profile is a run of exposures taken over minutes, and drifting cloud
changes the sky's brightness between them, so comparing one exposure with the
next misreads cloud as terrain (2026-10-06: 3 of 4 overcast columns wrong).
Inside ONE exposure, sky and terrain share the same instant, so the judge reads
the edge there:

frame   The best straight split of the frame (any angle: EQ frames rotate
        against the horizon). If its two sides differ enough, it is an edge
        and the side that looks like the column's open sky is sky; otherwise
        the frame is all sky or all terrain, by the same comparison. Brightness
        alone fails by day (sunlit terrain outshines the sky, shaded siding
        matches it), so the comparison uses colour as well.
column  The edge is the last upward crossing of 50% sky. A line that halves a
        frame passes through its centre at any rotation, so that crossing needs
        no "up" in the image (m110, terminus-77). Inside an edge frame the sky
        fraction becomes altitude assuming the frame's long side runs along
        altitude (FRAME_DEG), which holds to 0.1-0.3 deg on the S50's frames.

Raw frames are the S50's GRBG Bayer (red at [0,1], blue at [1,0]).
"""

import math
from dataclasses import dataclass
from typing import Literal

import numpy as np

FRAME_DEG = 1.276  # the S50 frame's long side on the sky (1920 px at 2.39"/px)
POOL = 12  # Bayer cells averaged per feature cell
EDGE_LOG_L = 0.15  # a split is an edge if the sides differ this much in log brightness...
EDGE_CHROMA = 0.15  # ...or this much in (B-R)/L (by day)
SKY_LOG_L = 0.35  # a whole frame is sky if within this of the column's open sky...
SKY_CHROMA = 0.15  # ...and within this in (B-R)/L
ANGLE_STEP_DEG = 3


def features(raw, pool=POOL):
    """(rows, cols, 3): log brightness, (B-R)/L and (G-R)/L, pooled."""
    a = np.asarray(raw, np.float32)
    r, g, b = a[0::2, 1::2], (a[0::2, 0::2] + a[1::2, 1::2]) / 2, a[1::2, 0::2]
    h, w = (r.shape[0] // pool) * pool, (r.shape[1] // pool) * pool

    def cells(x):
        return x[:h, :w].reshape(h // pool, pool, w // pool, pool).mean((1, 3))

    r, g, b = cells(r), cells(g), cells(b)
    lum = np.maximum((r + g + b) / 3, 1.0)
    return np.stack([np.log(lum), (b - r) / lum, (g - r) / lum], -1)


@dataclass
class Split:
    angle: float  # degrees, the normal from side a to side b in image coordinates
    a: np.ndarray  # mean features of each side
    b: np.ndarray
    frac_a: float  # side a's share of the frame

    def contrast(self, night):
        d = np.abs(self.a - self.b)
        return d[0] > EDGE_LOG_L or (not night and d[1] > EDGE_CHROMA)


def best_split(f, step_deg=ANGLE_STEP_DEG):
    """The straight line that best separates the frame's features into two sides."""
    h, w, _ = f.shape
    yy, xx = np.mgrid[0:h, 0:w]
    yy, xx = (yy - (h - 1) / 2) / h, (xx - (w - 1) / 2) / h
    flat = f.reshape(-1, f.shape[-1])
    scale = flat.std(0) + 1e-6
    n = len(flat)
    edge = max(1, n // 50)  # a sliver at the border is not a split
    best = None
    for th in np.radians(np.arange(0, 180, step_deg)):
        proj = (xx * math.sin(th) - yy * math.cos(th)).ravel()
        order = np.argsort(proj)
        fs = flat[order] / scale
        cs = np.cumsum(fs, 0)
        i = np.arange(1, n)
        m1, m2 = cs[:-1] / i[:, None], (cs[-1] - cs[:-1]) / (n - i)[:, None]
        score = ((m1 - m2) ** 2).sum(1) * i * (n - i) / n**2
        j = int(np.argmax(score[edge:-edge])) + edge
        if best is None or score[j] > best[0]:
            best = (score[j], math.degrees(th), m1[j] * scale, m2[j] * scale, (j + 1) / n)
    _, angle, a, b, frac = best
    return Split(angle, a, b, frac)


def _like_sky(side, ref, night):
    near = abs(side[0] - ref[0]) <= SKY_LOG_L
    return near if night else near and abs(side[1] - ref[1]) <= SKY_CHROMA


@dataclass
class Verdict:
    kind: Literal["sky", "terrain", "edge"]
    sky: float  # fraction of the frame that is sky: 1 or 0 unless an edge


def judge_frame(raw, ref, night=False):
    """One frame against its column's open-sky features `ref` (mean of a sky frame)."""
    return judge_features(features(raw), ref, night)


def judge_features(f, ref, night=False):
    """One frame, reduced by `features`, against its column's open-sky `ref`.
    At night the colour ratios are noise, so only brightness decides."""
    s = best_split(f)
    if s.contrast(night):
        # The side nearer the column's sky is sky, if it looks like sky at all:
        # two kinds of leaf split cleanly too, and neither side is sky.
        da = np.abs(s.a - ref)[: 1 if night else 2].sum()
        db = np.abs(s.b - ref)[: 1 if night else 2].sum()
        near, frac = (s.a, s.frac_a) if da < db else (s.b, 1.0 - s.frac_a)
        return Verdict("edge", frac) if _like_sky(near, ref, night) else Verdict("terrain", 0.0)
    whole = _mean(f)
    return Verdict("sky", 1.0) if _like_sky(whole, ref, night) else Verdict("terrain", 0.0)


def sky_reference(raw):
    """A column's open-sky features: the mean of a frame known to be sky."""
    return _mean(features(raw))


def _mean(f):
    return f.reshape(-1, 3).mean(0)


def judge_column(frames, ref, night=False):
    """[(alt, raw)] down one column, judged against `ref` (sky_reference of a frame
    known to be open sky) -> (Edge, [(alt, Verdict)]).

    From the top down, each frame against the nearest sky above it: sky brightens
    and whitens toward the horizon, so one reference taken high up calls low sky
    terrain. The reference is never guessed from the top frame: a column blocked
    above it would then read sky all the way down."""
    return judge_feature_column([(alt, features(raw)) for alt, raw in frames], ref, night)


def judge_feature_column(frames, ref, night=False):
    """`judge_column` on frames already reduced by `features`."""
    out = []
    for alt, f in sorted(frames, key=lambda r: r[0], reverse=True):
        v = judge_features(f, ref, night)
        if v.kind == "sky":
            ref = _mean(f)
        out.append((alt, v))
    return column_edge(out), out


@dataclass
class Edge:
    """A column's horizon: `alt` with `status` "edge"; "above" when the top frame
    is already blocked (the horizon is at least `alt`, that frame's altitude);
    "below" when every frame is sky (it is under the lowest frame, `alt`)."""

    alt: float | None
    status: Literal["edge", "above", "below"]


def column_edge(verdicts):
    """The horizon in one column from [(alt, Verdict)], as an Edge.

    Walking down from the top, the first frame that is not all sky: an edge frame
    places the horizon inside it, a terrain frame puts it halfway to the sky
    frame above. That is the LAST upward crossing into sky, so a pocket of sky
    lower down (under eaves, between branches) is never taken for the horizon.
    """
    rows = sorted(verdicts, key=lambda r: r[0], reverse=True)
    if not rows:
        raise ValueError("no frames to judge")
    for k, (alt, v) in enumerate(rows):
        if v.kind == "sky":
            continue
        if v.kind == "edge":
            return Edge(alt - (v.sky - 0.5) * FRAME_DEG, "edge")
        if v.kind != "terrain":
            raise ValueError(f"unknown verdict kind {v.kind!r}")
        return Edge((alt + rows[k - 1][0]) / 2, "edge") if k else Edge(alt, "above")
    return Edge(rows[-1][0], "below")
