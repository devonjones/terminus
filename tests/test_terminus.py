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


# ---- skymask / orient -----------------------------------------------------
def test_horizon_rows_needs_a_sustained_run():
    """A single dark row (a wire, a branch) must not read as the horizon."""
    import numpy as np

    from terminus.skymask import horizon_rows

    sky = np.ones((100, 3), bool)
    sky[40, :] = False  # one-row obstruction: too thin to be terrain
    sky[70:, :] = False  # the real terrain
    rows, clipped = horizon_rows(sky, run=6)
    assert list(rows) == [70, 70, 70]
    assert not clipped.any()


def test_horizon_rows_flags_clipped_columns():
    """Terrain reaching the top of frame is a crop, not a measurement."""
    import numpy as np

    from terminus.skymask import horizon_rows

    sky = np.ones((100, 2), bool)
    sky[:, 0] = False  # blocked all the way up
    sky[60:, 1] = False
    rows, clipped = horizon_rows(sky, run=6)
    assert clipped[0] and not clipped[1]


def test_upper_envelope_fills_narrow_gaps():
    """A gap narrower than mask resolution is not usable sky."""
    import numpy as np

    from terminus.skymask import upper_envelope

    rows = np.full(360, 50.0)
    rows[180] = 90.0  # one-column hole low in the canopy
    out = upper_envelope(rows, half_deg=2.0, px_per_deg=1.0)
    assert out[180] == 50.0


def test_rotate_alt_matches_small_angle_only_when_small():
    """The old sinusoid model is fine at small tilt and wrong at large."""
    import numpy as np

    from terminus.orient import rotate_alt

    az = np.arange(0, 360, 10.0)
    for tilt, tol in ((3.0, 0.2), (12.0, 1.0)):
        approx = -(tilt * np.cos(np.radians(az)))
        exact = rotate_alt(az, np.full_like(az, 60.0), 0.0, tilt, 0.0) - 60.0
        err = np.abs(approx - exact).max()
        assert (err < tol) if tilt == 3.0 else (err > tol)


def test_bound_residual_is_one_sided():
    """A photo above a bound agrees exactly; below it contradicts."""
    from terminus.orient import Fiducial, residuals

    fids = [Fiducial(0, 60.0, ceiling=60.0, bound=True)]
    above = residuals(fids, lambda az: 70.0, 0, 0, 0, 0)
    below = residuals(fids, lambda az: 50.0, 0, 0, 0, 0)
    assert above[0] == 0.0
    assert below[0] == -10.0


def test_headroom_flags_near_ceiling_edges():
    """An edge sitting at its ceiling is the false-edge signature."""
    from terminus.orient import Fiducial

    assert Fiducial(40, 32.5, ceiling=35.0).headroom() == 2.5
    assert Fiducial(80, 22.5, ceiling=35.0).headroom() == 12.5
    assert Fiducial(0, 35.0, ceiling=35.0, bound=True).headroom() == float("inf")


def test_from_mask_excludes_unknown():
    """'unknown' is a failed measurement, not a bound."""
    from terminus.orient import from_mask

    mask = {
        0: {"alt": 35.0, "type": "blocked>35"},
        40: {"alt": 32.5, "type": "edge"},
        160: {"alt": 35.0, "type": "unknown"},
    }
    fids = from_mask(mask, ceiling=35.0)
    assert sorted(f.az for f in fids) == [0.0, 40.0]
    assert [f.bound for f in sorted(fids, key=lambda f: f.az)] == [True, False]


def test_fit_recovers_a_known_rotation():
    """Round-trip: rotate a synthetic horizon, fit it back."""
    import numpy as np

    from terminus.orient import Fiducial, fit, rotate_alt

    true_yaw, true_pitch, true_tm, true_td = 137.0, 3.0, 6.0, 40.0
    shape = lambda a: 25.0 + 10.0 * np.cos(np.radians(2 * a))  # noqa: E731
    fids = []
    for az in range(0, 360, 20):
        alt = float(rotate_alt(az, shape((az - true_yaw) % 360), true_pitch, true_tm, true_td))
        fids.append(Fiducial(az, alt, ceiling=90.0))
    got = fit(fids, lambda a: float(shape(a)), yaw_step=1.0, tilt_step=2.0)
    assert abs(((got["yaw"] - true_yaw + 180) % 360) - 180) < 3.0
    assert got["rms"] < 1.5


def test_horizon_band_measures_canopy_porosity():
    """A gappy canopy reports a band and high porosity; a wall does not."""
    import numpy as np

    from terminus.skymask import horizon_band

    sky = np.ones((100, 2), bool)
    # column 0: solid wall from row 60
    sky[60:, 0] = False
    # column 1: foliage from row 40 with a clear gap at 50-56
    sky[40:, 1] = False
    sky[50:57, 1] = True
    b = horizon_band(sky, run=4)
    assert b["first"][0] == 60 and b["top"][0] == 60
    assert b["porosity"][0] == 0.0
    assert b["top"][1] == 40  # foliage really starts higher up
    assert b["porosity"][1] > 0.1  # and it is gappy in between


def test_type_uncertainty_widens_for_vegetation():
    """Trees get fuzz, rooflines do not."""
    import numpy as np

    from terminus.skymask import type_uncertainty

    classes = np.array([4, 1])  # tree, wall
    porosity = np.array([0.5, 0.0])
    u = type_uncertainty(classes, porosity)
    assert u[0] > u[1] * 2
    assert u[1] == 1.0


def test_tree_buffer_raises_only_vegetation():
    """Foliage gets a pessimistic margin; structures are exported as measured."""
    from terminus.export import apply_tree_buffer

    rows = [(0, 30.0, "tree"), (10, 30.0, "structure"), (20, 30.0, None)]
    out = apply_tree_buffer(rows, buffer_deg=3.0)
    assert out[0][1] == 33.0
    assert out[1][1] == 30.0
    assert out[2][1] == 30.0


def test_tree_buffer_cannot_exceed_zenith():
    from terminus.export import apply_tree_buffer

    assert apply_tree_buffer([(0, 89.0, "tree")], buffer_deg=5.0)[0][1] == 90.0


def test_tree_buffer_applies_to_every_export_format(tmp_path):
    """The buffer is a property of the horizon, not of one file format."""
    import yaml

    from terminus.export import export_all

    mask = tmp_path / "m.yaml"
    mask.write_text(
        yaml.safe_dump(
            {
                "meta": {"measured": "now", "lat": 39.8, "lon": -104.9},
                "horizon": {
                    0: {"alt": 30.0, "type": "tree"},
                    180: {"alt": 30.0, "type": "structure"},
                },
            }
        )
    )
    hrz, txt = export_all(str(mask), str(tmp_path / "out"), tree_buffer=3.0)
    for path in (hrz, txt):
        vals = {}
        for line in open(path):
            if line.startswith("#") or not line.strip():
                continue
            az, alt = line.split()
            vals[float(az)] = float(alt)
        assert vals[0.0] == 33.0, f"{path}: tree column not buffered"
        assert vals[180.0] == 30.0, f"{path}: structure wrongly buffered"


def test_find_edge_prefers_the_topmost_competitive_step():
    """A lit roof makes two change points; the horizon is the higher one.

    Real column (az 200, 2026-08-03): sky ~168, a pale roof at ~100, dark ground
    below. Maximising the step chose alt 12.5 over alt 35 by a 1% margin — the
    roof meeting the ground rather than the sky meeting the roof.
    """
    from terminus.sweep import find_edge

    profile = [
        (60, 167.2),
        (55, 163.4),
        (50, 166.4),
        (45, 166.4),
        (40, 169.1),
        (35, 171.3),
        (30, 101.7),
        (25, 92.8),
        (20, 74.6),
        (15, 88.0),
        (13.8, 87.0),
        (12.5, 56.8),
        (10, 28.4),
        (5, 23.6),
    ]
    idx, step, snr = find_edge(profile)
    assert idx is not None
    assert profile[idx][0] == 35, f"picked alt {profile[idx][0]}, expected the roofline at 35"
    assert snr >= 2.5


def test_find_edge_still_finds_a_single_clean_step():
    """The topmost-competitive rule must not fire early on a normal column."""
    from terminus.sweep import find_edge

    profile = [(60, 200.0), (50, 198.0), (40, 202.0), (30, 199.0), (20, 40.0), (10, 38.0)]
    idx, step, snr = find_edge(profile)
    assert profile[idx][0] == 30


def test_ambiguous_column_is_self_diagnosing():
    """A three-level column flags itself without any external reference."""
    from terminus.sweep import edge_is_ambiguous

    roof = [
        (60, 167.2),
        (55, 163.4),
        (50, 166.4),
        (45, 166.4),
        (40, 169.1),
        (35, 171.3),
        (30, 101.7),
        (25, 92.8),
        (20, 74.6),
        (15, 88.0),
        (13.8, 87.0),
        (12.5, 56.8),
        (10, 28.4),
        (5, 23.6),
    ]
    flagged, detail = edge_is_ambiguous(roof)
    assert flagged
    hi, lo, ratio = detail
    assert abs(hi - lo) > 15  # the two candidates are far apart in altitude
    assert ratio > 0.95  # yet score within a few percent

    clean = [(60, 200.0), (50, 198.0), (40, 202.0), (30, 199.0), (20, 40.0), (10, 38.0)]
    assert not edge_is_ambiguous(clean)[0]


def test_plan_targeted_brackets_the_prior():
    from terminus.sweep import plan_targeted

    plan = plan_targeted(30.0, sigma=3.0, repeats=3)
    alts = [a for a, _ in plan]
    assert max(alts) > 30.0 > min(alts)
    assert all(r == 3 for _, r in plan)
    assert alts == sorted(alts, reverse=True)


def test_fit_transition_recovers_edge_and_width():
    """A sharp wall gives a narrow transition; a gappy canopy a wide one."""
    from terminus.sweep import fit_transition

    ref = 200.0
    sharp = [
        (34, [180] * 4),
        (32, [180] * 4),
        (30, [180, 180, 180, 30]),
        (28, [30] * 4),
        (26, [30] * 4),
    ]
    alt, sigma, _ = fit_transition(sharp, ref)
    assert 28.0 <= alt <= 32.0
    assert sigma is not None and sigma <= 3.0

    windy = [
        (38, [180] * 4),
        (34, [180, 180, 180, 30]),
        (30, [180, 30, 30, 180]),
        (26, [30, 30, 180, 30]),
        (22, [30] * 4),
    ]
    alt2, sigma2, _ = fit_transition(windy, ref)
    assert sigma2 > sigma  # a moving boundary is genuinely less certain


def test_fit_transition_reports_when_range_missed_the_edge():
    from terminus.sweep import fit_transition

    _, _, why = fit_transition([(40, [30] * 3), (35, [30] * 3)], 200.0)
    assert "no sky" in why["reason"]


def test_boundary_models_differ_for_tree_and_structure():
    from terminus.sweep import boundary_model

    tree, wall = boundary_model("tree"), boundary_model("structure")
    assert tree["repeats"] > wall["repeats"]  # wind needs averaging
    assert tree["max_width_deg"] > wall["max_width_deg"]  # canopy is a band
    assert tree["buffer_deg"] > wall["buffer_deg"]
    assert tree["seasonal"] and not wall["seasonal"]
    assert boundary_model(None) == wall  # unknown falls back to the strict model


def test_judge_width_flags_a_soft_wall_and_a_sharp_tree():
    from terminus.sweep import judge_width

    assert judge_width("structure", 1.0)[0] == "ok"
    assert judge_width("structure", 9.0)[0] == "suspect"  # walls are not soft
    assert judge_width("tree", 6.0)[0] == "ok"  # canopy width is expected
    assert judge_width("tree", 0.2)[0] == "suspect"  # foliage is not a knife edge


def test_sun_guard_stands_down_once_the_sun_has_set():
    """Below the horizon the Sun is occulted; refusing to observe is wrong."""
    from unittest.mock import patch

    from terminus.sweep import Pointer, Sky, column_touches_sun

    sky = Sky(39.7917, -104.894, 1600)
    with patch.object(Sky, "sun", lambda self: (297.0, -4.7)):
        assert not column_touches_sun(sky, 300, 0, 60, 30)
        Pointer(None, sky, 30, 5, dry=True)._sun_check(297.0, 1.0)  # must not raise
    with patch.object(Sky, "sun", lambda self: (297.0, 5.0)):
        assert column_touches_sun(sky, 300, 0, 60, 30)  # still up: still guarded


def test_call_reconnects_once_on_a_dropped_socket():
    """A multi-hour run must survive the scope closing the connection."""
    from unittest.mock import MagicMock, patch

    from terminus.client import Seestar

    sc = Seestar.__new__(Seestar)
    sc.host = "10.0.0.1"
    sc.cmdid = 1
    sc.buf = ""
    sc.s = MagicMock()
    sc.s.sendall.side_effect = BrokenPipeError()
    with (
        patch.object(Seestar, "reconnect") as recon,
        patch.object(Seestar, "_readline", return_value=None),
    ):

        def fixed(*a, **k):
            sc.s = MagicMock()  # a healthy socket after reconnecting
            return True

        recon.side_effect = fixed
        sc.call("scope_get_equ_coord")
        assert recon.called, "a dropped socket must trigger exactly one reconnect"


def test_planner_prefers_steep_horizon_for_orientation():
    """Yaw information comes from gradient; a flat column carries almost none."""

    from terminus.plan import next_column

    # steep at az 100, flat everywhere else
    def grad(a):
        return 5.0 if 95 <= a % 360 <= 105 else 0.02

    pick, _ = next_column([0.0, 180.0], [40.0, 100.0, 220.0], grad)
    assert pick == 100.0


def test_planner_spreads_when_gradient_is_uniform():
    """With no gradient to chase, tilt and pitch want coverage."""
    from terminus.plan import next_column

    pick, _ = next_column([0.0, 10.0, 20.0], [30.0, 180.0], lambda a: 1.0)
    assert pick == 180.0  # opposite side beats another neighbour


def test_stability_not_residual_is_the_stopping_rule():
    from terminus.plan import is_stable

    settling = [{"yaw": 120.0}, {"yaw": 128.0}, {"yaw": 129.5}, {"yaw": 130.0}]
    assert not is_stable(settling, window=3, yaw_tol=1.0)[0]
    settled = settling + [{"yaw": 130.2}, {"yaw": 129.9}]
    ok, spread = is_stable(settled, window=3, yaw_tol=1.0)
    assert ok and spread <= 1.0


def test_stability_handles_the_wrap_at_zero():
    from terminus.plan import is_stable

    assert is_stable(
        [{"yaw": 359.5}, {"yaw": 0.2}, {"yaw": 359.8}, {"yaw": 0.1}], window=3, yaw_tol=1.0
    )[0]


def test_seed_columns_are_spread_and_snapped():
    from terminus.plan import seed_columns

    picks = seed_columns(4, [0, 10, 80, 90, 100, 170, 180, 260, 270])
    assert len(picks) == 4
    assert picks[0] == 0 and 90 in picks and 180 in picks


def test_residual_targets_finds_the_disagreements():
    from terminus.plan import residual_targets

    res = {0.0: 0.5, 50.0: -11.0, 200.0: 13.9, 90.0: 1.2}
    assert residual_targets(res, top=2) == [200.0, 50.0]


def test_capture_rejects_a_repeated_frame():
    """The stream has frozen mid-run, serving one stale frame however the mount
    moved, which fabricated ten blocked azimuths. Repetition is the detectable
    signature; brightness is not."""
    from unittest.mock import patch

    import numpy as np

    from terminus.client import Seestar

    sc = Seestar.__new__(Seestar)
    sc.host = "10.0.0.1"
    stale = np.full((4, 4, 3), 0.8, dtype=np.float32)  # what the frozen stream served
    fresh = np.full((4, 4, 3), 15.0, dtype=np.float32)
    # the pointing before this one already returned `stale`
    sc._last_frame_sig = (float(stale.mean()), float(stale.std()), float(stale[::37, ::37].sum()))
    queue = [stale, fresh]
    calls = {"n": 0}

    def fake(*a, **k):
        calls["n"] += 1
        return None

    with (
        patch("subprocess.run", side_effect=fake),
        patch("PIL.Image.open") as img,
        patch("os.path.exists", return_value=False),
        patch("time.sleep"),
    ):
        img.side_effect = lambda p: type(
            "I", (), {"convert": lambda self, m: queue[min(calls["n"] - 1, len(queue) - 1)]}
        )()
        with patch("numpy.asarray", side_effect=lambda x, dtype=None: x):
            out = sc.capture_rgb(warmup=0.1)
    assert calls["n"] >= 2, "an identical frame must be re-taken"
    assert float(out.mean()) > 10, "the retry must return the fresh frame"


def test_capture_returns_a_genuinely_dark_frame():
    """Unlit terrain reads ~0.09 counts and is the answer, not a failure.

    An earlier version retried anything dark, which taxed every terrain sample
    and still could not catch the frozen stream — those frames read 0.8, i.e.
    brighter than real terrain.
    """
    from unittest.mock import patch

    import numpy as np

    from terminus.client import Seestar

    sc = Seestar.__new__(Seestar)
    sc.host = "10.0.0.1"
    sc._last_frame_sig = None
    dark = np.full((4, 4, 3), 0.09, dtype=np.float32)
    calls = {"n": 0}

    def fake(*a, **k):
        calls["n"] += 1
        return None

    with (
        patch("subprocess.run", side_effect=fake),
        patch("PIL.Image.open") as img,
        patch("os.path.exists", return_value=False),
        patch("time.sleep"),
    ):
        img.side_effect = lambda p: type("I", (), {"convert": lambda self, m: dark})()
        with patch("numpy.asarray", side_effect=lambda x, dtype=None: x):
            out = sc.capture_rgb(warmup=0.1)
    assert calls["n"] == 1, "a dark frame is a measurement, not a retry"
    assert float(out.mean()) < 1.0


def test_reconnect_does_not_recurse_forever():
    """reconnect() calls authenticate(), which issues calls of its own; a socket
    that keeps failing must raise rather than loop."""
    from unittest.mock import patch

    import pytest

    from terminus.client import Seestar, SeestarError

    sc = Seestar.__new__(Seestar)
    sc.host = "10.0.0.1"
    sc._reconnecting = True  # already inside a reconnection
    with patch.object(Seestar, "_open"):
        with pytest.raises(SeestarError, match="during reconnection"):
            sc.reconnect()


def test_blocked_column_becomes_a_bound_not_a_hole():
    """A column with no edge is obstructed above the ceiling, not unmeasured."""
    from terminus.orient import Fiducial
    from terminus.plan import as_fiducial

    f = as_fiducial(0.0, None, 60.0, Fiducial)
    assert f.bound and f.alt == 60.0

    g = as_fiducial(90.0, {"alt": 22.5, "snr": 8.0}, 60.0, Fiducial)
    assert not g.bound and g.alt == 22.5 and g.weight == 1.0

    h = as_fiducial(10.0, {"alt": 60.0, "snr": 3.0}, 60.0, Fiducial)
    assert h.bound, "a result at the ceiling is a bound, whatever the detector called it"


def test_night_finds_horizon_under_a_skyglow_gradient():
    """Real column az 300: skyglow rises 22 -> 71 then drops at the horizon."""
    from terminus.night import find_horizon

    prof = [
        (60, 22.0),
        (50, 27.7),
        (45, 31.7),
        (40, 35.9),
        (35, 41.7),
        (30, 47.4),
        (25, 54.0),
        (20, 60.7),
        (17.5, 63.7),
        (15, 68.0),
        (12.5, 71.3),
        (10, 26.7),
        (7.5, 7.8),
        (5, 34.8),
        (2.5, 34.0),
        (0, 38.3),
    ]
    alt, detail = find_horizon(prof, sky_ref=20.6)
    assert alt is not None, detail
    assert 7.5 <= alt <= 12.5, f"horizon at {alt}, expected near 10"


def test_night_ignores_a_streetlight_inside_terrain():
    """Real column az 340: blocked throughout, with a lamp at 12.5-15."""
    from terminus.night import find_horizon

    prof = [
        (60, 5.8),
        (50, 6.9),
        (45, 9.6),
        (40, 7.0),
        (35, 6.7),
        (30, 7.7),
        (25, 6.2),
        (20, 7.8),
        (17.5, 10.7),
        (15, 53.9),
        (12.5, 56.4),
        (10, 32.0),
        (7.5, 9.7),
        (5, 6.8),
        (2.5, 6.3),
        (0, 6.3),
    ]
    alt, detail = find_horizon(prof, sky_ref=20.7)
    assert alt is None
    assert "blocked" in detail["reason"], detail


def test_night_detector_agrees_with_the_evening_sweep():
    """Columns measured both in twilight and at night must agree.

    These are real profiles from 2026-08-04 after polar realignment, judged by
    the night detector, against what the twilight sweep recorded hours earlier.
    Agreement across two very different lighting regimes is the strongest
    available check that the night path is measuring the horizon and not the
    skyglow or a streetlight.
    """
    from terminus.night import find_horizon

    cases = [
        # (profile, sky_ref, evening value, tolerance)
        (
            [
                (60, 22.0),
                (50, 27.7),
                (45, 31.7),
                (40, 35.9),
                (35, 41.7),
                (30, 47.4),
                (25, 54.0),
                (20, 60.7),
                (17.5, 63.7),
                (15, 68.0),
                (12.5, 71.3),
                (10, 26.7),
                (7.5, 7.8),
                (5, 34.8),
                (2.5, 34.0),
                (0, 38.3),
            ],
            20.6,
            11.2,
            3.0,
        ),
    ]
    for prof, ref, evening, tol in cases:
        alt, detail = find_horizon(prof, sky_ref=ref)
        assert alt is not None, detail
        assert abs(alt - evening) <= tol, f"night {alt} vs evening {evening}"


def test_floor_pinned_result_is_not_a_measurement():
    """A horizon reported at the search floor never departed the sky model."""
    from terminus.night import find_horizon

    # uniformly sky-like all the way down: nothing was found
    prof = [(a, 50.0 - 0.2 * (60 - a)) for a in (60, 50, 40, 30, 20, 10, 5, 0)]
    alt, detail = find_horizon(prof, sky_ref=50.0)
    assert alt is None
    assert "open" in detail["reason"]
