"""Hardware-free tests for the pure logic: horizon interpolation, exporters,
classifier, and Sun geometry. Run with: uv run pytest  (or pytest)."""

import os

import numpy as np
import pytest

from terminus import Horizon, ang_sep, classify, to_nina_hrz, to_stellarium_txt
from terminus.export import _ascending_pairs, export_all, load_mask, write_mask
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
    txt = to_nina_hrz([(0, 12, "tree"), (90, 30.5, "structure")], tree_buffer=0)
    body = [ln for ln in txt.splitlines() if not ln.startswith("#")]
    assert body[0] == "0 12"
    assert "90 30.5" in body
    assert body[-1].startswith("360 ")  # wrap endpoint present


def test_exporters_buffer_vegetation_when_called_directly():
    """The margin must not depend on going through export_all.

    Both exporters are public API and documented for direct import. Applying the
    buffer only in the export_all wrapper meant `load_mask()` -> `to_nina_hrz()`
    silently produced a horizon with no margin around foliage — the one place
    the mask is deliberately not to be trusted.
    """
    rows = [(0, 12, "tree"), (90, 30.5, "structure")]
    hrz = [ln for ln in to_nina_hrz(rows).splitlines() if not ln.startswith("#")]
    txt = to_stellarium_txt(rows).splitlines()
    assert hrz[0] == "0 15" and txt[0] == "0 15", "vegetation must be raised 3 deg"
    assert "90 30.5" in hrz and "90 30.5" in txt, "a roofline needs no margin"


def test_export_all_applies_the_buffer_exactly_once(tmp_path):
    """Buffering in both the wrapper and the exporters would double the margin."""
    p = tmp_path / "m.yaml"
    write_mask(str(p), {0: (12.0, "tree")}, [], {"lat": 40, "lon": -105})
    hrz, _ = export_all(str(p), str(tmp_path / "out"))
    body = [ln for ln in open(hrz).read().splitlines() if not ln.startswith("#")]
    assert body[0] == "0 15", f"expected one 3 deg buffer, got {body[0]}"


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
    """Round-trip: place a synthetic horizon on the sky, fit it back.

    Two things here were wrong and both flattered the result. The fiducials were
    generated as `rotate_alt(az, shape(az - yaw), ...)`, which is the same
    az-mixing shortcut the fit itself used, so the test scored the code against
    its own bug and could not have caught it. And `cos(2a)` is symmetric under
    half a turn, leaving yaw ambiguous by 180 degrees.
    """
    from terminus.orient import fit

    true_yaw, true_pitch, true_tm, true_td = 137.0, 3.0, 3.0, 40.0
    fids = _place(_skyline, true_yaw, true_pitch, true_tm, true_td, step=20.0)
    got = fit(fids, _skyline, yaw_step=2.5, tilt_step=1.5)
    assert abs(((got["yaw"] - true_yaw + 180) % 360) - 180) < 3.0, got
    assert abs(got["tilt_mag"] - true_tm) < 1.5, got
    assert abs(got["pitch"] - true_pitch) < 1.0, got
    assert got["rms"] < 1.5, got


def test_horizon_band_measures_canopy_gap_fraction():
    """A gappy canopy reports a band and high gap_fraction; a wall does not."""
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
    assert b["gap_fraction"][0] == 0.0
    assert b["top"][1] == 40  # foliage really starts higher up
    assert b["gap_fraction"][1] > 0.1  # and it is gappy in between


def test_type_uncertainty_widens_for_vegetation():
    """Trees get fuzz, rooflines do not."""
    import numpy as np

    from terminus.skymask import type_uncertainty

    classes = np.array([4, 1])  # tree, wall
    gap_fraction = np.array([0.5, 0.0])
    u = type_uncertainty(classes, gap_fraction)
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
    # This line used to read `assert boundary_model(None) == wall`, calling the
    # structure model "the strict model". It is not strict, it is a DIFFERENT
    # rule: it demands a sharper step and calls a wide transition suspect, which
    # is the wrong judgement for foliage. An unnamed column is most often a tree
    # measured at night, so the old fallback pointed the wall rule at exactly
    # the columns the vegetation rule exists for. Refusing is the honest answer;
    # see test_an_unnamed_column_gets_no_boundary_model.
    import pytest

    with pytest.raises(ValueError):
        boundary_model(None)


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


# ---- review round 1 regressions -------------------------------------------
def test_second_leg_rechecks_the_sun_after_the_waypoint():
    """A two-hop route must not fire its second leg on a stale clearance.

    GOTO_TIMEOUT is 90s and extends while the mount still reports motion, so the
    first leg can run for minutes. The clearance was computed before it started;
    by the time the second leg fires the Sun has moved. It must be recomputed
    against where the mount actually landed.
    """
    from unittest.mock import MagicMock, patch

    from terminus.sweep import Pointer, Sky, SunGuard

    sky = Sky(39.7917, -104.894, 1600)
    sc = MagicMock()
    sc.equ_coord.return_value = (12.0, 20.0)
    # A real frame: scan_horizon short-circuits entirely in dry mode and never
    # points, so the pointing path can only be exercised with dry=False.
    sc.capture_rgb.return_value = np.full((8, 8, 3), 120.0, dtype=np.float32)
    ptr = Pointer(sc, sky, 30, 5)

    legs = []

    def fake_goto(ra, dec, settle):
        legs.append((ra, dec))

    # Direct path blocked -> route via a waypoint; both legs clear when planned;
    # then the second leg is no longer clear once the waypoint is reached.
    seps = iter([1.0, 90.0, 90.0, 1.0])
    with (
        patch.object(Pointer, "_goto_wait", side_effect=fake_goto),
        patch.object(Pointer, "_sun_check", lambda self, az, alt: None),
        patch.object(Pointer, "current_azalt", lambda self: (100.0, 40.0)),
        patch.object(Pointer, "path_min_sep", lambda self, a, b: next(seps, 1.0)),
    ):
        with pytest.raises(SunGuard, match="Sun has moved"):
            ptr.point_to(200.0, 20.0)
    assert len(legs) == 1, "the second leg must not fire once the path is no longer clear"


def test_avoid_pole_nudges_azimuth_not_altitude():
    """Due north at altitude == latitude IS the celestial pole.

    A sweep stalled there twice: RA is undefined, so the mount makes enormous RA
    swings to reach neighbours. Altitude is the measured quantity and must not
    move; a degree or two of azimuth is inside the mask's own resolution.
    """
    from unittest.mock import MagicMock

    from terminus.sweep import Pointer, Sky

    sky = Sky(39.7917, -104.894, 1600)
    ptr = Pointer(MagicMock(), sky, 30, 5)

    az, alt = ptr.avoid_pole(0.0, 39.7917)  # straight at the pole
    assert alt == 39.7917, "altitude is the measurement and must not be nudged"
    assert az != 0.0, "azimuth must move off the pole"
    _, dec = sky.altaz_to_radec(az, alt)
    assert abs(dec) <= 88.5, f"nudged target still at dec {dec:.2f}"

    # A target nowhere near the pole is returned untouched.
    assert ptr.avoid_pole(180.0, 30.0) == (180.0, 30.0)


def test_night_skips_a_lamp_and_finds_the_horizon_below_it():
    """The lamp-skip branch, exercised on a column that IS open at the top.

    The existing streetlight test exits early at the sky-floor gate, so it never
    reaches this code. Here the sky is genuinely open down to 25, then a lamp
    outshines it at 20, then real terrain.
    """
    from terminus.night import find_horizon

    prof = [
        (60, 20.0),
        (50, 22.0),
        (45, 23.0),
        (40, 24.0),
        (35, 25.0),
        (30, 26.0),
        (25, 27.0),
        (20, 90.0),  # streetlight, far brighter than the sky model
        (15, 3.0),  # terrain
        (10, 2.8),
        (5, 2.6),
        (0, 2.5),
    ]
    alt, detail = find_horizon(prof, sky_ref=22.0)
    assert alt == 15.0, f"expected the horizon below the lamp, got {alt} ({detail})"
    assert 20.0 in detail["lights_at"], "the lamp must be identified, not treated as terrain"


def test_heuristic_sky_rejects_warm_and_green_surfaces():
    """The CI-reachable classifier path: no torch, so this is what runs by default."""
    import numpy as np

    from terminus.skymask import heuristic_sky

    img = np.zeros((10, 3, 3), np.float32)
    img[:, 0] = (120, 150, 200)  # blue sky
    img[:, 1] = (180, 120, 90)  # warm brick
    img[:, 2] = (60, 110, 70)  # green foliage
    sky = heuristic_sky(img, px_per_deg=1.0, half_deg=1.0)
    assert sky[:, 0].all(), "blue sky must classify as sky"
    assert not sky[:, 1].any(), "warm masonry must not"
    assert not sky[:, 2].any(), "foliage must not"


def test_obstruction_classes_votes_below_the_horizon():
    """The class is taken from the band just BELOW the skyline, not above it."""
    import numpy as np

    from terminus.skymask import obstruction_classes

    seg = np.zeros((20, 2), int)
    seg[:10, :] = 2  # sky above
    seg[10:, 0] = 4  # tree below in column 0
    seg[10:, 1] = 1  # building below in column 1
    out = obstruction_classes(seg, np.array([9.0, 9.0]), window=6)
    assert list(out) == [4, 1]

    # A column with no measured horizon yields no class rather than a wrong one.
    out = obstruction_classes(seg, np.array([np.nan, 9.0]), window=6)
    assert out[0] == -1 and out[1] == 1


def test_control_point_counts_maps_frames_to_their_constraints(tmp_path):
    """The count drives frame dropping: a frame with too few is unconstrained.

    autooptimiser will place a frame with ZERO control points, and did — a garage
    umbrella landed in the sky. Miscounting here silently reinstates that bug.
    """
    from terminus.mosaic import control_point_counts

    pto = tmp_path / "p.pto"
    pto.write_text(
        'i w100 h100 f0 n"/photos/a.jpg"\n'
        'i w100 h100 f0 n"/photos/b.jpg"\n'
        'i w100 h100 f0 n"/photos/c.jpg"\n'
        "c n0 N1 x1 y1 X2 Y2 t0\n"
        "c n0 N1 x3 y3 X4 Y4 t0\n"
        "c n1 N2 x5 y5 X6 Y6 t0\n"
    )
    counts = control_point_counts(str(pto))
    assert counts == {"a.jpg": 2, "b.jpg": 3, "c.jpg": 1}


# ---- review round 2 regressions -------------------------------------------
def _skyline(phi):
    """A synthetic horizon with STRUCTURE but no discontinuity.

    A single sinusoid is exactly degenerate with a tilt — a tilt about a
    horizontal axis adds precisely one sinusoid in azimuth — so a sinusoidal
    horizon cannot identify yaw at all and is useless as a fixture. So is
    cos(2a): its 180 degree symmetry leaves yaw ambiguous by half a turn.
    """
    import math

    p = phi % 360.0
    ridge = 11.0 * (1 + math.tanh((p - 100) / 3.0)) * (1 + math.tanh((130 - p) / 3.0)) / 4
    return (
        18.0
        + 6.0 * math.sin(math.radians(2 * p))
        + 3.0 * math.sin(math.radians(3 * p + 40))
        + ridge
    )


def _cliff(phi):
    """The same skyline with a genuine vertical step — a house corner."""
    import math

    p = phi % 360.0
    h = 18.0 + 6.0 * math.sin(math.radians(2 * p)) + 3.0 * math.sin(math.radians(3 * p + 40))
    return h + (22.0 if 100 <= p < 130 else 0.0)


def _place(shape, yaw, pitch, tmag, tdir, step=15.0):
    """Fiducials from the honest forward model.

    Each photo column is rotated into the world and recorded where it ACTUALLY
    lands — azimuth as well as altitude. Generating them as
    `rotate_alt(az, shape(az - yaw), ...)` instead bakes in the very
    approximation under test, and a fit is then scored against its own bug.
    """
    import numpy as np

    from terminus.orient import Fiducial, rotate

    world = sorted(
        tuple(float(v) for v in rotate(phi + yaw, shape(phi), pitch, tmag, tdir))
        for phi in np.arange(0, 360, 0.05)
    )
    wa = np.array([a for a, _ in world])
    we = np.array([e for _, e in world])
    return [Fiducial(az=a, alt=float(np.interp(a, wa, we))) for a in np.arange(0, 360, step)]


def _rms_at_truth(shape, tilt):
    import numpy as np

    from terminus.orient import residuals

    r = residuals(_place(shape, 40.0, 2.0, tilt, 60.0), shape, 40.0, 2.0, tilt, 60.0)
    r = r[np.isfinite(r)]
    return float(np.sqrt((r**2).mean()))


def test_residuals_vanish_at_the_true_orientation():
    """The model must be exact, not exact-to-first-order.

    A tilt moves a point in azimuth as well as altitude, so the photo column
    that ENDS at a fiducial's azimuth is not the one that STARTED at
    `az - yaw`. Sampling the starting column and scoring its altitude against
    the target azimuth reintroduced the small-angle approximation the exact
    rotation exists to remove: measured on this horizon before the fix, 0.56
    deg RMS at the TRUE parameters, growing with tilt.
    """
    for tilt in (1.0, 3.0, 6.0, 12.0):
        rms = _rms_at_truth(_skyline, tilt)
        assert rms < 0.05, f"tilt {tilt}: residual {rms:.3f} deg at the true parameters"


def test_a_vertical_cliff_stays_exact_through_the_working_range():
    """Where the horizon jumps, the model is ill-posed — but not until 9 deg.

    Rotating a discontinuous curve leaves world azimuths that no photo column
    maps to, so the solve for the native column cannot converge at a house
    corner. Against a 22 deg step it remains exact through 6 deg of tilt, which
    covers this site (3.0 deg), and past that only the two columns adjacent to
    the step degrade. Documented rather than papered over.
    """
    for tilt in (1.0, 3.0, 6.0):
        rms = _rms_at_truth(_cliff, tilt)
        assert rms < 0.05, f"cliff at tilt {tilt}: {rms:.3f} deg"


def test_jacobian_yaw_term_keeps_the_sign_of_the_slope():
    """A falling edge is not a rising one.

    horizon_gradient returned |dH/daz|, making the Jacobian's yaw entry negative
    everywhere. Because the row enters the information matrix as an outer
    product, flipping only that entry skews the yaw/pitch and yaw/tilt cross
    terms — which is exactly what next_column ranks on.
    """
    import numpy as np

    from terminus.plan import horizon_gradient, jacobian_row

    # 20 + 10 sin(az): rises through az 45, falls through az 135, crest at 90
    prof = [(a, 20.0 + 10.0 * np.sin(np.radians(a))) for a in np.arange(0, 360, 1.0)]
    at = horizon_gradient(prof)
    assert at(90.0) == pytest.approx(0.0, abs=0.02)  # crest: flat
    rising, falling = at(45.0), at(135.0)
    assert rising > 0 > falling, f"rising {rising}, falling {falling}"
    assert jacobian_row(45.0, rising)[0] < 0 < jacobian_row(135.0, falling)[0]


def test_native_column_falls_back_to_its_best_iterate():
    """At a severe discontinuity the fixed point oscillates; keep the best try.

    Rotating a discontinuous curve leaves world azimuths no photo column maps
    to, so the solve cannot converge at a house corner. Returning whichever side
    the loop happened to stop on is arbitrary: measured against a 60 deg step at
    15 deg of tilt, the last iterate lands 35.9 deg from the requested azimuth
    while the best one lands 3.5 deg away. Keeping the best bounds the error by
    roughly the width of the cliff instead of letting it land anywhere.
    """
    import math

    from terminus.orient import _rotate_scalar, _wrap180, native_column

    def steep_cliff(phi):
        p = phi % 360.0
        h = 18.0 + 6.0 * math.sin(math.radians(2 * p)) + 3.0 * math.sin(math.radians(3 * p + 40))
        return h + (60.0 if 100 <= p < 130 else 0.0)

    yaw, tilt_mag, tilt_dir, target = 40.0, 15.0, 60.0, 139.0
    phi, raw = native_column(steep_cliff, target, yaw, tilt_mag, tilt_dir)
    landed, _ = _rotate_scalar(phi + yaw, raw, tilt_mag, tilt_dir)
    err = abs(_wrap180(target - landed))
    assert err < 10.0, f"fell back to a poor iterate: landed {err:.1f} deg from the target"


def test_local_refinement_beats_the_coarse_grid():
    """The refinement passes must actually earn their place.

    On a grid fine enough to hit the tolerance by itself, disabling refinement
    changes nothing and the loop is untested. Here the coarse grid can only
    place yaw within half a step (2.5 deg), so meeting 1.5 deg requires the
    refinement to run.
    """
    from terminus.orient import fit

    true_yaw = 137.0
    fids = _place(_skyline, true_yaw, 3.0, 3.0, 40.0, step=20.0)
    got = fit(fids, _skyline, yaw_step=5.0, tilt_step=3.0)
    off = abs(((got["yaw"] - true_yaw + 180) % 360) - 180)
    assert off < 1.5, f"yaw off by {off:.2f} deg — coarse grid alone cannot do better than 2.5"


def _sweep_with_failures(fail_azimuths):
    """Run run_sweep against a mount that refuses to arrive at given azimuths."""
    from unittest.mock import MagicMock, patch

    import numpy as np

    from terminus.sweep import Pointer, PointingError, Sky, run_sweep

    sky = Sky(39.7917, -104.894, 1600)
    sc = MagicMock()
    sc.equ_coord.return_value = (12.0, 20.0)
    # A real frame: scan_horizon short-circuits entirely in dry mode and never
    # points, so the pointing path can only be exercised with dry=False.
    sc.capture_rgb.return_value = np.full((8, 8, 3), 120.0, dtype=np.float32)
    cfg = {
        "sun_cone_deg": 30,
        "slew_step_deg": 5,
        "az_step": 30,
        "alt_min": 0,
        "alt_max": 60,
        "alt_tol": 2.5,
        "clear_thresh": 0.6,
    }
    seen = []

    def point_to(self, az, alt):
        seen.append(az)
        if round(az) % 360 in fail_azimuths:
            raise PointingError(f"never arrived at ({az:.0f},{alt:.0f})")
        return az, alt

    with (
        patch.object(Sky, "sun", lambda self: (297.0, -20.0)),  # night: guard stands down
        patch.object(Pointer, "point_to", point_to),
        patch("terminus.sweep.column_touches_sun", lambda *a, **k: False),
    ):
        return run_sweep(sc, sky, cfg, az_start=0, az_end=350, dry=False, log=lambda *a, **k: None)


def test_one_bad_column_is_skipped_not_fatal():
    """PointingError is new in this PR and the scan loop caught only SunGuard.

    A single column that will not arrive is not worth losing a night's sweep
    over — before this, one non-arrival aborted the whole run.
    """
    _, skipped, _ = _sweep_with_failures({90})
    assert 90 in skipped
    assert len(skipped) < 12, "only the bad column should be skipped"


def test_a_mount_that_cannot_point_stops_the_sweep():
    """The opposite failure: skipping quietly would hide a dead mount.

    A goto once reported success while the arm was closed and nothing moved,
    which is why PointingError exists. Swallowing it at every column would
    produce an empty mask and call it a measurement.
    """
    import pytest

    from terminus.sweep import MAX_POINTING_MISSES, PointingError

    with pytest.raises(PointingError, match="consecutive pointing failures"):
        _sweep_with_failures(set(range(0, 360, 30)))
    assert MAX_POINTING_MISSES >= 2, "one transient miss must not abort a sweep"


def test_scattered_pointing_failures_do_not_accumulate():
    """The counter must reset, or a long sweep dies of unrelated hiccups."""
    from terminus.sweep import MAX_POINTING_MISSES

    # More failures than the abort threshold, but never consecutive.
    spread = {0, 60, 120, 180, 240, 300}
    assert len(spread) > MAX_POINTING_MISSES
    _, skipped, _ = _sweep_with_failures(spread)
    assert spread <= set(skipped)


def test_an_abandoned_sweep_keeps_what_it_measured():
    """Losing hours of good columns to a late stall is worse than the stall.

    The abort exists so a dead mount cannot pass off an empty mask as a
    measurement. But az_step defaults to 5 degrees, so three consecutive misses
    span only a 15 degree arc — plausibly a local stall near the pole, not a
    dead mount. Discarding thirty good columns for that would be the worse bug,
    so the partial result rides on the exception.
    """
    import pytest

    from terminus.sweep import PointingError

    # Fail only a contiguous run late in the sweep, after real columns measured.
    with pytest.raises(PointingError) as exc:
        _sweep_with_failures({240, 270, 300})
    mask, skipped, profiles = exc.value.partial
    assert mask, "the columns measured before the stall must survive the abort"
    assert max(mask) < 240, "everything before the stall should be present"
    assert {240, 270} <= set(skipped)


def test_cli_saves_the_partial_mask_and_still_fails(tmp_path):
    """The abort must write the mask AND exit non-zero.

    Without a handler it leaves main() as a bare traceback having written
    nothing, so an operator loses the night's measurement and gets a stack trace
    instead of a horizon. Exiting zero would be worse still: a truncated sweep
    would then look finished to anything downstream.
    """
    from types import SimpleNamespace
    from unittest.mock import MagicMock, patch

    import pytest

    from terminus import cli
    from terminus.sweep import PointingError

    out = tmp_path / "h.yaml"
    err = PointingError("3 consecutive pointing failures ending at az 300")
    err.partial = ({0: (12.0, "tree"), 30: (18.0, "structure")}, [300], {})

    sc = MagicMock()
    sc.is_eq_mode.return_value = True
    cfg = {
        "site": {"lat": 39.79, "lon": -104.89},
        "sweep": {
            "az_step": 5,
            "alt_min": 0,
            "alt_max": 60,
            "sun_cone_deg": 30,
            "clear_thresh": 0.6,
        },
    }
    args = SimpleNamespace(
        dry_run=True, out=str(out), frames=None, az_start=0, az_end=350, no_export=True
    )

    with patch.object(cli, "run_sweep", side_effect=err):
        with pytest.raises(SystemExit) as exit_info:
            cli.cmd_sweep(sc, cfg, args)

    assert exit_info.value.code == 2, "a truncated sweep must not report success"
    assert out.exists(), "the partial mask must be on disk before the exit"
    text = out.read_text()
    assert "12.0" in text and "18.0" in text, "measured columns must survive the abort"


def test_a_skipped_column_is_recorded_once():
    """`skipped` goes into the mask metadata, so duplicates are a real defect.

    The earlier tests compared `set(skipped)` and a length bound, which let a
    duplicated append survive untouched.
    """
    _, skipped, _ = _sweep_with_failures({90, 180})
    assert skipped == sorted(skipped), "skipped should be recorded in sweep order"
    assert len(skipped) == len(set(skipped)), f"duplicate entries in {skipped}"
    assert set(skipped) == {90, 180}


def test_the_refine_loop_also_survives_a_pointing_failure():
    """Refinement is optional work on an already-measured mask.

    Nothing in the suite set `az_refine_deg`, so the refine loop's handler — the
    exact code this PR changed — never executed and could be narrowed back to
    `except SunGuard` with every test still green.
    """
    from unittest.mock import MagicMock, patch

    import numpy as np

    from terminus.sweep import Pointer, PointingError, Sky, run_sweep

    sky = Sky(39.7917, -104.894, 1600)
    sc = MagicMock()
    sc.equ_coord.return_value = (12.0, 20.0)
    sc.capture_rgb.return_value = np.full((8, 8, 3), 120.0, dtype=np.float32)
    cfg = {
        "sun_cone_deg": 30,
        "slew_step_deg": 5,
        "az_step": 60,
        "alt_min": 0,
        "alt_max": 60,
        "alt_tol": 2.5,
        "clear_thresh": 0.6,
        "az_refine_deg": 30,  # subdivide down to 30 deg
    }
    refined = []

    def point_to(self, az, alt):
        # Fail only at the midpoints refinement will try, never on the main grid.
        if round(az) % 60 == 30:
            refined.append(az)
            raise PointingError(f"never arrived at ({az:.0f},{alt:.0f})")
        return az, alt

    def fake_scan(ptr, sc_, az, *a, **k):
        # Refinement only subdivides where NEIGHBOURS DISAGREE by the threshold,
        # so the columns must alternate; a uniform horizon never triggers it.
        ptr.point_to(az, 30.0)
        return (10.0 if az % 120 == 0 else 40.0), "edge", "tree", []

    with (
        patch.object(Sky, "sun", lambda self: (297.0, -20.0)),
        patch.object(Pointer, "point_to", point_to),
        patch("terminus.sweep.column_touches_sun", lambda *a, **k: False),
        patch("terminus.sweep.scan_horizon", fake_scan),
    ):
        mask, _, _ = run_sweep(
            sc, sky, cfg, az_start=0, az_end=300, dry=False, log=lambda *a, **k: None
        )
    assert refined, "the refinement pass must have run for this test to mean anything"
    assert len(mask) >= 6, "a refinement failure must not discard the main sweep"


def test_rank_columns_orders_by_information_and_truncates():
    """Ranking is separate logic from next_column and was untested."""
    from terminus.plan import next_column, rank_columns

    def grad(a):
        return 5.0 if 95 <= a % 360 <= 105 else 0.02

    cands = [40.0, 100.0, 220.0, 300.0, 150.0]
    ranked = rank_columns([0.0, 180.0], cands, grad, top=3)
    assert len(ranked) == 3
    assert ranked[0] == next_column([0.0, 180.0], cands, grad)[0], "best must match next_column"
    assert len(set(ranked)) == 3, "no candidate may be ranked twice"


def test_solve_gains_equalises_a_dim_frame():
    """A mis-metered frame darkens every overlap it touches, which the sky
    classifier then reads as terrain. Silent: a wrong gain blends, not raises."""
    import numpy as np

    from terminus.mosaic import solve_gains

    m = np.ones((4, 8), bool)
    bright = np.full((4, 8, 3), 200.0, dtype=np.float32)
    dim = np.full((4, 8, 3), 100.0, dtype=np.float32)  # same scene, half exposure
    gains = solve_gains([(bright, m, 0, 0), (dim, m, 4, 0)])  # overlap on 4 columns
    assert len(gains) == 2
    ratio = (gains[1] * 100.0) / (gains[0] * 200.0)
    assert 0.8 < ratio < 1.25, f"overlap still disagrees by {ratio:.2f}x after solving"


def test_a_pointing_error_without_a_sweep_behind_it_still_saves_and_exits():
    """The abort handler must survive the earliest possible failure.

    `_goto_wait` raises PointingError from inside a slew, where no sweep state
    exists — notably from the sky-reference seeding goto, which is the FIRST
    goto of a run. If that reaches the CLI handler without a `.partial`, the
    handler's unpacking raises AttributeError and the whole fix is undone: bare
    traceback, nothing written. Exactly the bug it exists to prevent.
    """
    from types import SimpleNamespace
    from unittest.mock import MagicMock, patch

    import pytest

    from terminus import cli
    from terminus.sweep import PointingError

    raw = PointingError("never arrived at (117,75)")  # no .partial set
    assert raw.partial is None, "the class default must make this attribute safe to read"

    sc = MagicMock()
    sc.is_eq_mode.return_value = True
    args = SimpleNamespace(
        dry_run=True, out=None, frames=None, az_start=0, az_end=350, no_export=True
    )
    cfg = {
        "site": {"lat": 39.79, "lon": -104.89},
        "sweep": {
            "az_step": 5,
            "alt_min": 0,
            "alt_max": 60,
            "sun_cone_deg": 30,
            "clear_thresh": 0.6,
        },
    }
    with patch.object(cli, "run_sweep", side_effect=raw), patch.object(cli, "write_mask") as wm:
        with pytest.raises(SystemExit) as info:
            cli.cmd_sweep(sc, cfg, args)
    assert info.value.code == 2, "an abort must still report failure"
    assert wm.called, "it must reach the save path rather than dying on AttributeError"


def test_the_sky_reference_seeding_swallows_a_pointing_failure():
    """Seeding is a convenience, not a measurement.

    A mount that cannot reach the zenith will fail the columns too, where the
    miss counter can judge it on evidence. Letting the seeding goto abort the
    run instead reports the failure from the one place that has nothing to save.
    """
    from unittest.mock import MagicMock, patch

    import numpy as np

    from terminus.sweep import Pointer, PointingError, Sky, run_sweep

    sky = Sky(39.7917, -104.894, 1600)
    sc = MagicMock()
    sc.equ_coord.return_value = (12.0, 20.0)
    sc.capture_rgb.return_value = np.full((8, 8, 3), 120.0, dtype=np.float32)
    cfg = {
        "sun_cone_deg": 30,
        "slew_step_deg": 5,
        "az_step": 60,
        "alt_min": 0,
        "alt_max": 60,
        "alt_tol": 2.5,
        "clear_thresh": 0.6,
    }

    def point_to(self, az, alt):
        if round(alt) == 75:  # the seeding goto, and only it
            raise PointingError("never arrived at the zenith")
        return az, alt

    with (
        patch.object(Sky, "sun", lambda self: (297.0, -20.0)),
        patch.object(Pointer, "point_to", point_to),
        patch("terminus.sweep.column_touches_sun", lambda *a, **k: False),
    ):
        mask, _, _ = run_sweep(
            sc, sky, cfg, az_start=0, az_end=300, dry=False, log=lambda *a, **k: None
        )
    assert mask, "a failed sky reference must not abort the sweep before it starts"


def test_min_drop_frac_is_pinned_not_merely_incidental():
    """Every other night fixture departs the sky model so drastically that the
    threshold could be moved 0.5 -> 0.9 unnoticed.

    A real column can sit near it: distant terrain under light haze, or a low
    pale wall, reads a substantial fraction of the modelled sky. Get this wrong
    and the horizon is reported lower than it is, which is the dangerous
    direction — a planner will then start an imaging run into the obstruction.
    """
    from terminus.night import find_horizon

    sky = lambda a: 50.0 - 0.5 * a  # noqa: E731  measured skyglow gradient
    alts = [60, 50, 45, 40, 35, 30, 25, 20, 15, 10, 5, 0]

    def column(frac):
        return [(a, sky(a)) for a in alts if a >= 25] + [(a, sky(a) * frac) for a in alts if a < 25]

    # Comfortably below the threshold: terrain.
    alt, detail = find_horizon(column(0.45), sky_ref=22.0)
    assert alt == 20.0, f"a drop to 0.45x the model is terrain, got {alt} ({detail['reason']})"
    # Comfortably above it: still sky, however dim.
    alt, detail = find_horizon(column(0.55), sky_ref=22.0)
    assert alt is None, f"a drop to only 0.55x is not an obstruction, got {alt}"
    assert "open" in detail["reason"]


def test_an_abandoned_sweep_keeps_its_profiles_too():
    """profiles is the record that lets a run be re-judged without re-observing.

    The abort carried mask and skipped but could drop profiles unnoticed, which
    would leave _profiles.json empty for columns that were measured perfectly
    well before the stall.
    """
    import pytest

    from terminus.sweep import PointingError

    with pytest.raises(PointingError) as exc:
        _sweep_with_failures({240, 270, 300})
    mask, skipped, profiles = exc.value.partial
    assert profiles, "the raw brightness profiles must survive the abort"
    assert set(profiles) == set(mask), "every measured column keeps its profile"


def test_an_aborted_sweep_still_exports(tmp_path):
    """The operator gets the planning files for what WAS measured.

    Checking the abort before the export would silently skip .hrz/.stellarium.txt
    on a partial sweep — the YAML appears, the files a planner actually consumes
    do not, and nothing says so.
    """
    from types import SimpleNamespace
    from unittest.mock import MagicMock, patch

    import pytest

    from terminus import cli
    from terminus.sweep import PointingError

    out = tmp_path / "h.yaml"
    err = PointingError("3 consecutive pointing failures ending at az 300")
    err.partial = ({0: (12.0, "tree"), 30: (18.0, "structure")}, [300], {})

    sc = MagicMock()
    sc.is_eq_mode.return_value = True
    cfg = {
        "site": {"lat": 39.79, "lon": -104.89},
        "sweep": {
            "az_step": 5,
            "alt_min": 0,
            "alt_max": 60,
            "sun_cone_deg": 30,
            "clear_thresh": 0.6,
        },
    }
    args = SimpleNamespace(
        dry_run=True, out=str(out), frames=None, az_start=0, az_end=350, no_export=False
    )
    with patch.object(cli, "run_sweep", side_effect=err):
        with pytest.raises(SystemExit) as info:
            cli.cmd_sweep(sc, cfg, args)
    assert info.value.code == 2
    assert (tmp_path / "h.hrz").exists(), "the partial sweep must still export for N.I.N.A."
    assert (tmp_path / "h.stellarium.txt").exists()


def test_native_column_converges_at_its_shipped_defaults():
    """Pin the contract, not the iteration count.

    Dropping `iters` from 6 to 5 left every test green while quietly degrading
    convergence at high tilt. Asserting "5 fails and 6 passes" would over-fit to
    one fixture; asserting that the shipped defaults actually converge to `tol`
    catches the same regression for the right reason.
    """
    import math

    from terminus.orient import _rotate_scalar, _wrap180, native_column

    for tilt in (3.0, 6.0, 9.0, 12.0, 15.0):
        worst = 0.0
        for target in range(0, 360, 7):
            phi, raw = native_column(_skyline, float(target), 40.0, tilt, 60.0)
            landed, _ = _rotate_scalar(phi + 40.0, raw, tilt, 60.0)
            worst = max(worst, abs(_wrap180(target - landed)))
        # 0.02 deg of azimuth is about 0.1 deg of altitude even on a steep
        # 5 deg/deg horizon — an order below the measurement error. Measured:
        # 0.002 at the shipped iters=8, 0.013 at 6, 0.038 at 5.
        assert worst < 0.02, f"tilt {tilt}: worst landing error {worst:.4f} deg at the defaults"
        assert math.isfinite(worst)


@pytest.mark.parametrize("boom", [OSError("socket gone"), None])
def test_a_failing_stop_view_never_costs_the_measurement(tmp_path, boom):
    """stop_view runs through client.call, which reconnects on a dropped socket.

    That path raises OSError from the socket itself but SeestarError when
    re-authentication fails or the re-entrancy guard trips. Catching only the
    first left the second skipping write_mask — the same bug one exception class
    over, and it would have exited 1 rather than the 2 that means "abandoned but
    saved". A view left running is the documented precondition for the frozen
    RTSP stream, so it is worth attempting and reporting; it is not worth a
    night's data.
    """
    from types import SimpleNamespace
    from unittest.mock import MagicMock, patch

    from terminus import cli
    from terminus.client import SeestarError

    out = tmp_path / "h.yaml"
    sc = MagicMock()
    sc.is_eq_mode.return_value = True
    # The pre-sweep stop_view is deliberately NOT guarded — nothing is measured
    # yet, so failing loudly there is right. Only the post-sweep call must be
    # survivable, so let the first through and break the second.
    sc.stop_view.side_effect = [None, boom or SeestarError("reconnected but auth failed")]
    cfg = {
        "site": {"lat": 39.79, "lon": -104.89},
        "sweep": {
            "az_step": 5,
            "alt_min": 0,
            "alt_max": 60,
            "sun_cone_deg": 30,
            "clear_thresh": 0.6,
        },
    }
    args = SimpleNamespace(
        dry_run=False, out=str(out), frames=None, az_start=0, az_end=350, no_export=True
    )
    with patch.object(cli, "run_sweep", return_value=({0: (12.0, "tree")}, [], {})):
        cli.cmd_sweep(sc, cfg, args)  # must NOT raise
    assert out.exists(), "a failed stop_view must not cost the mask"
    assert "12.0" in out.read_text()


# ---- public API surface ----------------------------------------------------
def test_importing_terminus_does_not_pull_in_torch_or_transformers():
    """The heavy optional stack must stay optional.

    torch and transformers are installed in this dev environment, so absence
    cannot be the test — a fresh interpreter is asked what it actually loaded.
    Importing them eagerly would add seconds to every CLI invocation and make
    the package uninstallable on a Pi that only ever runs the scope path.
    """
    import subprocess
    import sys

    out = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, terminus; "
            "print(int(any(m == 'torch' or m.startswith('torch.') for m in sys.modules))); "
            "print(int(any(m == 'transformers' for m in sys.modules)))",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert out.returncode == 0, out.stderr
    torch_loaded, transformers_loaded = out.stdout.split()
    assert torch_loaded == "0", "importing terminus must not load torch"
    assert transformers_loaded == "0", "importing terminus must not load transformers"


def _in_clean_interpreter(body, timeout=180):
    """Run `body` in a fresh interpreter and return its stdout.

    Anything asking "what does importing terminus do" has to run in its own
    process. Inside the suite the answer is already contaminated: an earlier test
    doing `from terminus.skymask import horizon_rows` binds `terminus.skymask`
    as a side effect of the import system, so a reachability check run in-process
    passes whether or not `__init__` imports it at all.
    """
    import subprocess
    import sys

    out = subprocess.run(
        [sys.executable, "-c", body], capture_output=True, text=True, timeout=timeout
    )
    assert out.returncode == 0, f"subprocess failed:\n{out.stdout}\n{out.stderr}"
    return out.stdout.strip()


def test_importing_terminus_does_not_shell_out():
    """Hugin is optional and probing for it is not free.

    `mosaic.hugin_available()` exists so a caller can ask; the import must not
    ask on its behalf. Every spawn route is blocked, not just `subprocess.run` —
    `Popen` and `os.system` bypass it entirely, and `check_output` only happens
    to be caught because it delegates to `run`.
    """
    body = (
        "import os, subprocess\n"
        "def boom(*a, **k):\n"
        "    raise AssertionError('terminus spawned a process during import')\n"
        "subprocess.run = boom\n"
        "subprocess.Popen = boom\n"
        "subprocess.call = boom\n"
        "subprocess.check_call = boom\n"
        "subprocess.check_output = boom\n"
        "os.system = boom\n"
        "os.popen = boom\n"
        "import terminus\n"
        "print('ok')\n"
    )
    assert _in_clean_interpreter(body) == "ok"


def test_photo_pipeline_is_reachable_from_the_package():
    """The published method must not be absent from the package's own API.

    Checked in a clean interpreter: importing `terminus` alone must bind all
    five, with nothing else having imported them first.
    """
    body = (
        "import terminus\n"
        "mods = ('skymask', 'mosaic', 'orient', 'plan', 'night')\n"
        "missing = [m for m in mods if not hasattr(terminus, m)]\n"
        "unlisted = [m for m in mods if m not in terminus.__all__]\n"
        "entry = all([\n"
        "    callable(terminus.mosaic.solve), callable(terminus.mosaic.render),\n"
        "    callable(terminus.mosaic.composite), callable(terminus.skymask.sky_mask),\n"
        "    callable(terminus.skymask.available), callable(terminus.skymask.horizon_band),\n"
        "    callable(terminus.orient.fit), callable(terminus.orient.from_mask),\n"
        "    callable(terminus.orient.residuals), callable(terminus.night.find_horizon),\n"
        "])\n"
        "print(repr((missing, unlisted, entry)))\n"
    )
    missing, unlisted, entry = eval(_in_clean_interpreter(body))
    assert not missing, f"importing terminus does not bind: {missing}"
    assert not unlisted, f"not in __all__: {unlisted}"
    assert entry, "an entry point named in the ticket is missing or not callable"


def test_every_exported_name_actually_exists():
    """A stale __all__ entry raises AttributeError on `from terminus import *`."""
    import terminus

    missing = [n for n in terminus.__all__ if not hasattr(terminus, n)]
    assert not missing, f"__all__ names nothing: {missing}"


def test_segmentation_backend_tracks_the_imports_not_the_environment():
    """`available('segment')` must answer about torch, not about this machine.

    The previous version blocked torch/transformers with a meta_path finder and
    asserted False. That is inert wherever they are genuinely absent — which is
    CI, since `uv sync --dev` installs only the declared dependencies and neither
    is one. The finder could be deleted outright and the test still passed.

    So both directions are forced here, with stubs rather than with whatever
    happens to be installed: make the imports succeed and the answer must be
    True; make them fail and it must be False. That proves the function responds
    to the imports, and it reads the same in CI as on a workstation that has the
    segmentation stack installed.
    """
    present = (
        "import sys, types\n"
        "for n in ('torch', 'torchvision', 'transformers'):\n"
        "    sys.modules[n] = types.ModuleType(n)\n"
        "from terminus import skymask\n"
        "print(repr((skymask.available('segment'), skymask.available('heuristic'))))\n"
    )
    absent = (
        "import sys\n"
        "class Block:\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name.split('.')[0] in ('torch', 'torchvision', 'transformers'):\n"
        "            raise ImportError('blocked for test: ' + name)\n"
        "        return None\n"
        "sys.meta_path.insert(0, Block())\n"
        "for n in ('torch', 'torchvision', 'transformers'):\n"
        "    sys.modules.pop(n, None)\n"
        "from terminus import skymask\n"
        "print(repr((skymask.available('segment'), skymask.available('heuristic'))))\n"
    )
    seg_yes, heur_yes = eval(_in_clean_interpreter(present))
    seg_no, heur_no = eval(_in_clean_interpreter(absent))

    assert seg_yes is True, "with both modules importable, the segment backend must report True"
    assert seg_no is False, "without them, it must report False rather than raising"
    assert heur_yes is True and heur_no is True, "the heuristic backend needs nothing"


def test_hugin_probe_reports_rather_than_raising():
    """`hugin_available()` must answer on a machine with no Hugin installed."""
    from unittest.mock import patch

    import terminus

    with patch("shutil.which", return_value=None):
        assert terminus.mosaic.hugin_available() is False
    with patch("shutil.which", side_effect=lambda t: "/usr/bin/" + t):
        assert terminus.mosaic.hugin_available() is True


# ---- offline photo CLI (terminus-19) ---------------------------------------
def _synthetic_panorama(path, w=720, h=360, blocked=None):
    """A panorama with a sinusoidal green skyline, optionally blocked to the top."""
    import numpy as np
    from PIL import Image

    img = np.zeros((h, w, 3), np.uint8)
    img[:, :] = (120, 150, 200)
    for x in range(w):
        img[int(h / 2 + (h / 12) * np.sin(2 * np.pi * x / w)) :, x] = (60, 110, 70)
    if blocked:
        img[:, blocked[0] : blocked[1]] = (60, 110, 70)
    Image.fromarray(img).save(path)
    return w, h


def test_skymask_writes_an_unoriented_mask_with_the_extra_columns(tmp_path):
    """The two fields that had nowhere to live in the schema must survive.

    `clipped` records that the obstruction ran off the top of the data — where
    the FRAME was cropped, never where the horizon is — and gap_fraction/uncertainty
    carry how gappy the canopy is. Without them a consumer cannot tell a bound
    from a measurement.
    """
    from terminus.cli import main
    from terminus.export import load_columns, load_mask

    pano = tmp_path / "pano.png"
    _synthetic_panorama(str(pano), blocked=(100, 140))
    main(["skymask", str(pano), "--backend", "heuristic", "--az-step", "10"])

    out = tmp_path / "pano_mask.yaml"
    assert out.exists()
    meta, cols = load_columns(str(out))
    assert meta["oriented"] is False, "a photo mask is not in true azimuth yet"
    assert meta["backend"] == "heuristic"

    clipped = sorted(a for a, c in cols.items() if c["clipped"])
    assert clipped, "the column blocked to the top of frame must be marked clipped"
    assert all(50 <= a <= 70 for a in clipped), f"clipped in the wrong place: {clipped}"
    for c in cols.values():
        assert "gap_fraction" in c and "uncertainty" in c
        assert c["uncertainty"] > 0

    # The three-tuple contract every exporter depends on is untouched.
    _, rows = load_mask(str(out))
    assert rows and all(len(r) == 3 for r in rows)


def test_an_unoriented_mask_says_so_in_its_header(tmp_path):
    """The header is what a human reads. It must not claim true north.

    The file previously said 'azimuth/altitude are TRUE (polar-aligned)'
    unconditionally, which on a photo-derived mask invites a planner to point at
    a horizon rotated by an unknown amount.
    """
    from terminus.export import write_mask

    p = tmp_path / "u.yaml"
    write_mask(str(p), {0: (10.0, "tree")}, [], {"oriented": False})
    head = p.read_text()
    assert "UNORIENTED" in head and "NOT true north" in head
    assert "Solve the" in head and "orientation" in head

    q = tmp_path / "o.yaml"
    write_mask(str(q), {0: (10.0, "tree")}, [], {"lat": 40})
    assert "UNORIENTED" not in q.read_text(), "a scope mask is oriented; do not warn"
    assert "are TRUE (polar-aligned)" in q.read_text()


def test_skymask_refuses_a_coverage_map_from_a_different_run(tmp_path, capsys):
    """Mismatched coverage would silently mask the wrong pixels.

    It must fail loudly and say why: the CLI turns this into exit 1 with a
    message, not a traceback and not a quietly wrong horizon.
    """
    import numpy as np
    import pytest

    from terminus.cli import main

    pano = tmp_path / "pano.png"
    w, h = _synthetic_panorama(str(pano))
    cov = tmp_path / "wrong.npy"
    np.save(cov, np.ones((h // 2, w // 2)))
    with pytest.raises(SystemExit) as info:
        main(["skymask", str(pano), "--backend", "heuristic", "--coverage", str(cov)])
    assert info.value.code == 1
    assert "does not match" in capsys.readouterr().err
    assert not (tmp_path / "pano_mask.yaml").exists(), "must not write a mask it could not trust"


def test_offline_commands_need_no_config_file(tmp_path, monkeypatch):
    """A user with photographs and no telescope must not have to write one."""
    import pytest

    from terminus.cli import main

    monkeypatch.chdir(tmp_path)  # no config.toml here
    pano = tmp_path / "pano.png"
    _synthetic_panorama(str(pano))
    main(["skymask", str(pano), "--backend", "heuristic", "--az-step", "30"])
    assert (tmp_path / "pano_mask.yaml").exists()

    # `export` is offline too — re-exporting a mask must not demand a config
    # file. CI, which has none, is what caught this; a local run never could.
    from terminus.export import write_mask

    write_mask(str(tmp_path / "m.yaml"), {0: (10.0, "tree")}, [], {"lat": 40})
    main(["export", str(tmp_path / "m.yaml")])
    assert (tmp_path / "m.hrz").exists()

    # and the scope commands still do demand one
    with pytest.raises(SystemExit):
        main(["preflight"])


def test_mosaic_without_hugin_explains_itself(tmp_path):
    """Not an ImportError traceback — the install message require_hugin produces."""
    from unittest.mock import patch

    import pytest

    from terminus.cli import main

    with patch("shutil.which", return_value=None):
        with pytest.raises(SystemExit) as info:
            main(["mosaic", str(tmp_path)])
    assert info.value.code == 1


def test_segment_classes_votes_by_majority_across_tiles():
    """Class indices are labels, not magnitudes — averaging them is meaningless.

    A wide panorama is segmented in overlapping square tiles, so the seams need
    a rule. `segment_sky` can average a sky FRACTION; classes cannot, and the
    mean of 'tree' and 'building' is neither.
    """
    from unittest.mock import patch

    import numpy as np
    from PIL import Image

    from terminus import skymask

    h, w = 40, 160
    img = Image.new("RGB", (w, h))

    def fake(image):
        # every tile says class 4 on top, 17 below — majority must preserve both
        a = np.full((image.size[1], image.size[0]), 4, dtype=int)
        a[image.size[1] // 2 :, :] = 17
        return a

    with patch.object(skymask, "_SEG", fake):
        out = skymask.segment_classes(img)
    assert out.shape == (h, w)
    assert set(np.unique(out)) == {4, 17}
    assert (out[: h // 2] == 4).all() and (out[h // 2 :] == 17).all()


def test_an_unoriented_mask_cannot_be_exported(tmp_path):
    """The refusal must be structural, not a message in one command.

    A photo mask is in the panorama's own azimuth until the orientation is
    solved. Exported anyway it produces a file that looks like every other
    horizon — the .hrz header even declares "true-north azimuth" — while being
    rotated by an unknown amount. A planner then refuses targets that are clear
    and accepts targets sitting behind a roof, with nothing to say why.

    Previously the only refusal was a print inside `cmd_skymask`, which never
    exports anyway; `terminus export` on the same file produced the wrong .hrz
    silently.
    """
    import pytest

    from terminus.export import UnorientedMask, export_all, to_nina_hrz, write_mask

    p = tmp_path / "u.yaml"
    write_mask(str(p), {0: (10.0, "tree"), 90: (20.0, "structure")}, [], {"oriented": False})

    with pytest.raises(UnorientedMask, match="UNORIENTED"):
        export_all(str(p), str(tmp_path / "out"))
    assert not (tmp_path / "out.hrz").exists(), "nothing may be written before the refusal"

    # The documented direct-import path refuses too, since its header is the lie.
    _, rows = __import__("terminus.export", fromlist=["load_mask"]).load_mask(str(p))
    with pytest.raises(UnorientedMask):
        to_nina_hrz(rows, {"oriented": False})

    # Escape hatch, for someone who knows the azimuths are already true.
    hrz, txt = export_all(str(p), str(tmp_path / "forced"), allow_unoriented=True)
    assert os.path.exists(hrz) and os.path.exists(txt)


def test_an_oriented_mask_still_exports(tmp_path):
    """The guard must not fire on a scope-measured mask, which has no flag."""
    from terminus.export import export_all, write_mask

    p = tmp_path / "o.yaml"
    write_mask(str(p), {0: (10.0, "tree")}, [], {"lat": 40})
    hrz, _ = export_all(str(p), str(tmp_path / "ok"))
    assert os.path.exists(hrz)


def test_cli_export_refuses_an_unoriented_mask(tmp_path, capsys):
    """Exit 1 with the reason, not a traceback."""
    import pytest

    from terminus.cli import main
    from terminus.export import write_mask

    p = tmp_path / "u.yaml"
    write_mask(str(p), {0: (10.0, "tree")}, [], {"oriented": False})
    with pytest.raises(SystemExit) as info:
        main(["export", str(p)])
    assert info.value.code == 1
    assert "UNORIENTED" in capsys.readouterr().err
    main(["export", str(p), "--allow-unoriented"])  # must not raise
    assert (tmp_path / "u.hrz").exists()


def test_a_scope_mask_gains_no_photo_field_header(tmp_path):
    """Photo-only field explanations must not appear on a mask without them.

    The header explained clipped/gap_fraction/uncertainty unconditionally, so
    every scope-measured mask grew three comment lines about fields it does not
    carry — while the docstring claimed such a mask was unchanged.

    A UNIVERSAL note is a different matter and belongs on every mask, so the
    position-specificity warning is asserted present rather than counted as
    bloat. Counting comment lines would have conflated the two.
    """
    from terminus.export import write_mask

    plain = tmp_path / "scope.yaml"
    write_mask(str(plain), {0: (12.0, "tree"), 90: (30.5, "structure")}, [180], {"lat": 40})
    head = [ln for ln in plain.read_text().splitlines() if ln.startswith("#")]
    assert not any(
        "clipped" in ln or "gap_fraction" in ln for ln in head
    ), "a scope mask must not explain fields it does not carry"
    assert any("POSITION-SPECIFIC" in ln for ln in head), "the position note is universal"
    # The count is still asserted. Dropping it for substring checks alone lost
    # the ability to catch unrelated header bloat, which is what this test was
    # originally for — a header that grows quietly is how the photo-field lines
    # ended up on scope masks in the first place. Adding a universal note is
    # legitimate and should require deliberately updating this number.
    # 12 = 3 original + 4 explaining an EMPTY type + 4 position note + 1
    # "skipped azimuths", which this fixture triggers by passing [180]. The
    # empty-type note is universal: ANY mask may hold a column that was measured
    # but not named, and a bare "type:" with nothing said about it reads as a
    # bug rather than as an honest gap.
    assert len(head) == 12, f"header changed size; update deliberately, got {len(head)}"

    rich = tmp_path / "photo.yaml"
    write_mask(str(rich), {0: {"alt": 12.0, "type": "tree", "clipped": True}}, [], {})
    rich_head = [ln for ln in rich.read_text().splitlines() if ln.startswith("#")]
    assert any("clipped" in ln for ln in rich_head), "explain the fields that ARE present"


def test_every_exporter_refuses_an_unoriented_mask(tmp_path):
    """Not most of them. The first guard covered two of the three call sites.

    `to_stellarium_txt` is the one that matters most: the format forbids
    comments, so an unoriented landscape cannot even carry a warning inside the
    file the way a .hrz header could.
    """
    import pytest

    from terminus.export import (
        UnorientedMask,
        load_mask,
        to_nina_hrz,
        to_stellarium_txt,
        write_mask,
    )

    p = tmp_path / "u.yaml"
    write_mask(str(p), {0: (10.0, "tree"), 90: (20.0, "structure")}, [], {"oriented": False})
    _, rows = load_mask(str(p))
    unoriented = {"oriented": False}

    with pytest.raises(UnorientedMask):
        to_nina_hrz(rows, unoriented)
    with pytest.raises(UnorientedMask):
        to_stellarium_txt(rows, unoriented)

    # Both still work when the mask is oriented, or when overridden.
    assert to_nina_hrz(rows, {"lat": 40})
    assert to_stellarium_txt(rows, {"lat": 40})
    assert to_stellarium_txt(rows, unoriented, allow_unoriented=True)


def test_the_oriented_flag_is_interpreted_not_identity_checked(tmp_path):
    """The mask is documented as hand-editable, so people will write these.

    `meta.get("oriented") is False` waves `oriented: 'false'` and `oriented: 0`
    straight through to a planner, because neither is the False singleton.
    """
    import pytest

    from terminus.export import UnorientedMask, export_all, is_oriented, write_mask

    for value in (False, "false", "False", "no", 0, "0", "off"):
        assert not is_oriented({"oriented": value}), f"{value!r} should read as unoriented"
        p = tmp_path / f"m{hash(str(value))}.yaml"
        write_mask(str(p), {0: (10.0, "tree")}, [], {"oriented": value})
        with pytest.raises(UnorientedMask):
            export_all(str(p), str(tmp_path / "o"))

    for value in (True, "true", "yes", 1):
        assert is_oriented({"oriented": value}), f"{value!r} should read as oriented"

    # Absent means oriented: a scope mask has never carried the key.
    assert is_oriented({"lat": 40})
    assert is_oriented({})
    assert is_oriented(None)


def test_an_unrecognised_oriented_value_raises_rather_than_guessing():
    """Matching only the false words fails open, which is the same bug inverted.

    `oriented: flase` is a typo a person will make in a file the project
    documents as hand-editable. Read as a negative allowlist it means "not one
    of the false words, therefore true" — and a horizon rotated by an unknown
    amount goes out with no warning. A typo is not evidence of orientation.
    """
    import pytest

    from terminus.export import is_oriented

    for bad in ("flase", "nope", "unoriented", "maybe", "TRUEISH"):
        with pytest.raises(ValueError, match="neither true nor false"):
            is_oriented({"oriented": bad})

    # The vocabulary it does accept, both ways.
    for good in ("true", "TRUE", " yes ", "y", "on", "1"):
        assert is_oriented({"oriented": good}) is True
    for good in ("false", "No", "n", "off", "0", ""):
        assert is_oriented({"oriented": good}) is False


def test_a_typo_in_the_oriented_flag_blocks_export(tmp_path):
    """The refusal must reach the export path, not just the helper."""
    import pytest

    from terminus.export import export_all

    p = tmp_path / "typo.yaml"
    p.write_text("meta: {oriented: flase}\nhorizon:\n  0: {alt: 10.0, type: tree}\n")
    with pytest.raises(ValueError, match="neither true nor false"):
        export_all(str(p), str(tmp_path / "out"))
    assert not (tmp_path / "out.hrz").exists()


def test_an_unreadable_mask_flag_reaches_the_user_as_a_message(tmp_path, capsys):
    """A typo must not produce a traceback while a valid refusal produces prose.

    `main()` caught UnorientedMask but not the plain ValueError raised for an
    unparseable flag, so `oriented: flase` — the easier of the two to fix —
    was the one that dumped a stack trace.
    """
    import pytest

    from terminus.cli import main
    from terminus.export import MaskError, UnorientedMask

    assert issubclass(UnorientedMask, MaskError), "the CLI catches the base"

    p = tmp_path / "typo.yaml"
    p.write_text("meta: {oriented: flase}\nhorizon:\n  0: {alt: 10.0, type: tree}\n")
    with pytest.raises(SystemExit) as info:
        main(["export", str(p)])
    assert info.value.code == 1
    assert "neither true nor false" in capsys.readouterr().err


def test_write_mask_does_not_truncate_on_a_bad_flag(tmp_path):
    """The flag is validated before the file is opened, so a failed write
    leaves whatever was there rather than an empty file."""
    import pytest

    from terminus.export import MaskError, write_mask

    p = tmp_path / "existing.yaml"
    p.write_text("PRE-EXISTING\n")
    with pytest.raises(MaskError):
        write_mask(str(p), {0: (1.0, "tree")}, [], {"oriented": "flase"})
    assert p.read_text() == "PRE-EXISTING\n"


def test_a_mask_written_before_the_rename_still_loads(tmp_path):
    """`porosity` was the wrong word, but files carrying it already exist.

    Gap fraction is the field's term (Jonckheere et al. 2004); porosity is
    windbreak vocabulary. The rename is worth doing before the API is public —
    but a mask written this morning must not become unreadable, so the old key
    is accepted on load and never written back.
    """
    from terminus.export import load_columns

    p = tmp_path / "old.yaml"
    p.write_text(
        "meta: {lat: 40}\n"
        "horizon:\n"
        "  0: {alt: 12.0, type: tree, clipped: True, porosity: 0.4, uncertainty: 4.2}\n"
    )
    _, cols = load_columns(str(p))
    assert cols[0]["gap_fraction"] == 0.4, "the old spelling must still be read"
    assert "porosity" not in cols[0], "but it is not carried forward under the old name"


def test_the_new_spelling_wins_when_both_are_present(tmp_path):
    """A hand-edited file could carry both. Prefer the current name."""
    from terminus.export import load_columns

    p = tmp_path / "both.yaml"
    p.write_text(
        "meta: {lat: 40}\n"
        "horizon:\n"
        "  0: {alt: 12.0, type: tree, porosity: 0.1, gap_fraction: 0.9}\n"
    )
    _, cols = load_columns(str(p))
    assert cols[0]["gap_fraction"] == 0.9


def test_gap_fraction_is_written_not_porosity(tmp_path):
    """The wrong word must not reappear in anything we emit."""
    from terminus.export import write_mask

    p = tmp_path / "new.yaml"
    write_mask(str(p), {0: {"alt": 12.0, "type": "tree", "gap_fraction": 0.4}}, [], {})
    text = p.read_text()
    assert "gap_fraction: 0.4" in text
    assert "porosity" not in text


# ---- planner feasibility (terminus-40) -------------------------------------
def _pointer_with_sun(sun_az, sun_alt, cone=30):
    from unittest.mock import MagicMock

    from terminus.sweep import Pointer, Sky

    sky = Sky(39.7917, -104.894, 1600)
    sc = MagicMock()
    sc.equ_coord.return_value = (12.0, 20.0)
    return Pointer(sc, sky, cone, 5), sky


def test_a_column_can_clear_the_cone_and_still_be_unreachable():
    """The endpoint is not the test — the slew PATH is.

    Measured case: with the Sun up, a column whose endpoint sits comfortably
    outside the cone is still refused because the RA/Dec path passes through it.
    Ranking on endpoint separation would have chosen it, which is the
    EQ-goto-swings-past-the-Sun failure the guard was written for.
    """
    from unittest.mock import patch

    from terminus.sweep import Sky, ang_sep, reachable_now

    ptr, _ = _pointer_with_sun(123.0, 55.8)
    with patch.object(Sky, "sun", lambda self: (123.0, 55.8)):
        ok = reachable_now(ptr, 30.0)
        outside = [az for az in range(0, 360, 20) if ang_sep(az, 30.0, 123.0, 55.8) >= 30.0]
        refused = [az for az in outside if not ok(az)]
    assert refused, "no column had a clear endpoint and a blocked path; test is not exercising"


def test_the_planner_never_returns_a_column_it_cannot_reach():
    from terminus.plan import next_column, rank_columns

    cands = [10.0, 100.0, 200.0, 300.0]
    # 100 is by far the most informative, and is exactly the one refused.
    grad = lambda a: 5.0 if a == 100.0 else 0.02  # noqa: E731
    ok = lambda a: a != 100.0  # noqa: E731

    assert next_column([0.0, 180.0], cands, grad)[0] == 100.0, "unfiltered it wins"
    pick, _ = next_column([0.0, 180.0], cands, grad, reachable=ok)
    assert pick != 100.0 and pick in cands
    assert 100.0 not in rank_columns([0.0, 180.0], cands, grad, reachable=ok)


def test_filtering_happens_before_the_criterion_not_after():
    """Filter-then-rank gives the best feasible CONFIGURATION.

    Rank-then-filter gives the best infeasible column and a fallback, which is a
    different and worse answer: the columns are chosen jointly, so removing one
    changes which of the others is worth having.
    """
    from terminus.plan import rank_columns

    cands = [0.0, 45.0, 90.0, 135.0, 180.0]
    grad = lambda a: 1.0  # noqa: E731
    ok = lambda a: a not in (90.0, 135.0)  # noqa: E731

    ranked_then_filtered = [c for c in rank_columns([0.0], cands, grad, top=5) if ok(c)]
    filtered_then_ranked = rank_columns([0.0], cands, grad, top=5, reachable=ok)
    assert set(filtered_then_ranked).isdisjoint({90.0, 135.0})
    # Same members here, but the ORDER is decided among feasible columns only.
    assert filtered_then_ranked == sorted(
        filtered_then_ranked, key=lambda c: ranked_then_filtered.index(c)
    ) or set(filtered_then_ranked) == set(ranked_then_filtered)


def test_nothing_reachable_is_a_state_not_a_crash():
    """The Sun moves; waiting is sometimes the right move, so say so quietly."""
    import numpy as np

    from terminus.plan import next_column, partition

    cands = [10.0, 20.0]
    pick, score = next_column([0.0], cands, lambda a: 1.0, reachable=lambda a: False)
    assert pick is None and score == -np.inf
    feasible, refused = partition(cands, lambda a: False)
    assert feasible == [] and refused == cands


def test_hours_until_endpoint_clear_reports_a_wait(monkeypatch):
    """A refused column is not permanently lost — report when it frees up."""
    import datetime

    from terminus import sweep

    sky = sweep.Sky(39.7917, -104.894, 1600)
    base = sweep._now()

    # Sun sits on the column now, and has moved well away a few hours later.
    class _FakeSunCoord:
        def __init__(self, az, alt):
            self.az = type("D", (), {"deg": az})()
            self.alt = type("D", (), {"deg": alt})()

        def transform_to(self, frame):
            return self

    def fake_get_sun(when):
        elapsed = (when - base).sec / 3600.0
        return _FakeSunCoord(100.0 + 15.0 * elapsed, 40.0)

    monkeypatch.setattr(sweep, "get_sun", fake_get_sun)
    wait = sweep.hours_until_endpoint_clear(sky, 100.0, 30.0, cone=30.0, within=8.0, step_min=30.0)
    assert wait is not None and 1.0 <= wait <= 4.0, wait

    # A column the Sun never approaches is available immediately.
    assert sweep.hours_until_endpoint_clear(sky, 280.0, 30.0, cone=30.0) == 0.0
    assert isinstance(datetime.timedelta(minutes=1), datetime.timedelta)


def test_the_feasibility_check_tests_the_same_azimuth_point_to_will_slew_to():
    """The predicate and the slew must agree about which azimuth is meant.

    `point_to` nudges an azimuth off the celestial pole before converting to
    RA/Dec, so a predicate that checked the RAW azimuth would be answering about
    a different path than the one later flown. Both call `avoid_pole`; nothing
    pinned that they must, and removing it from the predicate left the whole
    suite green.
    """
    from unittest.mock import patch

    from terminus.sweep import Sky, reachable_now

    ptr, sky = _pointer_with_sun(123.0, 55.8)
    # Due north at altitude == latitude IS the pole, which is what avoid_pole
    # exists to dodge; at this site that is az 0, alt 39.79.
    pole_az, pole_alt = 0.0, 39.7917
    nudged_az, nudged_alt = ptr.avoid_pole(pole_az, pole_alt)
    assert nudged_az != pole_az, "fixture is wrong: this azimuth is not pole-adjacent"

    seen = []
    real = Sky.altaz_to_radec

    def record(self, az, alt, when=None):
        seen.append(round(az, 4))
        return real(self, az, alt, when)

    with (
        patch.object(Sky, "sun", lambda self: (123.0, 55.8)),
        patch.object(Sky, "altaz_to_radec", record),
    ):
        reachable_now(ptr, pole_alt)(pole_az)

    # avoid_pole itself converts candidates while searching, so the raw azimuth
    # legitimately appears. What matters is the LAST conversion — the one whose
    # RA/Dec the path check actually uses.
    assert seen[-1] == round(nudged_az, 4), (
        f"predicate's path target was az {seen[-1]} but point_to would slew to "
        f"{nudged_az:.4f} — the check and the slew disagree about the path"
    )


def test_a_set_sun_blocks_nothing():
    """Below SUN_SAFE_ALT the Earth is in the way, so every column is clear now.

    Without this branch the function would step forward through the night
    looking for a separation that is already irrelevant, and report a wait where
    the answer is zero. Deleting the check left the whole suite green.
    """
    from terminus import sweep

    sky = sweep.Sky(39.7917, -104.894, 1600)

    class _Set:
        az = type("D", (), {"deg": 100.0})()
        alt = type("D", (), {"deg": -20.0})()

        def transform_to(self, frame):
            return self

    # The column must sit INSIDE the cone in angular separation, or the
    # separation test passes on its own and the altitude branch is never
    # exercised — which is exactly how the first version of this test let the
    # mutation survive. Sun at az 100 alt -20, column at az 100 alt 0: 20 deg
    # apart, inside a 30 deg cone, and refused by separation alone.
    orig = sweep.get_sun
    try:
        sweep.get_sun = lambda when: _Set()
        assert sweep.ang_sep(100.0, 0.0, 100.0, -20.0) < 30.0, "fixture must be inside the cone"
        assert sweep.hours_until_endpoint_clear(sky, 100.0, 0.0, cone=30.0) == 0.0
    finally:
        sweep.get_sun = orig


def test_truncation_happens_after_filtering_not_before():
    """`top` must select among FEASIBLE columns, not among all of them.

    The order the docstring claims — filter, then rank, then truncate — only
    shows itself when `top` is smaller than the candidate list. Rank-then-
    truncate-then-filter can return NOTHING while plenty of reachable columns
    exist, because the top slots are all taken by columns the Sun refuses. The
    two earlier tests both passed `top == len(candidates)`, so truncation never
    bit and neither caught it.
    """
    from terminus.plan import rank_columns

    # The three most informative columns are exactly the ones blocked.
    blocked = {90.0, 95.0, 100.0}
    cands = [float(a) for a in range(0, 360, 30)] + sorted(blocked)
    grad = lambda a: 5.0 if a in blocked else 0.05  # noqa: E731
    ok = lambda a: a not in blocked  # noqa: E731

    picks = rank_columns([0.0, 180.0], cands, grad, top=3, reachable=ok)
    assert len(picks) == 3, f"asked for 3 feasible columns, got {picks}"
    assert not (set(picks) & blocked), "returned a column the Sun refuses"


# ---- type-weighted fiducials (terminus-36) ---------------------------------
def test_vegetation_and_structure_move_the_fit_by_different_amounts():
    """The whole point: identical SNR and identical error must NOT count equally.

    SNR says how well an edge was DETECTED; type says how well the thing
    detected STAYS PUT between the photograph and the measurement. A crisp
    canopy edge scores excellently on the first and badly on the second, and
    before this the fit trusted it exactly as much as a roofline.

    Two otherwise identical fiducials carry the same wrong altitude. The one
    labelled vegetation must drag the solved yaw less far than the one labelled
    structure.
    """
    from terminus.orient import Fiducial, fit
    from terminus.plan import as_fiducial

    truth = 40.0
    good = _place(_skyline, truth, 0.0, 0.0, 0.0, step=30.0)

    def solve(uncertainty):
        fids = list(good[:-1])
        bad_az = good[-1].az
        # Same SNR, same 8-degree error; only the type prior differs.
        f = as_fiducial(
            bad_az,
            {"alt": good[-1].alt + 8.0, "snr": 8.0},
            90.0,
            Fiducial,
            uncertainty=uncertainty,
            photo_type="tree" if uncertainty and uncertainty > 2 else "structure",
        )
        fids.append(f)
        return fit(fids, _skyline, yaw_step=1.0, tilt_step=5.0, robust=False)["yaw"]

    pull_structure = abs(((solve(1.0) - truth + 180) % 360) - 180)
    pull_vegetation = abs(((solve(3.0) - truth + 180) % 360) - 180)
    assert pull_vegetation < pull_structure, (
        f"a vegetation column pulled the fit {pull_vegetation:.2f} deg and a structure "
        f"column {pull_structure:.2f} — the type prior is not reaching the fit"
    )


def test_the_two_type_sources_are_recorded_separately():
    """Photo and scope can disagree, and the disagreement is the signal.

    Merging them into one field destroys the only evidence that either is
    wrong, so `Fiducial` keeps both and neither defaults from the other.
    """
    from terminus.orient import Fiducial
    from terminus.plan import as_fiducial

    f = as_fiducial(
        10.0,
        {"alt": 20.0, "snr": 8.0},
        60.0,
        Fiducial,
        photo_type="tree",
        scope_type="structure",
    )
    assert f.photo_type == "tree" and f.scope_type == "structure"

    only_photo = as_fiducial(10.0, {"alt": 20.0, "snr": 8.0}, 60.0, Fiducial, photo_type="tree")
    assert only_photo.scope_type is None, "an absent scope type must not inherit the photo's"


def test_detection_and_movement_are_kept_as_separate_numbers():
    """SNR and type answer different questions, so they are stored separately.

    `weight` is how well the edge was DETECTED. `sigma` is how far the thing
    detected may MOVE. Folding the second into the first looks equivalent and is
    not — see `test_sigma_standardises_the_residual_before_the_robust_loss`.
    """
    from terminus.orient import Fiducial
    from terminus.plan import as_fiducial

    canopy = as_fiducial(0.0, {"alt": 20.0, "snr": 8.0}, 60.0, Fiducial, uncertainty=3.0)
    wall = as_fiducial(0.0, {"alt": 20.0, "snr": 8.0}, 60.0, Fiducial, uncertainty=1.0)
    noisy_wall = as_fiducial(0.0, {"alt": 20.0, "snr": 1.0}, 60.0, Fiducial, uncertainty=1.0)

    # Detection quality is identical for the first two; only movement differs.
    assert canopy.weight == wall.weight == pytest.approx(1.0)
    assert canopy.sigma == pytest.approx(3.0) and wall.sigma == pytest.approx(1.0)
    # And a badly detected wall is still badly detected, independently.
    assert noisy_wall.weight < wall.weight and noisy_wall.sigma == pytest.approx(1.0)


def test_sigma_standardises_the_residual_before_the_robust_loss():
    """Scaling the loss and scaling the residual are NOT the same under Huber.

    `delta` is a threshold on the residual, so a column allowed to move 3
    degrees must have its residual measured in units of that 3 degrees. The
    earlier implementation multiplied the finished loss by 1/sigma^2, which is
    inverse-variance weighting only while residuals stay inside the quadratic
    core. At r=10, sigma=3, delta=4 the two differ by 36 per cent — precisely
    the regime, a large residual on a high-sigma vegetation column, that this
    weighting exists to handle.
    """
    from terminus.orient import _huber

    r, sigma, delta = 10.0, 3.0, 4.0
    standardised = float(_huber(np.array([r / sigma]), delta)[0])
    scaled_loss = float(_huber(np.array([r]), delta)[0]) / sigma**2
    assert standardised == pytest.approx(5.5556, abs=1e-3)
    assert scaled_loss == pytest.approx(3.5556, abs=1e-3)
    assert standardised > scaled_loss, "the wrong form under-penalises a far-off column"

    # Inside the core they agree exactly, which is why this hid.
    small = 2.0
    assert float(_huber(np.array([small / sigma]), delta)[0]) == pytest.approx(
        float(_huber(np.array([small]), delta)[0]) / sigma**2
    )


def test_a_bound_column_carries_its_sigma_too():
    """Both bound paths must agree. One hard-coded weight=1.0 and skipped type.

    A column that hit its ceiling and one whose edge landed at the ceiling are
    the same kind of statement, and were getting sigmas differing by 9x
    depending only on which branch produced them.
    """
    from terminus.orient import Fiducial
    from terminus.plan import as_fiducial

    no_edge = as_fiducial(0.0, None, 60.0, Fiducial, uncertainty=3.0, photo_type="tree")
    at_ceiling = as_fiducial(
        0.0, {"alt": 60.0, "snr": 8.0}, 60.0, Fiducial, uncertainty=3.0, photo_type="tree"
    )
    assert no_edge.bound and at_ceiling.bound
    assert no_edge.sigma == at_ceiling.sigma == pytest.approx(3.0)
    assert no_edge.photo_type == at_ceiling.photo_type == "tree"


def test_zero_uncertainty_is_a_value_not_a_missing_one():
    """`if uncertainty:` read 0.0 as "unknown", silently downgrading it.

    Zero means perfectly certain. It is clamped rather than taken literally,
    because a sigma of zero is a division by zero dressed as infinite
    confidence, and nothing has earned that.
    """
    from terminus.orient import Fiducial
    from terminus.plan import as_fiducial

    certain = as_fiducial(0.0, {"alt": 20.0, "snr": 8.0}, 60.0, Fiducial, uncertainty=0.0)
    unknown = as_fiducial(0.0, {"alt": 20.0, "snr": 8.0}, 60.0, Fiducial, uncertainty=None)
    assert certain.sigma < unknown.sigma, "0 must not be read as 'no information'"
    assert certain.sigma > 0.0, "a zero sigma would divide by zero in the fit"


def test_an_untyped_column_weighs_exactly_as_before():
    """No type known must mean no change, or every existing mask shifts."""
    from terminus.orient import Fiducial
    from terminus.plan import as_fiducial

    for snr in (1.0, 4.0, 8.0, 20.0):
        f = as_fiducial(0.0, {"alt": 20.0, "snr": snr}, 60.0, Fiducial)
        assert f.weight == pytest.approx(min(1.0, snr / 8.0))


def test_the_fit_objective_standardises_rather_than_scaling():
    """Pins the order INSIDE the cost the fit actually minimises.

    The Huber property was tested in isolation, which left the fit free to use
    the wrong form: reverting `objective` to scale the finished loss by
    1/sigma^2 passed all 123 tests. The invariant has to be asserted where it is
    applied, not where it is true in the abstract.
    """
    import numpy as np

    from terminus.orient import objective

    r, sigma, delta = np.array([10.0]), np.array([3.0]), 4.0
    w = np.array([1.0])
    standardised = objective(r, w, sigma, delta=delta)
    wrong = float(__import__("terminus.orient", fromlist=["_huber"])._huber(r, delta)[0]) / 9.0

    assert standardised == pytest.approx(5.5556, abs=1e-3)
    assert wrong == pytest.approx(3.5556, abs=1e-3)
    assert standardised > wrong, "the fit is under-penalising far-off high-sigma columns"

    # A sigma of 1 must leave the cost exactly as it was before sigma existed.
    assert objective(r, w, np.array([1.0]), delta=delta) == pytest.approx(
        float(__import__("terminus.orient", fromlist=["_huber"])._huber(r, delta)[0])
    )


def test_no_column_can_buy_control_of_the_fit_with_a_tiny_sigma():
    """A sigma floor of epsilon is a numerical guard, not a physical one.

    Standardising divides by sigma, so a vanishing sigma multiplies that
    column's residual without limit and the solver will sacrifice every other
    column to satisfy it. Measured at sigma 1e-6: a half-degree residual scored
    399998 against 0.1 for the alternative. That is the failure this module's
    own docstring forbids — one bad fiducial tipping the whole sphere.

    The floor is the resolution of the data instead: the mosaic resolves about
    0.1 degree per column, so nothing may claim to be more certain than that.
    """
    import numpy as np

    from terminus.orient import Fiducial, objective
    from terminus.plan import MIN_SIGMA_DEG, _sigma

    assert _sigma(0.0) == MIN_SIGMA_DEG
    assert _sigma(1e-9) == MIN_SIGMA_DEG
    assert MIN_SIGMA_DEG >= 0.05, "a floor this low stops division by zero and nothing else"

    # Four ordinary columns against one hyper-confident one: the confident
    # column must not be able to outvote them by orders of magnitude.
    f = Fiducial(0.0, 20.0, sigma=_sigma(0.0))
    ordinary = np.array([1.0, 1.0, 1.0, 1.0])
    lone = np.array([0.5])
    cost_of_ignoring_the_lone_column = objective(lone, np.array([1.0]), np.array([f.sigma]))
    cost_of_ignoring_four = objective(ordinary, np.ones(4), np.ones(4))
    assert cost_of_ignoring_the_lone_column < 1e3 * cost_of_ignoring_four, (
        "one column can still dominate: it scores "
        f"{cost_of_ignoring_the_lone_column:.1f} against {cost_of_ignoring_four:.1f}"
    )


def test_a_second_lamp_column_is_not_mistaken_for_a_horizon():
    """Real az 350, 2026-08-03, transcribed from the saved frame filenames.

    Luminance runs 4.7-7 counts below 10 degrees, spikes to 20.7 and 20.0 at 10
    and 12.5, then falls back to 4.7-9 for the whole rest of the column to 60.

    Dark BELOW and dark ABOVE a bright band is not a horizon — a horizon is dark
    below and bright above, once. It is a streetlight.

    Two things this does NOT claim. The 10.0 degree "edge" that made these
    columns contentious came from an abandoned median-of-second-differences
    prototype, never committed; the shipped RMS estimator reports snr 1.2-1.4
    and no edge here, so `find_edge` was never fooled. And the deciding gate is
    the sky-floor test on the TOP of the column against an absolute reference,
    not `mask_lights` — disabling lamp-masking leaves this test passing, because
    the column never looks like sky at the top in the first place.

    The frames live under captures/, which is git-ignored, so these numbers
    cannot be re-derived from a clean clone. That is why they are transcribed
    literally rather than rounded.
    """
    from terminus.night import find_horizon

    prof = [
        (60, 4.7), (50, 5.0), (45, 5.0), (40, 7.0), (35, 6.0), (30, 6.0),
        (25, 7.0), (20, 6.0), (17.5, 7.0), (15, 9.0), (12.5, 20.0),
        (10, 20.7), (7.5, 4.7), (5, 5.0), (2.5, 5.0), (0, 5.0),
    ]  # fmt: skip
    alt, detail = find_horizon(prof, sky_ref=20.7)
    assert alt is None, f"reported a horizon at {alt}; this column is a lamp in the dark"
    assert "blocked" in detail["reason"], detail


def test_the_segment_backend_needs_all_three_libraries():
    """Missing ANY of torch, torchvision or transformers must report False.

    torchvision was unchecked, and it fails late: `SegformerImageProcessor`
    raises at construction, not at import, with "requires the Torchvision
    library but it was not found". So `available()` said yes and `sky_mask`
    died — a check reporting success while the thing it vouches for does not
    work, which is this project's recurring failure in a new place.

    It matters most where it is least welcome: `sky_mask(backend='auto')`
    consults this to choose, so a host with torch and no torchvision got a hard
    crash exactly where the auto path exists to fall back to the numpy
    heuristic. That is the Raspberry Pi.
    """
    for missing in ("torch", "torchvision", "transformers"):
        body = (
            "import sys, types\n"
            "class Block:\n"
            "    def find_spec(self, name, path=None, target=None):\n"
            f"        if name.split('.')[0] == {missing!r}:\n"
            "            raise ImportError('blocked for test')\n"
            "        return None\n"
            "sys.meta_path.insert(0, Block())\n"
            "for n in ('torch', 'torchvision', 'transformers'):\n"
            f"    if n != {missing!r}:\n"
            "        sys.modules[n] = types.ModuleType(n)\n"
            f"    else:\n"
            "        sys.modules.pop(n, None)\n"
            "from terminus import skymask\n"
            "print(repr(skymask.available('segment')))\n"
        )
        got = eval(_in_clean_interpreter(body))
        assert got is False, f"available('segment') said {got} with {missing} missing"

    # And the other direction, in the same test: with all three importable it
    # must say True. Without this the test passes against `return False`.
    present = (
        "import sys, types\n"
        "for n in ('torch', 'torchvision', 'transformers'):\n"
        "    sys.modules[n] = types.ModuleType(n)\n"
        "from terminus import skymask\n"
        "print(repr(skymask.available('segment')))\n"
    )
    assert eval(_in_clean_interpreter(present)) is True


def test_auto_degrades_when_the_model_cannot_be_built(tmp_path):
    """`available()` cannot promise the model will RUN, only that libs import.

    `from_pretrained` reaches for the network or a local cache and raises on a
    fresh or offline machine with all three libraries installed. Checking
    imports alone left auto crashing on precisely the host it exists to serve —
    the same bug this ticket fixes, one layer deeper.

    Patching `segment_sky` rather than `available` is deliberate: patching
    `available` proves only that `heuristic_sky` does not crash, and a mutation
    hardcoding auto to "heuristic" survived that version of this test.
    """
    from unittest.mock import patch

    import numpy as np
    import pytest
    from PIL import Image

    from terminus import skymask

    img = Image.fromarray(np.full((16, 32, 3), 120, np.uint8))
    boom = OSError("we couldn't connect to huggingface.co")

    with (
        patch.object(skymask, "available", lambda backend="segment": True),
        patch.object(skymask, "segment_sky", side_effect=boom),
    ):
        with pytest.warns(RuntimeWarning, match="falling back"):
            mask, used = skymask.sky_mask(img, backend="auto", report=True)
        assert used == "heuristic", "auto must fall back, and say which ran"
        assert mask.shape == (16, 32)

        # But an EXPLICIT request must still fail loudly rather than degrade.
        with pytest.raises(OSError):
            skymask.sky_mask(img, backend="segment")


def test_auto_reports_which_backend_actually_ran():
    """A run that fell back produces a different horizon; the caller must know."""
    from unittest.mock import patch

    import numpy as np
    from PIL import Image

    from terminus import skymask

    img = Image.fromarray(np.full((16, 32, 3), 120, np.uint8))
    with patch.object(skymask, "available", lambda backend="segment": False):
        mask, used = skymask.sky_mask(img, backend="auto", report=True)
    assert used == "heuristic"
    assert skymask.sky_mask(img, backend="auto").shape == mask.shape, "report is opt-in"


def test_a_fallback_is_recorded_rather_than_guessed_at_by_exception_type():
    """Any failure to run degrades, and the caller is told which backend ran.

    An earlier version let TypeError, AttributeError and NameError propagate, on
    the theory that those mean a bug here rather than a missing model. That does
    not survive contact: this module calls the transformers API directly, so a
    version skew — squarely environmental — surfaces as TypeError and would have
    made `auto` fatal on a dependency upgrade, breaking the one promise `auto`
    makes.

    Exception type cannot separate "the environment is short something" from "we
    have a bug". What can is recording which backend actually ran, so a silent
    downgrade becomes a stated one.
    """
    from unittest.mock import patch

    import numpy as np
    import pytest
    from PIL import Image

    from terminus import skymask

    img = Image.fromarray(np.full((16, 32, 3), 120, np.uint8))
    with patch.object(skymask, "available", lambda backend="segment": True):
        for failure in (
            OSError("we couldn't connect to huggingface.co"),
            TypeError("unexpected keyword 'reduce_labels'"),  # a version skew
            RuntimeError("model is on the wrong device"),
        ):
            with patch.object(skymask, "segment_sky", side_effect=failure):
                with pytest.warns(RuntimeWarning, match="falling back"):
                    mask, used = skymask.sky_mask(img, backend="auto", report=True)
                assert used == "heuristic", f"{type(failure).__name__} must degrade, not crash"
                assert mask.shape == (16, 32)

        # An EXPLICIT request still fails loudly, whatever the cause.
        with patch.object(skymask, "segment_sky", side_effect=TypeError("x")):
            with pytest.raises(TypeError):
                skymask.sky_mask(img, backend="segment")


def test_the_mask_records_the_backend_that_ran_not_the_one_requested(tmp_path):
    """A mask claiming `segment` that the heuristic produced is a false record.

    `cmd_skymask` resolved the backend before calling and wrote that into the
    meta, so an internal fallback would have been recorded as a segment run —
    and the segmentation-only type pass would then have run against a heuristic
    mask.
    """
    from unittest.mock import patch

    from terminus.cli import main
    from terminus.export import load_columns

    pano = tmp_path / "p.png"
    _synthetic_panorama(str(pano))
    from terminus import skymask

    with (
        patch.object(skymask, "available", lambda backend="segment": True),
        patch.object(skymask, "segment_sky", side_effect=OSError("no weights")),
    ):
        main(["skymask", str(pano), "--backend", "auto", "--az-step", "30"])

    meta, _ = load_columns(str(tmp_path / "p_mask.yaml"))
    assert meta["backend"] == "heuristic", "the mask must record what actually ran"


def test_tile_is_rejected_by_the_heuristic_rather_than_dropped():
    """Splitting the kwargs silently swallowed `tile` for the heuristic.

    Before the split it reached `heuristic_sky` and raised TypeError. Dropping it
    is worse: a caller passing `tile` believes they are tiling, and gets a
    single-pass mask with no indication otherwise.
    """
    import numpy as np
    import pytest
    from PIL import Image

    from terminus import skymask

    img = Image.fromarray(np.full((16, 32, 3), 120, np.uint8))
    with pytest.raises(TypeError, match="tile"):
        skymask.sky_mask(img, backend="heuristic", tile=True)


def test_every_mask_and_export_says_the_horizon_is_position_specific(tmp_path):
    """A mask taken from the patio does not describe the sky from the lawn.

    Reasoned from geometry, not measured here: a ridge two kilometres out does
    not care where the observer stands, but a fence five metres away swings by
    degrees for a couple of metres of displacement. The error is largest exactly
    where the horizon is highest, because the tall obstructions are the near
    ones — so the warning matters most where it is most consequential.

    Stellarium's format forbids comments, so it cannot carry the note; that is a
    limit of the format rather than an omission, and the mask and .hrz both say
    it.
    """
    from terminus.export import export_all, write_mask

    p = tmp_path / "m.yaml"
    write_mask(str(p), {0: (12.0, "tree"), 90: (30.5, "structure")}, [], {"lat": 40})
    assert "POSITION-SPECIFIC" in p.read_text()

    hrz, txt = export_all(str(p), str(tmp_path / "out"))
    hrz_text = open(hrz).read()
    assert "Measured from one spot" in hrz_text
    assert "nearer" in hrz_text.lower(), "the .hrz must name the direction that matters"

    # Stellarium stays comment-free, which the format requires.
    assert all(not ln.startswith("#") for ln in open(txt).read().splitlines())


# ---- sweep --azimuths (terminus-5) -----------------------------------------
def test_run_sweep_scans_an_explicit_list_not_a_grid():
    """The planner produces interesting columns, not a range.

    Re-measuring the handful that came back unresolved should not cost a whole
    circle, which is the only thing a uniform az_start/az_end walk can do.
    """
    from unittest.mock import MagicMock, patch

    import numpy as np

    from terminus.sweep import Pointer, Sky, run_sweep

    sky = Sky(39.7917, -104.894, 1600)
    sc = MagicMock()
    sc.equ_coord.return_value = (12.0, 20.0)
    sc.capture_rgb.return_value = np.full((8, 8, 3), 120.0, dtype=np.float32)
    cfg = {
        "sun_cone_deg": 30, "slew_step_deg": 5, "az_step": 30, "alt_min": 0,
        "alt_max": 60, "alt_tol": 2.5, "clear_thresh": 0.6,
    }  # fmt: skip
    seen = []

    def fake_scan(ptr, sc_, az, *a, **k):
        seen.append(az)
        return 20.0, "edge", "tree", []

    with (
        patch.object(Sky, "sun", lambda self: (297.0, -20.0)),
        patch.object(Pointer, "point_to", lambda self, az, alt: (az, alt)),
        patch("terminus.sweep.column_touches_sun", lambda *a, **k: False),
        patch("terminus.sweep.scan_horizon", fake_scan),
    ):
        mask, _, _ = run_sweep(
            sc, sky, cfg, dry=False, log=lambda *a, **k: None, azimuths=[55, 57, 82, 172]
        )
    assert seen == [55, 57, 82, 172], f"scanned {seen}"
    assert sorted(mask) == [55, 57, 82, 172]


def test_an_explicit_sweep_merges_rather_than_replaces(tmp_path, monkeypatch):
    """Re-measuring four columns must not discard the thirty already good.

    Replacing would make a targeted re-measure strictly worse than not running
    it — the mask would come back shorter than it went in.
    """
    from types import SimpleNamespace
    from unittest.mock import MagicMock, patch

    from terminus import cli
    from terminus.export import load_columns, write_mask

    out = tmp_path / "h.yaml"
    # lat/lon as a real sweep writes them: `default_meta` always stamps the site,
    # and a prior mask without one is now refused rather than merged blind.
    write_mask(
        str(out),
        {0: (10.0, "tree"), 90: (20.0, "structure"), 180: (5.0, "tree")},
        [],
        {"lat": 39.79, "lon": -104.89},
    )

    sc = MagicMock()
    sc.is_eq_mode.return_value = True
    cfg = {
        "site": {"lat": 39.79, "lon": -104.89},
        "sweep": {"az_step": 5, "alt_min": 0, "alt_max": 60, "sun_cone_deg": 30,
                  "clear_thresh": 0.6},
    }  # fmt: skip
    args = SimpleNamespace(
        dry_run=True, out=str(out), frames=None, az_start=0, az_end=350,
        no_export=True, azimuths="90,270",
    )  # fmt: skip

    with patch.object(
        cli, "run_sweep", return_value=({90: (33.0, "tree"), 270: (7.0, "tree")}, [], {})
    ):
        cli.cmd_sweep(sc, cfg, args)

    _, cols = load_columns(str(out))
    assert sorted(cols) == [0, 90, 180, 270], "columns not re-measured must survive"
    assert cols[90]["alt"] == 33.0, "a re-measured column must be replaced by the new value"
    assert cols[0]["alt"] == 10.0 and cols[180]["alt"] == 5.0, "untouched columns preserved"


def test_a_full_sweep_still_replaces(tmp_path):
    """Merging is for --azimuths only. A full circle is a fresh measurement."""
    from types import SimpleNamespace
    from unittest.mock import MagicMock, patch

    from terminus import cli
    from terminus.export import load_columns, write_mask

    out = tmp_path / "h.yaml"
    write_mask(str(out), {0: (10.0, "tree"), 90: (20.0, "structure")}, [], {})
    sc = MagicMock()
    sc.is_eq_mode.return_value = True
    cfg = {
        "site": {"lat": 39.79, "lon": -104.89},
        "sweep": {"az_step": 5, "alt_min": 0, "alt_max": 60, "sun_cone_deg": 30,
                  "clear_thresh": 0.6},
    }  # fmt: skip
    args = SimpleNamespace(
        dry_run=True, out=str(out), frames=None, az_start=0, az_end=350,
        no_export=True, azimuths=None,
    )  # fmt: skip
    with patch.object(cli, "run_sweep", return_value=({45: (12.0, "tree")}, [], {})):
        cli.cmd_sweep(sc, cfg, args)
    _, cols = load_columns(str(out))
    assert sorted(cols) == [45], "a full sweep writes what it measured"


def _sweep_args(out, azimuths=None):
    from types import SimpleNamespace

    return SimpleNamespace(
        dry_run=True, out=str(out), frames=None, az_start=0, az_end=350,
        no_export=True, azimuths=azimuths,
    )  # fmt: skip


_SWEEP_CFG = {
    "site": {"lat": 39.79, "lon": -104.89},
    "sweep": {"az_step": 5, "alt_min": 0, "alt_max": 60, "sun_cone_deg": 30, "clear_thresh": 0.6},
}


def test_refuses_to_merge_scope_columns_into_an_unoriented_mask(tmp_path):
    """Two coordinate frames in one file, with nothing to say which is which.

    A photo mask is in the panorama's own azimuth until the orientation is
    solved. Merging true-north scope columns into it leaves some columns true
    and some not, under a header claiming one frame for all of them.
    """
    from unittest.mock import MagicMock

    import pytest

    from terminus import cli
    from terminus.export import MaskError, write_mask

    out = tmp_path / "photo.yaml"
    write_mask(str(out), {0: (10.0, "tree")}, [], {"oriented": False, "lat": 39.79, "lon": -104.89})
    sc = MagicMock()
    sc.is_eq_mode.return_value = True
    with pytest.raises(MaskError, match="UNORIENTED"):
        cli.cmd_sweep(sc, _SWEEP_CFG, _sweep_args(out, "90,270"))


def test_refuses_to_merge_across_a_move(tmp_path):
    """The horizon belongs to one spot, and terminus-44 measured how sharply.

    A 2 m fence at 5 m shifts 11.9 degrees for 2 m of observer displacement, so
    merging a run from the far side of the garden mixes two different horizons.
    """
    from unittest.mock import MagicMock

    import pytest

    from terminus import cli
    from terminus.export import MaskError, write_mask

    out = tmp_path / "elsewhere.yaml"
    write_mask(str(out), {0: (10.0, "tree")}, [], {"lat": 39.80, "lon": -104.89})
    sc = MagicMock()
    sc.is_eq_mode.return_value = True
    with pytest.raises(MaskError, match="one position"):
        cli.cmd_sweep(sc, _SWEEP_CFG, _sweep_args(out, "90"))


def test_a_patch_does_not_erase_the_record_of_skipped_columns(tmp_path):
    """`dict.update` let a two-column patch describe the whole file.

    A base sweep that skipped 190 and 195 for the Sun, patched by a run that
    skipped nothing, had `skipped_az` overwritten to [] — while 190 and 195
    stayed absent from the mask. The columns were still missing and the reason
    was gone.
    """
    from unittest.mock import MagicMock, patch

    from terminus import cli
    from terminus.export import load_columns, write_mask

    out = tmp_path / "h.yaml"
    write_mask(
        str(out),
        {0: (10.0, "tree"), 90: (20.0, "structure")},
        [190, 195],
        {"lat": 39.79, "lon": -104.89, "measured": "2026-08-01 21:00", "skipped_az": [190, 195]},
    )
    sc = MagicMock()
    sc.is_eq_mode.return_value = True
    with patch.object(cli, "run_sweep", return_value=({90: (33.0, "tree")}, [], {})):
        cli.cmd_sweep(sc, _SWEEP_CFG, _sweep_args(out, "90"))

    meta, cols = load_columns(str(out))
    assert meta["skipped_az"] == [190, 195], "the reason those columns are missing must survive"
    assert meta["measured"] == "2026-08-01 21:00", "most columns are still from the original run"
    assert meta["patched"], "and the patch must be recorded rather than hidden"
    assert 90 in meta["patched_columns"]
    assert cols[90]["alt"] == 33.0


def test_a_measured_column_leaves_the_skipped_list(tmp_path):
    """Re-measuring a previously skipped column must clear it from the record."""
    from unittest.mock import MagicMock, patch

    from terminus import cli
    from terminus.export import load_columns, write_mask

    out = tmp_path / "h.yaml"
    write_mask(str(out), {0: (10.0, "tree")}, [190], {"lat": 39.79, "lon": -104.89,
                                                      "skipped_az": [190]})  # fmt: skip
    sc = MagicMock()
    sc.is_eq_mode.return_value = True
    with patch.object(cli, "run_sweep", return_value=({190: (12.0, "tree")}, [], {})):
        cli.cmd_sweep(sc, _SWEEP_CFG, _sweep_args(out, "190"))
    meta, cols = load_columns(str(out))
    assert meta["skipped_az"] == [], "190 was measured this time; it is no longer skipped"
    assert cols[190]["alt"] == 12.0


def test_duplicate_azimuths_are_measured_once():
    """370 and 10 are the same column; asking for both must not scan twice."""
    from unittest.mock import MagicMock, patch

    import numpy as np

    from terminus.sweep import Pointer, Sky, run_sweep

    sky = Sky(39.7917, -104.894, 1600)
    sc = MagicMock()
    sc.equ_coord.return_value = (12.0, 20.0)
    sc.capture_rgb.return_value = np.full((8, 8, 3), 120.0, dtype=np.float32)
    cfg = {
        "sun_cone_deg": 30, "slew_step_deg": 5, "az_step": 30, "alt_min": 0,
        "alt_max": 60, "alt_tol": 2.5, "clear_thresh": 0.6,
    }  # fmt: skip
    seen = []

    def fake_scan(ptr, sc_, az, *a, **k):
        seen.append(az)
        return 20.0, "edge", "tree", []

    with (
        patch.object(Sky, "sun", lambda self: (297.0, -20.0)),
        patch.object(Pointer, "point_to", lambda self, az, alt: (az, alt)),
        patch("terminus.sweep.column_touches_sun", lambda *a, **k: False),
        patch("terminus.sweep.scan_horizon", fake_scan),
    ):
        run_sweep(sc, sky, cfg, dry=False, log=lambda *a, **k: None, azimuths=[10, 370, 10.4, -350])
    assert seen == [10], f"expected one scan of az 10, got {seen}"


def test_the_move_threshold_is_the_same_distance_in_every_direction():
    """Comparing a DEGREE threshold to both lat and lon is not one distance.

    A degree of longitude shrinks by cos(lat), so at 39.8 the same 0.0003 deg
    is 33 m north-south and 26 m east-west — the east-west test was 23 per cent
    tighter than intended, which is not what anyone means by "the same spot".
    """
    import math

    from terminus.cli import MERGE_POSITION_TOLERANCE_M as tol

    lat = 39.79
    # A displacement just inside the tolerance must pass in BOTH directions,
    # and one just outside must fail in both.
    north_ok = (tol * 0.9) / 111320.0
    east_ok = (tol * 0.9) / (111320.0 * math.cos(math.radians(lat)))
    north_bad = (tol * 1.5) / 111320.0
    east_bad = (tol * 1.5) / (111320.0 * math.cos(math.radians(lat)))
    # East and north offsets of the same metre distance differ in degrees...
    assert east_ok > north_ok, "longitude degrees are shorter at this latitude"
    # ...and the guard must treat them as the same distance.
    for dlat, dlon, should_pass in (
        (north_ok, 0.0, True),
        (0.0, east_ok, True),
        (north_bad, 0.0, False),
        (0.0, east_bad, False),
    ):
        d = math.hypot(dlat * 111320.0, dlon * 111320.0 * math.cos(math.radians(lat)))
        assert (d <= tol) is should_pass, f"{d:.1f} m classified wrongly"


def test_refuses_to_merge_into_a_mask_that_never_said_where_it_stood(tmp_path):
    """An absent position is not a matching position.

    The position guard only fired when the prior mask carried lat/lon, so a mask
    without them merged silently — and the merged file was positionless too, so
    every later patch skipped the check as well. The hole propagated itself.
    """
    from unittest.mock import MagicMock

    import pytest

    from terminus import cli
    from terminus.export import MaskError, write_mask

    out = tmp_path / "nowhere.yaml"
    write_mask(str(out), {0: (10.0, "tree")}, [], {"oriented": True})
    sc = MagicMock()
    sc.is_eq_mode.return_value = True
    with pytest.raises(MaskError, match="no lat/lon"):
        cli.cmd_sweep(sc, _SWEEP_CFG, _sweep_args(out, "90"))


def test_a_hand_edited_skipped_az_fails_loudly_instead_of_scrambling():
    """`skipped_az: "190"` iterates to the characters 1, 9, 0.

    Three wrong columns recorded as skipped, no error. A bare `190` is a fair
    shorthand and is accepted; a string is the case that must be refused,
    because the silent answer is wrong rather than absent.
    """
    import pytest

    from terminus import cli
    from terminus.export import MaskError

    assert cli._az_list(190, "skipped_az") == {190}
    assert cli._az_list([190, 195], "skipped_az") == {190, 195}
    assert cli._az_list(None, "skipped_az") == set()
    with pytest.raises(MaskError, match="list of azimuths"):
        cli._az_list("190", "skipped_az")
    with pytest.raises(MaskError, match="non-numeric"):
        cli._az_list(["north"], "skipped_az")
    # And the merge path routes through it rather than round the side.
    meta = cli._merge_meta(
        {"skipped_az": 190, "measured": "2026-08-01"}, {"measured": "2026-08-04"}, [90], []
    )
    assert meta["skipped_az"] == [190], "a bare int is one column, not three digits"


def test_a_column_skipped_this_time_does_not_erase_what_was_measured_before():
    """The meta must not contradict the data it describes.

    Measure az 190 in one patch; re-request it in the next and have the Sun
    block it. Subtracting only THIS run's measurements put 190 back on
    `skipped_az` while `horizon:` still carried its altitude from before — the
    header said the column was never measured while the file held the number.
    """
    from terminus import cli

    prior = {"skipped_az": [], "measured": "2026-08-01"}
    # Patch 2: nothing measured, 190 skipped, but 190 is already in the mask.
    meta = cli._merge_meta(
        prior,
        {"measured": "2026-08-04"},
        measured=set(),
        skipped=[190],
        present={0, 90, 190},
    )
    assert meta["skipped_az"] == [], "a column the mask holds is not a skipped column"
    # A genuinely absent column still records the skip and the reason for it.
    meta = cli._merge_meta(
        prior, {"measured": "2026-08-04"}, measured=set(), skipped=[195], present={0, 90}
    )
    assert meta["skipped_az"] == [195]


def test_the_move_check_wraps_around_the_antimeridian():
    """Two metres apart at 179.9999 and -179.9999, not most of the way round.

    An unwrapped subtraction makes that 360 degrees of longitude, which at any
    latitude is tens of thousands of kilometres, so the guard refuses a merge
    for someone whose only mistake was where they live.
    """
    from unittest.mock import MagicMock

    from terminus import cli

    sky = MagicMock()
    sky.loc.lat.deg = -16.5
    sky.loc.lon.deg = -179.9999
    # Raises MaskError if the wrap is missing; the point is that it does not.
    cli._check_mergeable({"oriented": True, "lat": -16.5, "lon": 179.9999}, sky)


def test_the_scope_does_not_name_an_obstruction_it_cannot_see():
    """After sunset every silhouette is neutral, so colour typing is not evidence.

    `classify` calls a pixel vegetation only when it is green-dominant. A night
    silhouette fails that test and falls into `structure = obstr & ~veg` by
    default, so EVERY column came back "structure" — stated with exactly the
    confidence of a real daylight reading, and it was that label the residual
    table showed while the imagery said six of nine were trees.
    """
    from terminus.sweep import obstruction_type

    # Daylight: the hint is real and is kept.
    assert obstruction_type(0.6, 0.1, sun_alt=25.0) == "tree"
    assert obstruction_type(0.1, 0.6, sun_alt=25.0) == "structure"
    # After sunset the same neutral frame must not be named.
    assert obstruction_type(0.0, 0.7, sun_alt=-8.0) == ""
    assert obstruction_type(0.0, 0.7, sun_alt=-0.5) == ""
    # "open" is a statement about how much obstruction there is, not about what
    # kind, so darkness does not invalidate it.
    assert obstruction_type(0.01, 0.02, sun_alt=-8.0) == "open"
    # An unstated Sun keeps the old behaviour for a hand-run daylight probe.
    assert obstruction_type(0.0, 0.7) == "structure"


def test_a_merged_mask_says_which_instrument_named_each_column(tmp_path):
    """One `type` field, two instruments, and they are not equally able.

    The photo segments in focus and returns a real semantic class; the scope
    reads colour through a 250mm lens focused at infinity. Merging is now
    routine, so a reader has to be able to tell one column's provenance from
    another's rather than trusting the file's header for all of them.
    """
    from terminus.export import load_columns, write_mask

    out = str(tmp_path / "src.yaml")
    write_mask(
        out,
        {
            0: {"alt": 12.0, "type": "tree", "type_source": "photo"},
            90: {"alt": 30.0, "type": "structure", "type_source": "scope"},
            180: {"alt": 8.0, "type": "", "type_source": None},
        },
        [],
        {"lat": 40, "lon": -104},
    )
    _, cols = load_columns(out)
    assert cols[0]["type_source"] == "photo"
    assert cols[90]["type_source"] == "scope"
    assert cols[180].get("type_source") is None, "no type means no source to claim"
    header = [ln for ln in (tmp_path / "src.yaml").read_text().splitlines() if ln.startswith("#")]
    assert any("type_source" in ln for ln in header), "and the file explains what it means"


def test_an_unnamed_column_gets_no_boundary_model():
    """Defaulting an unknown type to `structure` is the same bug, one layer down.

    A tree measured at night has no type, and quietly judging it by the rule for
    a wall is exactly the case that matters most — the vegetation model exists
    because foliage is wide, gappy and moves. Nothing calls `boundary_model`
    yet, so refusing costs nothing today and forces the decision at the moment
    someone wires it up.
    """
    import pytest

    from terminus.sweep import boundary_model, judge_width

    assert boundary_model("tree")["repeats"] == 4
    assert boundary_model("structure")["repeats"] == 2
    # A misspelling is a typo, not an absence of evidence, so it still falls back.
    assert boundary_model("treee") is boundary_model("structure")
    for empty in ("", "   ", None):
        with pytest.raises(ValueError, match="no obstruction type"):
            boundary_model(empty)
    # judge_width must not blow up on the same input; it has an honest answer.
    verdict, note = judge_width("", 5.0)
    assert verdict == "unknown" and "no type" in note


def test_a_failed_sun_read_costs_one_reading_not_the_whole_sweep(monkeypatch):
    """The new per-column `sky.sun()` sat inside a try that does not catch it.

    That try catches SunGuard and PointingError only, so an OSError from the
    ephemeris would escape cmd_sweep's abort handler and take the entire
    in-memory mask with it — a column that had already cleared the Sun-cone gate
    killing a sweep that was otherwise fine. And the fallback must be the LAST
    KNOWN altitude, never None: None means "the caller did not say", which
    restores the daylight assumption at exactly the wrong moment.
    """
    from terminus import sweep

    calls = {"n": 0}
    real_alt = -9.0

    def flaky_sun():
        calls["n"] += 1
        if calls["n"] > 2:
            raise OSError("ephemeris unavailable")
        return 180.0, real_alt

    sky = type("S", (), {"sun": staticmethod(flaky_sun)})()
    seen = []

    def fake_scan(ptr, sc, az, *a, **kw):
        seen.append(kw["sun_alt"])
        return 20.0, "edge", "", [(20.0, 5.0)]

    monkeypatch.setattr(sweep, "scan_horizon", fake_scan)
    monkeypatch.setattr(sweep, "column_touches_sun", lambda *a, **k: False)
    monkeypatch.setattr(sweep, "Pointer", lambda *a, **k: object())
    cfg = {
        "sun_cone_deg": 30, "slew_step_deg": 5, "az_step": 90, "alt_min": 0,
        "alt_max": 60, "alt_tol": 0.5,
    }  # fmt: skip
    mask, _, _ = sweep.run_sweep(
        None, sky, cfg, az_start=0, az_end=270, dry=True, log=lambda *a, **k: None
    )

    assert len(mask) == 4, "a failed Sun read must not abandon the sweep"
    assert all(v == real_alt for v in seen), (
        "after the ephemeris failed, the last known altitude must be reused — "
        f"None would restore the daylight assumption, got {seen}"
    )


def test_a_heuristic_mask_claims_no_photo_type_it_never_segmented(tmp_path):
    """The regression the code's own comment warns about, with a test behind it.

    The heuristic backend segments nothing, so every class comes back -1 and
    every type is empty. Stamping `type_source: photo` unconditionally would
    have claimed a segmentation that never ran — an unevidenced provenance,
    which is worse than none, because a reader trusts the field precisely to
    tell photo columns apart from scope ones.
    """
    from terminus.cli import main
    from terminus.export import load_columns

    pano = tmp_path / "p.png"
    _synthetic_panorama(str(pano))
    main(["skymask", str(pano), "--backend", "heuristic", "--az-step", "30"])

    _, cols = load_columns(str(tmp_path / "p_mask.yaml"))
    assert cols, "the fixture must produce columns for this to mean anything"
    for az, col in cols.items():
        assert col["type"] == "", f"az {az}: the heuristic backend names nothing"
        assert (
            col.get("type_source") is None
        ), f"az {az}: no type was measured, so no source may be claimed"


def test_a_scope_measured_column_records_that_the_scope_named_it(tmp_path):
    """`type_source` has to be stamped where the sweep writes, not just defined.

    Dropping the stamp left every column sourceless while the field, the header
    text and the loader all still worked, so nothing failed — which is how a
    provenance field quietly becomes decorative.
    """
    from unittest.mock import MagicMock, patch

    from terminus import cli
    from terminus.export import load_columns

    out = tmp_path / "h.yaml"
    sc = MagicMock()
    sc.is_eq_mode.return_value = True
    # 90 named in daylight, 270 measured after sunset and left unnamed.
    with patch.object(
        cli, "run_sweep", return_value=({90: (33.0, "tree"), 270: (7.0, "")}, [], {})
    ):
        cli.cmd_sweep(sc, _SWEEP_CFG, _sweep_args(out))

    _, cols = load_columns(str(out))
    assert cols[90]["type"] == "tree" and cols[90]["type_source"] == "scope"
    assert cols[270]["type"] == "", "an unnamed column stays unnamed"
    assert cols[270].get("type_source") is None, "and claims no source for it"


def test_a_night_re_measure_does_not_throw_away_the_photo_s_type(tmp_path):
    """A targeted re-measure is meant to improve a column, not degrade it.

    The merge replaced the whole record, so re-measuring at 2 a.m. a column the
    photo had segmented as `tree` overwrote it with `type: ""` — the scope
    cannot name anything after sunset. A real, in-focus segmentation was thrown
    away by a measurement that had nothing to say about type, and the column
    then exported without its seasonal vegetation buffer.

    Altitude is the scope's answer and the fresh one wins. Type is whichever
    instrument could name it, so fresh silence must not overwrite it.
    """
    from unittest.mock import MagicMock, patch

    from terminus import cli
    from terminus.export import load_columns, write_mask

    out = tmp_path / "h.yaml"
    write_mask(
        str(out),
        {
            90: {
                "alt": 20.0, "type": "tree", "type_source": "photo",
                "gap_fraction": 0.4, "uncertainty": 4.2,
            },
            180: {"alt": 5.0, "type": "structure", "type_source": "photo"},
        },  # fmt: skip
        [],
        {"lat": 39.79, "lon": -104.89},
    )
    sc = MagicMock()
    sc.is_eq_mode.return_value = True
    # Re-measure 90 after sunset: a new altitude, and no type at all.
    with patch.object(cli, "run_sweep", return_value=({90: (33.0, "")}, [], {})):
        cli.cmd_sweep(sc, _SWEEP_CFG, _sweep_args(out, "90"))

    _, cols = load_columns(str(out))
    assert cols[90]["alt"] == 33.0, "the fresh altitude must win"
    assert cols[90]["type"] == "tree", "a fresh silence must not erase a real segmentation"
    assert cols[90]["type_source"] == "photo", "and the source stays with the type"
    assert cols[90]["gap_fraction"] == 0.4, "photo fields describe the panorama, not this scan"
    assert cols[180]["type"] == "structure", "untouched columns are untouched"


def test_a_daylight_re_measure_may_correct_the_type(tmp_path):
    """The other direction: a column that CAN be named replaces the old name.

    Preserving the old type unconditionally would be the mirror bug — a
    re-measure that genuinely sees a wall where the mask says tree has to be
    able to say so.
    """
    from unittest.mock import MagicMock, patch

    from terminus import cli
    from terminus.export import load_columns, write_mask

    out = tmp_path / "h.yaml"
    write_mask(
        str(out),
        {90: {"alt": 20.0, "type": "tree", "type_source": "photo"}},
        [],
        {"lat": 39.79, "lon": -104.89},
    )
    sc = MagicMock()
    sc.is_eq_mode.return_value = True
    with patch.object(cli, "run_sweep", return_value=({90: (21.0, "structure")}, [], {})):
        cli.cmd_sweep(sc, _SWEEP_CFG, _sweep_args(out, "90"))

    _, cols = load_columns(str(out))
    assert cols[90]["type"] == "structure", "a fresh naming replaces an old one"
    assert cols[90]["type_source"] == "scope", "and brings its own source with it"
