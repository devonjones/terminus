"""Horizon ACTUAL and horizon PLANNING, in true coordinates, from one reprojection.

Inputs are panorama-space class maps from the frames' own vote:

actual    terrain-biased: sky only where a strict majority of covering frames
          say sky, and only sky contiguous with the open sky. No altitude clamp:
          true 0 deg is unknown until the fit. Per column the line is the floor
          of the lowest contiguous sky pixel, so it dips into pockets.
planning  terrain wherever ANY covering frame saw terrain within
          SEARCH_ABOVE_DEG above the highest obstruction (wind), run-guarded so a speck
          cannot lift it; never below actual. Where only one frame saw a tree's
          edge, the fixed tree buffer stands in for the movement nobody measured.
fuzz      planning - the highest obstruction: how far the edge was seen to move.

Every true-sky pixel asks the panorama what is behind it (orient.rotate_inverse,
the photo's own mapping), so the photo, the lines and the pockets between them
come from the same pixels. Never read a line in panorama space and rotate it:
under tilt that leans edges sideways.
"""

import numpy as np

from . import skymask
from .export import TREE_BUFFER_DEG
from .orient import rotate_inverse

SEARCH_ABOVE_DEG = 10.0  # how far above the highest obstruction wind-blown terrain is looked for
IDENTITY = {"yaw": 0.0, "pitch": 0.0, "tilt_mag": 0.0, "tilt_dir": 0.0}


def index_map(height, width, solution):
    """For each true (az, alt) pixel, the panorama pixel behind it."""
    taz = (np.arange(width) + 0.5) / width * 360.0
    talt = 90.0 - (np.arange(height) + 0.5) / height * 180.0
    TAZ, TALT = np.meshgrid(taz, talt)
    s = solution
    phi, theta = rotate_inverse(TAZ, TALT, s["pitch"], s["tilt_mag"], s["tilt_dir"])
    phi = (phi - s["yaw"]) % 360.0
    sx = np.clip((phi / 360.0 * width).astype(int), 0, width - 1)
    sy = np.clip(((90.0 - theta) / 180.0 * height).astype(int), 0, height - 1)
    return sy, sx


def true_horizon(actual, strict, cover, coverage, solution=None):
    """{az: {"alt", "type", "planning", "fuzz"}} for every azimuth with an edge."""
    act, strict, cover, cov = (np.asarray(a) for a in (actual, strict, cover, coverage))
    height, width = act.shape
    sy, sx = index_map(height, width, solution or IDENTITY)
    seen = cov[sy, sx] > 0

    def true_sky(cls):
        c = cls[sy, sx]
        valid = seen & (c >= 0)  # UNLABELLED IS NOT GROUND (M-19)
        sky = (c == skymask.SKY_CLASS_ADE20K) & valid  # no true-0 clamp
        return skymask.connected_sky(sky, valid), valid, c

    a_sky, valid, a_cls = true_sky(act)
    s_sky, _, _ = true_sky(strict)
    a_band = skymask.horizon_band(a_sky, valid=valid, run=6)
    s_band = skymask.horizon_band(s_sky, valid=valid, run=6)
    types = skymask.obstruction_classes(a_cls, a_band["top"], valid=valid)
    tcover = cover[sy, sx]
    ppd, row_deg = width / 360.0, 180.0 / height
    mask = {}
    for az in range(360):
        x = int(az * ppd + ppd / 2)
        t = a_band["top"][x]
        if not np.isfinite(t):
            continue
        top_alt = 90.0 - t * row_deg  # the highest obstruction: planning starts here
        rows_sky = np.flatnonzero(a_sky[:, x])
        actual_alt = (
            min(top_alt, 90.0 - (rows_sky.max() + 1) * row_deg) if len(rows_sky) else top_alt
        )
        typ = skymask.type_name(int(types[x]))
        plan = top_alt
        sf = s_band["first"][x]
        if np.isfinite(sf) and 0 < (t - sf) * row_deg <= SEARCH_ABOVE_DEG:
            plan = 90.0 - sf * row_deg  # wind-blown terrain seen above the top edge
        if tcover[int(t), x] < 2 and typ == "tree":
            plan = max(plan, top_alt + TREE_BUFFER_DEG)  # one frame cannot show movement
        mask[az] = {
            "alt": round(actual_alt, 2),
            "type": typ,
            "planning": round(plan, 2),
            "fuzz": round(plan - top_alt, 2),
            # The obstruction runs off the top of the photos: a lower bound.
            "clipped": bool(a_band["clipped"][x]),
        }
    return mask
