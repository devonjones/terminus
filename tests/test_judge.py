"""The horizon judge on real frames: overcast night 2026-10-06, clear day 2026-10-07.

The fixtures are the frames reduced by judge.features (tests/data/judge_frames.npz).
Truth is m110's focused telescope edges; our pointing sits up to ~0.8 deg low of
them (terminus-85 and a different sync), so the day bounds are loose there."""

import math
import pathlib
import re

import numpy as np
import pytest

from terminus import judge
from terminus.judge import Verdict, column_edge

DATA = np.load(pathlib.Path(__file__).parent / "data" / "judge_frames.npz")


def column(name):
    rows = []
    for key in DATA.files:
        if key.startswith(name + "__"):
            alt = float(re.search(r"alt([\d.]+)", key).group(1))
            rows.append((alt, DATA[key].astype(np.float32)))
    return rows


def frame(name):
    ((_, f),) = column(name)
    return f


def sky(f):
    return judge._mean(f)


@pytest.mark.parametrize(
    "name, truth, tol",
    [
        ("night90", 10.31, 0.2),  # overcast: the old detector read 23.8 here
        ("night165", 14.46, 0.2),  # overcast: the old detector read 41.9
        ("day280", 13.10, 0.3),  # a roof edge, sunlit below
        ("day145", 36.29, 0.3),  # a diagonal edge
    ],
)
def test_the_edge_is_read_inside_the_frames(name, truth, tol):
    night = name.startswith("night")
    rows = column(name)
    top = max(rows, key=lambda r: r[0])[1]  # every one of these scans starts in open sky
    edge, _ = judge.judge_feature_column(rows, sky(top), night)
    assert edge.status == "edge" and abs(edge.alt - truth) <= tol, edge


def test_low_sky_is_judged_against_the_sky_just_above_it():
    """Sky brightens and whitens toward the horizon: against the 45 deg sky the
    lower frames read terrain; against the frame above each, they are sky."""
    rows = column("day270col")
    top = sky(dict(rows)[45.0])
    assert judge.judge_features(dict(rows)[20.0], top).kind == "terrain"  # the old failure
    _, verdicts = judge.judge_feature_column(rows, top)
    assert all(v.kind == "sky" for _, v in verdicts)


def test_whole_frames_by_day_and_night():
    west_sky = sky(frame("day300"))
    assert judge.judge_features(frame("day300"), west_sky).kind == "sky"
    assert judge.judge_features(frame("day20"), west_sky).sky < 0.5  # leaves
    night_sky = sky(frame("nightseries_sky"))
    assert (
        judge.judge_features(frame("nightseries_terrain"), night_sky, night=True).kind == "terrain"
    )


def test_a_straight_split_is_found_at_any_angle():
    h, w = 80, 45
    yy, xx = np.mgrid[0:h, 0:w]
    th = math.radians(30)
    side = (xx - w / 2) * math.sin(th) - (yy - h / 2) * math.cos(th) > 0
    f = np.zeros((h, w, 3), np.float32)
    f[..., 0] = np.where(side, 10.0, 9.0)  # log brightness: one side e times the other
    s = judge.best_split(f)
    assert abs(s.angle - 30) <= judge.ANGLE_STEP_DEG and s.contrast(night=True)
    assert abs(min(s.frac_a, 1 - s.frac_a) - side.mean().clip(max=1 - side.mean())) < 0.03


def test_the_last_upward_crossing_skips_a_pocket():
    """Sky under eaves, below the real horizon, is never taken for it."""
    v = [
        (30.0, Verdict("sky", 1.0)),
        (27.5, Verdict("terrain", 0.0)),
        (25.0, Verdict("sky", 1.0)),  # a pocket
        (22.5, Verdict("terrain", 0.0)),
    ]
    assert column_edge(v) == judge.Edge(28.75, "edge")


def test_an_edge_frame_places_the_edge_by_its_sky_fraction():
    v = [(20.0, Verdict("sky", 1.0)), (18.0, Verdict("edge", 0.75))]
    e = column_edge(v)
    assert e.status == "edge" and e.alt == pytest.approx(18.0 - 0.25 * judge.FRAME_DEG)


def test_blocked_above_and_open_below_are_never_the_same_answer():
    """The two opposite non-results: blocked from the top frame up (the horizon is
    at least there) and open down to the lowest frame (it is under it)."""
    blocked = [(40.0, Verdict("terrain", 0.0)), (38.0, Verdict("terrain", 0.0))]
    open_ = [(40.0, Verdict("sky", 1.0)), (38.0, Verdict("sky", 1.0))]
    assert column_edge(blocked) == judge.Edge(40.0, "above")
    assert column_edge(open_) == judge.Edge(38.0, "below")


def test_an_unknown_verdict_is_refused_not_read_as_terrain():
    with pytest.raises(ValueError, match="unknown verdict"):
        column_edge([(30.0, Verdict("sky", 1.0)), (20.0, Verdict("Edge", 0.4))])
