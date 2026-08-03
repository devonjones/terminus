"""Hardware-free tests for the pure logic: horizon interpolation, exporters,
classifier, and Sun geometry. Run with: uv run pytest  (or pytest)."""

import numpy as np
import pytest

from terminus import Horizon, ang_sep, classify, to_nina_hrz, to_stellarium_txt
from terminus.export import _ascending_pairs, load_mask, write_mask
from terminus.sweep import find_edge, obstruction_type, sky_reference, wrap180, wrap_ra


# ---- horizon interpolation -----------------------------------------------
def test_horizon_interpolates_and_wraps():
    h = Horizon([(0, 10), (90, 30), (180, 20)])
    assert h.altitude_at(0) == 10
    assert h.altitude_at(90) == 30
    assert h.altitude_at(45) == pytest.approx(20)  # halfway 10->30
    # past the last measured point it wraps back toward az 0's value
    assert h.altitude_at(180) == 20
    assert 10 <= h.altitude_at(300) <= 20 or h.altitude_at(300) == pytest.approx(10, abs=10)


def test_horizon_visibility_queries():
    h = Horizon([(0, 20), (180, 20)])
    assert h.is_above(0, 25) is True
    assert h.is_above(0, 15) is False
    assert h.clearance(0, 25) == pytest.approx(5)
    assert h.clearance(0, 15) == pytest.approx(-5)


def test_horizon_needs_two_points():
    with pytest.raises(ValueError):
        Horizon([(0, 10)])


# ---- exporters ------------------------------------------------------------
def test_ascending_pairs_adds_wrap_endpoints():
    pairs = _ascending_pairs([(10, 5, ""), (90, 8, "")])
    assert pairs[0][0] == 0 and pairs[-1][0] == 360
    assert pairs[-1][1] == pairs[0][1]  # 360 mirrors 0


def test_nina_hrz_format():
    txt = to_nina_hrz([(0, 12, "tree"), (90, 30.5, "structure")])
    body = [ln for ln in txt.splitlines() if not ln.startswith("#")]
    assert body[0] == "0 12"
    assert "90 30.5" in body
    assert body[-1].startswith("360 ")  # wrap endpoint present


def test_stellarium_txt_has_no_comments():
    txt = to_stellarium_txt([(0, 12, ""), (90, 30, "")])
    assert all(not ln.startswith("#") for ln in txt.splitlines())


def test_mask_roundtrip(tmp_path):
    p = tmp_path / "m.yaml"
    write_mask(
        str(p),
        {0: (12.3, "tree"), 90: (30.0, "structure")},
        [180],
        {"measured": "now", "lat": 40, "lon": -105},
    )
    meta, rows = load_mask(str(p))
    assert meta["lat"] == 40
    assert (0, 12.3, "tree") in rows and (90, 30.0, "structure") in rows


# ---- classifier & geometry ------------------------------------------------
def solid(rgb, shape=(4, 4)):
    a = np.zeros((*shape, 3), np.float32)
    a[:] = rgb
    return a


def test_classifier_splits_on_brightness_not_colour():
    bright_sky = solid((150, 170, 210))
    assert classify(bright_sky)[0] > 0.9  # open sky
    ref = sky_reference(bright_sky)
    # A silhouette against bright sky still reads blue (scattered light + blur);
    # brightness must call it an obstruction anyway. Colour alone got this wrong.
    assert classify(solid((30, 40, 70)), ref)[0] < 0.1


def test_classifier_calls_cloud_sky_not_obstruction():
    # A horizon mask records terrain, not weather: bright cloud is not an
    # obstruction even though it is grey rather than blue.
    ref = sky_reference(solid((150, 170, 210)))
    assert classify(solid((200, 200, 205)), ref)[0] > 0.9


def test_classifier_tags_obstruction_type():
    ref = sky_reference(solid((150, 170, 210)))
    assert classify(solid((40, 80, 20)), ref)[1] > 0.9  # green -> vegetation
    assert classify(solid((60, 60, 60)), ref)[2] > 0.9  # neutral -> structure


def test_find_edge_survives_a_twilight_gradient():
    # Sky brightening steadily toward the horizon (sunset glow), then terrain.
    # An absolute threshold fails here; the step is only visible as a level change.
    profile = [(40, 90), (35, 100), (30, 110), (25, 120), (20, 130), (15, 40), (10, 38), (5, 36)]
    idx, step, snr = find_edge(profile)
    assert profile[idx][0] == 20 and profile[idx + 1][0] == 15  # edge between 20 and 15
    assert snr > 2.5


def test_find_edge_reports_none_on_a_smooth_column():
    profile = [(40, 90), (35, 95), (30, 100), (25, 105), (20, 110)]
    idx, _, snr = find_edge(profile)
    assert idx is None and snr < 2.5


def test_find_edge_rejects_noise_masquerading_as_an_edge():
    # Real column measured 2026-08-02 at az 20 with auto-exposure enabled: the
    # camera renormalises each frame, so the profile is scatter with no true
    # step. A detector keyed on the largest single drop reported an edge at
    # alt 30 here (76 -> 116 -> 88 is a spike, not terrain); requiring the step
    # to beat the column's own noise rejects it.
    profile = [(35, 76), (30, 116), (25, 88), (20, 92), (15, 101), (10, 104), (5, 97), (0, 79)]
    idx, _, snr = find_edge(profile)
    assert idx is None and snr < 2.5


def test_obstruction_type():
    assert obstruction_type(0.6, 0.2) == "tree"
    assert obstruction_type(0.1, 0.7) == "structure"
    assert obstruction_type(0.02, 0.02) == "open"


def test_ang_sep_and_wrap():
    assert ang_sep(0, 0, 0, 0) == pytest.approx(0)
    assert ang_sep(0, 0, 90, 0) == pytest.approx(90)
    assert ang_sep(0, 45, 180, 45) == pytest.approx(90)  # over the zenith
    assert wrap180(350) == pytest.approx(-10)
    assert wrap180(-190) == pytest.approx(170)


def test_wrap_ra_takes_the_short_way():
    # RA is 24-hour cyclic; a slew from 23h to 1h is +2h, not -22h. The Sun
    # path-check interpolates on this, so a wrong sign would sample the wrong arc.
    assert wrap_ra(1 - 23) == pytest.approx(2)
    assert wrap_ra(23 - 1) == pytest.approx(-2)
    assert wrap_ra(0) == pytest.approx(0)
