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
    """Real column az 340: blocked throughout, with a lamp at 12.5-15.

    Transcribed from captures/2026-08-04-averaged/scan/, whose frame names carry
    the measured luminance. There is a SECOND, independent run of this same
    azimuth from 2026-08-03 in the terminus-14 test below, and the two differ by
    up to 5 counts — different nights, not a disagreement. Labelled here because
    a reviewer read them as two transcriptions of one frame and was right to
    ask; two runs agreeing on the conclusion is worth more than one.
    """
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


def test_a_goto_that_doglegs_is_not_reported_failed_while_the_mount_still_moves():
    """Live failure 2026-08-07: two consecutive gotos 'failed' and both landed.

    A large arm reconfiguration doglegs: closure on the target paused for more
    than NO_PROGRESS_S mid-path while the mount kept reporting motion, the old
    rule broke out at the stall, and the mount arrived seconds later — so the
    NEXT goto's failure message blamed the position the first had just reached.
    The rule now is: a stall stops EXTENDING the deadline but only a stall with
    the mount HALTED ends the attempt early. The 2026-08-06 case the stall rule
    was built for — moving the whole time, never converging, 288 s unbounded —
    must still fail, within GOTO_TIMEOUT of its last real progress.
    """
    from unittest.mock import MagicMock, patch

    import pytest

    from terminus import sweep as sweep_mod
    from terminus.sweep import Pointer, PointingError, Sky

    class FakeTime:
        def __init__(self):
            self.t = 0.0

        def time(self):
            return self.t

        def sleep(self, dt):
            self.t += dt

    def make_pointer(ra_at, moving_at):
        sc = MagicMock()
        sc.goto = MagicMock()
        sc.equ_coord = MagicMock(side_effect=lambda: (ra_at(clock.t), 0.0))
        sc.call = MagicMock(
            side_effect=lambda m, *a, **k: {
                "result": {"mount": {"move_type": ("goto" if moving_at(clock.t) else "none")}}
            }
        )
        return Pointer(sc, Sky(39.79, -104.89, 1600), 30, 5, dry=False)

    # Dogleg: closes for 10 s, pauses 35 s (still moving), then closes and
    # lands ~55 s in — inside the deadline the closing phase earned.
    clock = FakeTime()

    def dogleg_ra(t):
        if t < 10:
            return 0.2 * t  # closing: 3 deg/s toward RA 4h
        if t < 45:
            return 2.0  # paused mid-reconfiguration
        return min(4.0, 2.0 + 0.2 * (t - 45.0))

    with patch.object(sweep_mod, "time", clock):
        ptr = make_pointer(dogleg_ra, lambda t: True)
        ptr._goto_wait(4.0, 0.0, settle=0.5)  # must NOT raise
    assert clock.t < 90, f"the dogleg must land within the un-extended deadline, took {clock.t}"

    # Halted short: stops moving 9 deg out. Fails fast at the stall.
    clock = FakeTime()
    with patch.object(sweep_mod, "time", clock):
        ptr = make_pointer(lambda t: min(3.4, 0.2 * t), lambda t: t < 20)
        with pytest.raises(PointingError, match="stopped improving"):
            ptr._goto_wait(4.0, 0.0, settle=0.5)
    assert clock.t < 60, f"a halted mount must fail fast, took {clock.t}"


def test_a_mount_that_refuses_a_sun_adjacent_goto_is_sun_blocked_not_broken():
    """Live 2026-08-07: the mount sat motionless on a goto ~35 deg from the Sun.

    Our cone (30) had cleared the target; the Seestar's own solar protection is
    evidently wider, and it refused by simply not moving. That must surface as
    SunGuard — a clean skip every loop already handles without counting a miss —
    not as PointingError, which charges the mount's good judgement toward
    MAX_POINTING_MISSES and can abort a healthy run. A never-moved goto AWAY
    from the Sun keeps the PointingError: that is the stowed-arm signature.
    """
    from unittest.mock import MagicMock, patch

    import pytest

    from terminus import sweep as sweep_mod
    from terminus.sweep import Pointer, PointingError, Sky, SunGuard

    class FakeTime:
        def __init__(self):
            self.t = 0.0

        def time(self):
            return self.t

        def sleep(self, dt):
            self.t += dt

    def frozen_pointer():
        sc = MagicMock()
        sc.equ_coord = MagicMock(return_value=(8.0, 30.0))  # never changes
        sc.call = MagicMock(return_value={"result": {"mount": {"move_type": "none"}}})
        return Pointer(sc, Sky(39.79, -104.89, 1600), 30, 5, dry=False)

    clock = FakeTime()
    with patch.object(sweep_mod, "time", clock), patch.object(Sky, "sun", lambda s: (287.0, 5.0)):
        ptr = frozen_pointer()
        sky = ptr.sky
        # A target ~35 deg from the Sun (inside cone + FIRMWARE_SUN_MARGIN).
        near = sky.altaz_to_radec(259.0, 25.0)
        with pytest.raises(SunGuard, match="solar protection"):
            ptr._goto_wait(*near, settle=0.5)
        # The margin is 10, not less: a target out at ~cone + 9 must still be
        # reclassified (round 1 found the constant unpinned at ~3.5).
        wide = sky.altaz_to_radec(259.0, 32.0)
        from terminus.sweep import FIRMWARE_SUN_MARGIN, ang_sep

        taz, talt = sky.radec_to_altaz(*wide)
        sep = ang_sep(taz, talt, 287.0, 5.0)
        assert (
            30.0 + FIRMWARE_SUN_MARGIN - 3.0 < sep < 30.0 + FIRMWARE_SUN_MARGIN
        ), f"fixture must sit just inside the margin band, got {sep:.1f}"
        with pytest.raises(SunGuard, match="solar protection"):
            ptr._goto_wait(*wide, settle=0.5)
        # THE RECLASSIFICATION IS CAPPED (round 1's P2): a frozen mount shares
        # the motionless signature, and near noon the band covers most of the
        # sky. The third consecutive refusal with no motion in between is a
        # mount fault again, so the stuck-mount abort can see it.
        with pytest.raises(PointingError, match="consecutive"):
            ptr._goto_wait(*near, settle=0.5)

    # AND THE CAP RESETS ON REAL MOTION (round 2): a healthy mount that
    # legitimately gets Sun-refused three times across a long session must not
    # be branded stuck. After an ARRIVING goto, a fresh near-Sun refusal is a
    # clean SunGuard again, not an instant PointingError.
    clock = FakeTime()
    with patch.object(sweep_mod, "time", clock), patch.object(Sky, "sun", lambda s: (287.0, 5.0)):
        arriving = MagicMock()
        state = {"ra": 8.0}
        arriving.equ_coord = MagicMock(side_effect=lambda: (state["ra"], 0.0))
        moving = {"on": False}
        arriving.call = MagicMock(
            side_effect=lambda m, *a, **k: {
                "result": {"mount": {"move_type": ("goto" if moving["on"] else "none")}}
            }
        )
        ptr = Pointer(arriving, Sky(39.79, -104.89, 1600), 30, 5, dry=False)
        near = ptr.sky.altaz_to_radec(259.0, 25.0)
        for _ in range(2):
            with pytest.raises(SunGuard):
                ptr._goto_wait(*near, settle=0.1)
        # a goto that ARRIVES: point at the current position
        ptr._goto_wait(8.0, 0.0, settle=0.1)
        with pytest.raises(SunGuard, match="solar protection"):
            # reset by the arrival: refusal number three counts as one again
            ptr._goto_wait(*near, settle=0.1)

    clock = FakeTime()
    with patch.object(sweep_mod, "time", clock), patch.object(Sky, "sun", lambda s: (287.0, 5.0)):
        ptr = frozen_pointer()
        far = ptr.sky.altaz_to_radec(90.0, 25.0)  # nowhere near the Sun
        with pytest.raises(PointingError, match="never moved"):
            ptr._goto_wait(*far, settle=0.5)

    # With the Sun harmlessly below SUN_SAFE_ALT, a frozen mount is a broken
    # mount whatever direction the target is (SAFE-07 in reverse).
    clock = FakeTime()
    with patch.object(sweep_mod, "time", clock), patch.object(Sky, "sun", lambda s: (287.0, -10.0)):
        ptr = frozen_pointer()
        near = ptr.sky.altaz_to_radec(259.0, 25.0)
        with pytest.raises(PointingError, match="never moved"):
            ptr._goto_wait(*near, settle=0.5)


# ---- review round 1 regressions -------------------------------------------
def test_a_later_leg_rechecks_the_sun_instead_of_trusting_the_plan():
    """A multi-leg route must not fire a later leg on a stale clearance.

    GOTO_TIMEOUT is 90s and extends while the mount still reports motion, so one
    leg can run for minutes. The clearance was computed before the route started;
    by the time a later leg fires the Sun has moved. It must be recomputed
    against where the mount ACTUALLY landed, not where it was sent.

    Rewritten when routes became explicit. The check it guards is the same, and
    matters more now: a planned route has more legs than the old two-hop
    waypoint, so there are more moments at which the plan can go stale.
    """
    from unittest.mock import MagicMock, patch

    import pytest

    from terminus.sweep import Pointer, Sky, SunGuard

    sky = Sky(39.7917, -104.894, 1600)
    sc = MagicMock()
    sc.equ_coord.return_value = (12.0, 20.0)
    sc.capture_rgb.return_value = np.full((8, 8, 3), 120.0, dtype=np.float32)
    ptr = Pointer(sc, sky, 30, 5)

    legs = []

    def fake_goto(ra, dec, settle):
        legs.append((ra, dec))

    # Planning sees every candidate route as clear; then the first leg lands and
    # the remaining route is no longer clear. Keyed on whether a leg has actually
    # been driven, NOT on a count of calls — counting broke the moment the
    # planner gained more candidate routes to evaluate, which is a thing it
    # should be free to do.
    def flaky_route_sep(self, rd0, waypoints, samples=None):
        return 1.0 if legs else 90.0

    with (
        patch.object(Pointer, "_goto_wait", side_effect=fake_goto),
        patch.object(Pointer, "_sun_check", lambda self, az, alt: None),
        patch.object(Pointer, "current_azalt", lambda self: (100.0, 40.0)),
        patch.object(Pointer, "path_min_sep", lambda self, a, b: 1.0),
        patch.object(Pointer, "route_min_sep", flaky_route_sep),
    ):
        with pytest.raises(SunGuard, match="Sun has moved"):
            ptr.point_to(200.0, 30.0)

    assert legs, "the route must have started before it was abandoned"
    assert len(legs) < 4, f"it must stop as soon as the plan goes stale, drove {len(legs)}"


def test_a_route_is_driven_leg_by_leg_not_handed_over_as_one_goto():
    """The whole reason a blocked shape no longer blocks the target.

    The old code gave the mount ONE goto and had no say in the route, so it had
    to assume the worst of three shapes and refuse if any grazed the Sun. A tube
    parked in the west could not be moved anywhere: hauling declination up at its
    own RA passed 18.7 degrees from the Sun, and that hypothetical leg vetoed
    every target regardless of where the target was.

    Every leg of a planned route changes ONE axis, which the mount can perform in
    exactly one way — so the route that was checked is the route that happens.
    """
    from unittest.mock import MagicMock, patch

    from terminus.sweep import Pointer, Sky

    sky = Sky(39.7917, -104.894, 1600)
    sc = MagicMock()
    sc.equ_coord.return_value = (12.0, 20.0)
    ptr = Pointer(sc, sky, 30, 5)

    legs = []
    with (
        patch.object(Pointer, "_goto_wait", side_effect=lambda ra, dec, s: legs.append((ra, dec))),
        patch.object(Pointer, "_sun_check", lambda self, az, alt: None),
        patch.object(Pointer, "current_azalt", lambda self: (100.0, 40.0)),
        patch.object(Pointer, "path_min_sep", lambda self, a, b: 1.0),  # direct is blocked
        patch.object(Pointer, "route_min_sep", lambda self, a, w, samples=None: 90.0),
    ):
        ptr.point_to(200.0, 30.0)

    assert len(legs) >= 2, "a blocked direct path must be driven as an explicit route"
    # Each leg moves one axis only, which is what makes the route unambiguous.
    prev = (12.0, 20.0)
    for ra, dec in legs:
        moved = (abs(ra - prev[0]) > 1e-9, abs(dec - prev[1]) > 1e-9)
        assert sum(moved) <= 1, f"leg {(ra, dec)} moves both axes, so its route is not determined"
        prev = (ra, dec)


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
    # 25.0, not 15.0. The convention changed deliberately: the reported altitude
    # is the LAST CLEAR SKY sample, not the first terrain one, because the mask
    # defines altitude as "lowest clear sky" and a planner takes it literally.
    # The step-back also has to skip the LAMP at 20 — reporting a streetlight
    # burning inside the obstruction as the horizon would be worse than either.
    alt, detail = find_horizon(prof, sky_ref=22.0)
    assert alt == 25.0, f"expected the last clear sky above the lamp, got {alt} ({detail})"
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
        patch.object(
            Sky, "sun", lambda self: (297.0, -5.0)
        ),  # dusk: guard stands down, day channel
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
        patch.object(Sky, "sun", lambda self: (297.0, -5.0)),
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
        patch.object(Sky, "sun", lambda self: (297.0, -5.0)),
        patch.object(Pointer, "point_to", point_to),
        patch("terminus.sweep.column_touches_sun", lambda *a, **k: False),
    ):
        mask, _, _ = run_sweep(
            sc, sky, cfg, az_start=0, az_end=300, dry=False, log=lambda *a, **k: None
        )
    assert mask, "a failed sky reference must not abort the sweep before it starts"


def test_the_departure_threshold_sits_between_model_error_and_real_terrain():
    """Retired MIN_DROP_FRAC as the detector, and this test's old premise with it.

    It used to assert that a drop to 0.55x the modelled sky is "still sky", which
    pinned MIN_DROP_FRAC at a factor of two. Two real columns then landed astride
    that line and neither survived it: az 60 cleared it by 0.004 and was thrown
    away by the persistence check, and az 190 — a roofline visible by eye, 53.6
    sigma clear of the sky's own scatter — missed it by 0.019 and was never seen.
    A rule that a genuine roofline fails by one part in fifty is not measuring
    what it claims to.

    So the threshold moved to a quarter, which is not a taste: skyglow departs
    from a straight line by perhaps ten to twenty per cent over a full column,
    and the az 190 roofline is forty-eight per cent down. A quarter separates the
    two with room on both sides. The consequence, stated plainly because it is a
    real behaviour change: a 45 per cent drop now reads as terrain where it used
    to read as sky. That is the conservative direction — the error it prevents is
    a planner starting a run into an obstruction — but it is a change, and if a
    real hazy column turns out to sit there, this is the number to revisit.
    """
    from terminus.night import find_horizon

    sky = lambda a: 50.0 - 0.5 * a  # noqa: E731  measured skyglow gradient
    alts = [60, 50, 45, 40, 35, 30, 25, 20, 15, 10, 5, 0]

    def column(frac):
        return [(a, sky(a)) for a in alts if a >= 25] + [(a, sky(a) * frac) for a in alts if a < 25]

    # The reported altitude is the last CLEAR sky sample, so 25 rather than 20.
    for frac in (0.45, 0.55, 0.70):
        alt, detail = find_horizon(column(frac), sky_ref=22.0)
        assert alt == 25.0, f"a sustained drop to {frac}x is terrain, got {alt} ({detail})"

    # A shallow, sustained dip is model error, not an obstruction. This is the
    # half that keeps the threshold honest in the other direction.
    alt, detail = find_horizon(column(0.85), sky_ref=22.0)
    assert alt is None, f"a 15 per cent dip is within model error, got {alt}"

    # And a single dark frame is not a horizon: the persistence check requires
    # the column to STAY down.
    spike = [(a, sky(a)) for a in alts]
    spike[6] = (25, sky(25) * 0.3)
    alt, detail = find_horizon(spike, sky_ref=22.0)
    assert alt is None, f"one dark sample is not a roofline, got {alt}"


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
        patch.object(Sky, "sun", lambda self: (297.0, -5.0)),
        patch.object(Pointer, "point_to", lambda self, az, alt: (az, alt)),
        patch("terminus.sweep.column_touches_sun", lambda *a, **k: False),
        patch("terminus.sweep.scan_horizon", fake_scan),
    ):
        mask, _, _ = run_sweep(
            sc, sky, cfg, dry=False, log=lambda *a, **k: None, azimuths=[55, 57, 82, 172]
        )
    assert seen == [55, 57, 82, 172], f"scanned {seen}"
    assert sorted(mask) == [55, 57, 82, 172]


def test_the_sweep_measures_with_the_night_eye_when_the_sun_is_down():
    """I-15 wired into the sweep: after dark the scenery stream is blind.

    Before this, `run_sweep` measured every column with the day detector on the
    scenery stream whatever the clock said, and a sweep run after dark returned
    confident darkness rather than measurements (S-04's direction). Now the
    channel follows the Sun: below NIGHT_SUN_ALT the star-mode imaging channel
    and `scan_horizon_night` carry the column, and the profile is recorded WITH
    its channel so a replay cannot judge raw16 medians with the RGB judge (F-13).
    """
    from unittest.mock import MagicMock, patch

    from terminus.sweep import Pointer, Sky, run_sweep

    sky = Sky(39.7917, -104.894, 1600)
    sc = MagicMock()
    cfg = {
        "sun_cone_deg": 30, "slew_step_deg": 5, "az_step": 30, "alt_min": 0,
        "alt_max": 60, "alt_tol": 2.5, "clear_thresh": 0.6,
    }  # fmt: skip
    night_seen, day_seen = [], []
    prof = [(45.0, 900.0), (40.0, 880.0), (35.0, 100.0)]

    def fake_night(ptr, sc_, az, *a, **k):
        night_seen.append(az)
        return 37.5, "edge(night)", "", list(prof)

    def fake_day(ptr, sc_, az, *a, **k):
        day_seen.append(az)
        return 20.0, "edge", "tree", []

    with (
        patch.object(Sky, "sun", lambda self: (297.0, -20.0)),  # full dark
        patch.object(Pointer, "point_to", lambda self, az, alt: (az, alt)),
        patch("terminus.sweep.column_touches_sun", lambda *a, **k: False),
        patch("terminus.sweep.scan_horizon_night", fake_night),
        patch("terminus.sweep.scan_horizon", fake_day),
    ):
        mask, _, profiles = run_sweep(
            sc, sky, cfg, dry=False, log=lambda *a, **k: None, azimuths=[55, 82]
        )

    assert night_seen == [55, 82], f"night scanner must carry the columns, saw {night_seen}"
    assert day_seen == [], "the day detector must never touch a night column"
    sc.start_view.assert_called_with("star")
    sc.lock_exposure.assert_not_called()  # star mode manages its own exposure
    assert profiles[55] == {
        "channel": "star4800",
        "profile": prof,
    }, "a night profile must carry its channel, or replay judges raw16 with the RGB judge"
    assert mask[55] == {"alt": 37.5, "type": "", "bound": False}


def test_a_sweep_crossing_twilight_switches_channels_mid_run():
    """The regime is re-decided per column, because a sweep spans hours.

    A run that starts at dusk and ends in the dark must not carry the day eye
    across the boundary (M-17's confound, S-04's failure direction) — and the
    switch must happen ONCE, at the boundary, not per column.
    """
    from unittest.mock import MagicMock, patch

    import numpy as np

    from terminus.sweep import Pointer, Sky, run_sweep

    sky = Sky(39.7917, -104.894, 1600)
    sc = MagicMock()
    sc.capture_rgb.return_value = np.full((8, 8, 3), 120.0, dtype=np.float32)
    cfg = {
        "sun_cone_deg": 30, "slew_step_deg": 5, "az_step": 30, "alt_min": 0,
        "alt_max": 60, "alt_tol": 2.5, "clear_thresh": 0.6,
    }  # fmt: skip
    # One Sun reading at run start, then one per column: dusk, dusk, dark.
    suns = iter([(297.0, -8.0), (297.0, -8.0), (297.0, -13.0)])
    night_seen, day_seen = [], []

    with (
        patch.object(Sky, "sun", lambda self: next(suns)),
        patch.object(Pointer, "point_to", lambda self, az, alt: (az, alt)),
        patch("terminus.sweep.column_touches_sun", lambda *a, **k: False),
        patch(
            "terminus.sweep.scan_horizon_night",
            lambda ptr, sc_, az, *a, **k: (night_seen.append(az), (30.0, "edge(night)", "", []))[1],
        ),
        patch(
            "terminus.sweep.scan_horizon",
            lambda ptr, sc_, az, *a, **k: (day_seen.append(az), (20.0, "edge", "tree", []))[1],
        ),
    ):
        run_sweep(sc, sky, cfg, dry=False, log=lambda *a, **k: None, azimuths=[10, 20])

    assert day_seen == [10], f"the dusk column belongs to the day eye, saw {day_seen}"
    assert night_seen == [20], f"the dark column belongs to the night eye, saw {night_seen}"
    started = [c.args[0] for c in sc.start_view.call_args_list]
    assert started == ["scenery", "star"], f"one switch, at the boundary, not per column: {started}"


def test_replay_judges_a_night_column_with_the_night_judge():
    """F-13 for profiles: a raw16 night median judged by the RGB day rule is wrong.

    The night column here is a Bortle-8 shape the day judge mishandles — skyglow
    brightening toward the horizon, then a persistent unrecovered drop (M-23).
    The channel travels with the profile, so replay re-judges it with
    `night_find_edge` (D-16: judge is code, data is data) and lands on the
    coarse bracket's midpoint. A plain-list column keeps the day judge, so old
    profiles files replay unchanged.
    """
    import pytest

    from terminus import guide

    night_rows = [
        [50.0, 200.0], [45.0, 210.0], [40.0, 225.0], [35.0, 245.0],
        [30.0, 270.0], [25.0, 60.0], [20.0, 55.0], [15.0, 50.0], [10.0, 45.0],
    ]  # fmt: skip
    day_rows = [[50.0, 120.0], [45.0, 118.0], [40.0, 20.0], [35.0, 18.0]]
    measure = guide.replay(
        {"90": {"channel": "star4800", "profile": night_rows}, "100": day_rows},
        uncertainty=1.0,
    )

    got = measure(90)
    assert got is not None and got[0] is not None, "the night edge must be found"
    assert (
        got[0]["alt"] == 27.5
    ), f"the coarse bracket's midpoint, judged by night_find_edge, got {got[0]}"
    day = measure(100)
    assert (
        day is not None and day[0] is not None and "alt" in day[0]
    ), "a plain-list column must keep the day judge and still resolve"
    with pytest.raises(ValueError, match="channel"):
        guide.replay({"90": {"channel": "rtsp", "profile": night_rows}})


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
        no_export=True, azimuths=azimuths, stop_above_sun_alt=None,
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


def test_a_truncated_sweep_is_visible_in_the_file_and_the_exit_code(tmp_path):
    """End to end through cmd_sweep, on both the fresh and the merge path.

    Every existing test passes `stop_above_sun_alt=None`, so nothing drove
    `cmd_sweep` with a deadline that actually fires: deleting both the meta stamp
    and the `SystemExit(3)` left the whole suite green. That gap is why the merge
    path shipped broken — the flag was stamped onto `fresh`, and `_merge_meta`
    builds from `prior_meta` and discards `fresh`.

    Both directions matter. Losing the flag lets a truncated run read as
    complete; INHERITING it lets a mask stay flagged forever however many
    complete patches follow. A record that cannot be cleared is not a record.
    """
    from types import SimpleNamespace
    from unittest.mock import MagicMock, patch

    import pytest

    from terminus import cli
    from terminus.export import load_columns, write_mask

    def args(out, azimuths=None):
        return SimpleNamespace(
            dry_run=True, out=str(out), frames=None, az_start=0, az_end=350,
            no_export=True, azimuths=azimuths, stop_above_sun_alt=-12.0,
        )  # fmt: skip

    class Deadline:
        """Stands in for `_sun_deadline`, firing or not on demand."""

        def __init__(self, fires):
            self.fires = fires
            self.fired = False

        def __call__(self):
            if self.fires:
                self.fired = True
            return self.fires

    sc = MagicMock()
    sc.is_eq_mode.return_value = True

    def sweep(out, deadline, azimuths=None):
        def run(*a, **k):
            k["should_stop"]()  # the real loop asks; asking is what sets `fired`
            return {90: (12.0, "tree")}, [], {}

        with (
            patch.object(cli, "_sun_deadline", return_value=deadline),
            patch.object(cli, "run_sweep", side_effect=run),
        ):
            cli.cmd_sweep(sc, _SWEEP_CFG, args(out, azimuths))

    # 1. A fresh run the window closed on: exit 3, and the file says so.
    out = tmp_path / "h.yaml"
    with pytest.raises(SystemExit) as exc:
        sweep(out, Deadline(fires=True))
    assert exc.value.code == 3, "a closed window is not the same outcome as a mount fault (2)"
    meta, _ = load_columns(str(out))
    assert meta.get("stopped_early") is True, "the file outlives the terminal and must say it"

    # 2. A patch that is ALSO truncated must keep saying so after merging.
    write_mask(str(out), {0: (10.0, "tree")}, [], {"lat": 39.79, "lon": -104.89})
    with pytest.raises(SystemExit) as exc:
        sweep(out, Deadline(fires=True), azimuths="90")
    assert exc.value.code == 3
    meta, _ = load_columns(str(out))
    assert meta.get("stopped_early") is True, "merging must not discard this run's truncation"

    # 3. A complete patch over a truncated mask CLEARS the flag.
    sweep(out, Deadline(fires=False), azimuths="90")
    meta, cols = load_columns(str(out))
    assert not meta.get(
        "stopped_early"
    ), "a mask since completed must stop claiming it was cut short"
    assert 90 in cols, "and the merge still did its actual job"


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
        patch.object(Sky, "sun", lambda self: (297.0, -5.0)),
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
                "alt": 20.0,
                "type": "tree",
                "type_source": "photo",
                "gap_fraction": 0.4,
                "uncertainty": 4.2,
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


def test_a_real_crossing_clears_the_photo_s_lower_bound_flag(tmp_path):
    """`clipped` describes an altitude, not an obstruction.

    It means the obstruction ran off the top of the PHOTO, so the altitude
    beside it is a lower bound rather than a measurement. Once the scope
    supplies a real crossing, that number is no longer the bound — carrying the
    flag forward makes the file assert something false about a value it no
    longer describes.

    The contrast is `gap_fraction` and `uncertainty`, which describe how gappy
    the canopy is and how far it moves. Those are properties of the thing, and
    looking at it again from a different instrument does not change them.
    """
    from unittest.mock import MagicMock, patch

    from terminus import cli
    from terminus.export import load_columns, write_mask

    out = tmp_path / "h.yaml"
    write_mask(
        str(out),
        {
            90: {
                "alt": 60.0,
                "type": "tree",
                "type_source": "photo",
                "clipped": True,
                "gap_fraction": 0.4,
                "uncertainty": 4.2,
            }
        },  # fmt: skip
        [],
        {"lat": 39.79, "lon": -104.89},
    )
    sc = MagicMock()
    sc.is_eq_mode.return_value = True
    # The scope finds a genuine edge well below the photo's cropped bound.
    with patch.object(cli, "run_sweep", return_value=({90: (22.0, "")}, [], {})):
        cli.cmd_sweep(sc, _SWEEP_CFG, _sweep_args(out, "90"))

    _, cols = load_columns(str(out))
    assert cols[90]["alt"] == 22.0
    assert cols[90].get("clipped") is None, "22.0 is a measurement, not a lower bound"
    assert cols[90]["gap_fraction"] == 0.4, "how gappy the canopy is did not change"
    assert cols[90]["uncertainty"] == 4.2, "nor how far it moves"
    assert cols[90]["type"] == "tree", "and the photo still knows what it is"


# ---- picture exporters (terminus-1, terminus-26) --------------------------
_LS_ROWS = [(0, 10.0, "structure"), (90, 30.0, "structure"), (180, 5.0, "structure"),
            (270, 20.0, "structure")]  # fmt: skip
_LS_META = {"lat": 39.79, "lon": -104.89, "measured": "2026-08-05 21:00"}


def test_the_panorama_puts_north_at_the_left_edge_and_zenith_at_the_top():
    """Sky Safari's convention, and getting it wrong is silently plausible.

    North at the LEFT edge increasing east, zenith +90 at the TOP, horizon 0 at
    the middle, nadir -90 at the bottom. A picture with the axes flipped still
    looks like a horizon — it is just somebody else's.
    """
    from terminus.landscape import render

    rgba = render(_LS_ROWS, (360, 180), tree_buffer=0)
    alpha = rgba[..., 3]
    # Column 90 has the HIGHEST horizon, so it must have the most opaque rows;
    # column 180 the lowest, so the fewest. If x ran the other way these swap.
    assert alpha[:, 90].sum() > alpha[:, 0].sum() > alpha[:, 180].sum()
    # Zenith is transparent everywhere and nadir is opaque everywhere. If y ran
    # the other way, both flip.
    assert alpha[0].max() == 0, "nothing is opaque at the zenith"
    assert alpha[-1].min() == 255, "everything is opaque at the nadir"
    # And the boundary in a column sits at that column's measured altitude.
    ground_rows = np.nonzero(alpha[:, 90])[0]
    top_alt = 90.0 - (ground_rows[0] + 0.5) / 180 * 180.0
    assert abs(top_alt - 30.0) < 1.0, f"az 90 boundary at {top_alt}, measured 30"


def test_the_sky_is_transparent_because_alpha_is_the_horizon():
    """Neither program reads a drawn line; opacity is the whole mechanism.

    A silhouette painted onto an opaque background would import as a completely
    blocked sky, which is a plausible thing to produce and a total failure.
    """
    from terminus.landscape import render

    rgba = render(_LS_ROWS, (64, 32), tree_buffer=0)
    assert rgba[..., 3].min() == 0, "some pixel must be fully transparent"
    assert rgba[..., 3].max() == 255, "and some fully opaque"
    assert set(np.unique(rgba[..., 3])) == {0, 255}, "no partial alpha to misread"


def test_uncovered_panorama_shows_as_sky_not_as_invented_ground():
    """The gap in the data must not become black terrain.

    A phone panorama does not fill the full sphere. Where the mosaic has no
    frames the pixel is zero, and drawing it opaque presents a hole in the
    survey as a wall the viewer has no reason to doubt.
    """
    from terminus.landscape import render

    texture = np.full((32, 64, 3), 90, np.uint8)
    coverage = np.ones((32, 64))
    coverage[:, 10:20] = 0  # the phone never pointed here
    rgba = render(_LS_ROWS, (64, 32), texture=texture, coverage=coverage, tree_buffer=0)
    assert rgba[:, 10:20, 3].max() == 0, "no coverage means no ground"
    assert rgba[:, 30, 3].max() == 255, "covered columns still render ground"


def test_the_picture_and_the_numbers_are_the_same_horizon(tmp_path):
    """A landscape whose polygon disagrees with its texture is worse than neither.

    Both must go through the same buffered pairs, so the vegetation margin the
    .hrz applies is the margin the picture shows.
    """
    from terminus.export import TREE_BUFFER_DEG
    from terminus.landscape import horizon_altitudes, write_landscape

    rows = [(0, 10.0, "tree"), (90, 30.0, "structure"), (180, 5.0, "tree"), (270, 20.0, "tree")]
    write_landscape(str(tmp_path / "ls"), rows, _LS_META)
    txt = (tmp_path / "ls" / "horizon.txt").read_text()
    pairs = {float(a): float(b) for a, b in (ln.split() for ln in txt.splitlines() if ln.strip())}
    assert pairs[0.0] == 10.0 + TREE_BUFFER_DEG, "the polygon carries the vegetation margin"
    alts = horizon_altitudes(rows, 360)
    assert abs(alts[0] - pairs[0.0]) < 1e-6, "and the picture is drawn from the same numbers"


def test_a_scope_only_mask_ships_a_polygonal_landscape_not_a_fake_photo(tmp_path):
    """Without imagery there is nothing honest to put in maptex.

    Declaring type=spherical with a rendered silhouette would claim a photograph
    that does not exist.
    """
    from terminus.landscape import write_landscape

    write_landscape(str(tmp_path / "ls"), _LS_ROWS, _LS_META)
    ini = (tmp_path / "ls" / "landscape.ini").read_text()
    assert "type = polygonal" in ini
    assert "maptex" not in ini, "no texture was supplied, so none may be declared"
    assert not (tmp_path / "ls" / "maptex.png").exists()
    assert "polygonal_horizon_list = horizon.txt" in ini
    assert "azDeg_altDeg" in ini, "the mode our .txt is actually written in"


def test_both_stellarium_rotations_stay_zero_because_the_texture_is_pre_rotated(tmp_path):
    """`angle_rotatez` turns the image; `polygonal_angle_rotatez` turns the polygon.

    Stellarium's own `zero` landscape comments on why they are separate. Setting
    either would turn the numbers away from the picture and destroy the property
    that makes the package worth shipping — that a wrong yaw is visible as the
    observer's own house in the wrong place. So the texture is rotated into true
    azimuth here and both keys ship as zero.
    """
    from terminus.landscape import _rotate_east, write_landscape

    texture = np.zeros((32, 64, 3), np.uint8)
    texture[:, :8] = 200  # a bright marker at the panorama's own azimuth 0
    write_landscape(str(tmp_path / "ls"), _LS_ROWS, dict(_LS_META, yaw=90.0), texture=texture)
    ini = (tmp_path / "ls" / "landscape.ini").read_text()
    assert "type = spherical" in ini
    assert "angle_rotatez = 0" in ini and "polygonal_angle_rotatez = 0" in ini
    assert "maptex_top = 90" in ini and "maptex_bottom = -90" in ini
    # Derived from the convention, not restated from the implementation.
    # `orient` defines yaw by phi = target_az - yaw, so a native column phi sits
    # at TRUE azimuth phi + yaw. The marker is at native 0, so with yaw 90 it
    # belongs at true azimuth 90 — a QUARTER of the way across a 64px image,
    # x = 16. The opposite roll would put it at 48, which is true 270: still a
    # horizon, just somebody else's, and indistinguishable by eye for a small
    # yaw. This assertion is the only thing standing between the two.
    rolled = _rotate_east(texture, 90.0)
    assert rolled[0, 0, 0] == 0, "the marker no longer sits at true north"
    assert rolled[0, 16, 0] == 200, "native 0 with yaw 90 is true 90, a quarter across"
    assert rolled[0, 48, 0] == 0, "and emphatically not true 270"


def test_a_panorama_of_an_unoriented_mask_is_refused(tmp_path):
    """A PNG has nowhere to carry a warning, so it cannot be allowed to lie.

    The text exporters at least ship a comment saying the azimuth is not true
    north. A picture just shows the wrong horizon.
    """
    import pytest

    from terminus.export import UnorientedMask
    from terminus.landscape import to_skysafari_png, write_landscape

    with pytest.raises(UnorientedMask):
        to_skysafari_png(_LS_ROWS, str(tmp_path / "p.png"), {"oriented": False})
    with pytest.raises(UnorientedMask):
        write_landscape(str(tmp_path / "ls"), _LS_ROWS, {"oriented": False})


def test_export_writes_the_pictures_only_when_asked(tmp_path):
    """The default export is unchanged; the pictures are opt-in."""
    from terminus.cli import main
    from terminus.export import write_mask

    mask = tmp_path / "h.yaml"
    write_mask(str(mask), {az: (alt, t) for az, alt, t in _LS_ROWS}, [], _LS_META)
    main(["export", str(mask)])
    assert not (tmp_path / "h.skysafari.png").exists()
    assert not (tmp_path / "h_landscape").exists()

    main(["export", str(mask), "--skysafari", "--landscape"])
    png = tmp_path / "h.skysafari.png"
    assert png.exists()
    from PIL import Image

    img = Image.open(png)
    assert img.mode == "RGBA", "Sky Safari reads the alpha channel"
    assert img.size == (2048, 1024), "the vendor's documented panorama size"
    assert (tmp_path / "h_landscape" / "landscape.ini").exists()
    assert (tmp_path / "h_landscape" / "horizon.txt").exists()


def test_a_landscape_invents_no_position_it_was_never_given(tmp_path):
    """`latitude = 0, longitude = 0` is a claim, not a default.

    It would move a Stellarium user to the Gulf of Guinea and tell them nothing
    was wrong. A landscape with no [location] section simply leaves the observer
    where they already are, which is the honest answer for a horizon whose site
    we do not know.
    """
    from terminus.landscape import write_landscape

    sited = tmp_path / "sited"
    write_landscape(str(sited), _LS_ROWS, _LS_META)
    ini = (sited / "landscape.ini").read_text()
    assert "[location]" in ini and "latitude = 39.79" in ini and "longitude = -104.89" in ini

    unsited = tmp_path / "unsited"
    write_landscape(str(unsited), _LS_ROWS, {"measured": "2026-08-05"})
    ini = (unsited / "landscape.ini").read_text()
    assert "[location]" not in ini, "no position recorded means no position claimed"
    assert "latitude" not in ini and "longitude" not in ini
    # The rest of the file must still be a valid, complete landscape.
    import configparser

    cp = configparser.ConfigParser()
    cp.read_string(ini)
    assert cp["landscape"]["polygonal_horizon_list"] == "horizon.txt"


def test_a_failed_render_leaves_no_half_written_landscape(tmp_path):
    """Stellarium reads a partial directory as broken, not as absent.

    horizon.txt was written before the texture was rendered, so a failure
    partway left a landscape with no landscape.ini — presenting as a corrupt
    install rather than as an error. A package that does not exist is a better
    outcome than one that half does.
    """
    import pytest

    from terminus.landscape import write_landscape

    d = tmp_path / "ls"
    with pytest.raises((IndexError, ValueError)):
        write_landscape(str(d), _LS_ROWS, _LS_META, texture=np.zeros((4,), np.uint8))
    assert not d.exists(), "nothing may be left behind when the render fails"

    # And the normal path still produces all three files.
    write_landscape(str(d), _LS_ROWS, _LS_META, texture=np.zeros((8, 16, 3), np.uint8))
    assert {p.name for p in d.iterdir()} == {"landscape.ini", "horizon.txt", "maptex.png"}


def _export_fails(capsys, argv, message):
    """Run the CLI expecting a clean refusal: `error: ...` on stderr, exit 1."""
    import pytest

    from terminus.cli import main

    with pytest.raises(SystemExit) as exc:
        main(argv)
    assert exc.value.code == 1, "a refusal must not look like success"
    err = capsys.readouterr().err
    assert message in err, f"expected {message!r} in stderr, got {err!r}"


def _picture_mask(tmp_path):
    from terminus.export import write_mask

    mask = tmp_path / "h.yaml"
    write_mask(str(mask), {az: (alt, t) for az, alt, t in _LS_ROWS}, [], _LS_META)
    return str(mask)


def test_the_export_explains_an_unreadable_texture_instead_of_tracing_back(tmp_path, capsys):
    """A missing file is the most ordinary failure there is.

    It reached the user as a raw traceback while a mask with a typo'd flag got a
    clean sentence — backwards, since the file path is the one with the obvious
    fix. UnidentifiedImageError subclasses OSError, so a file that is not an
    image at all is covered by the same catch.
    """
    from PIL import Image

    mask = _picture_mask(tmp_path)
    _export_fails(
        capsys,
        ["export", mask, "--landscape", "--texture", str(tmp_path / "nope.png")],
        "could not read the texture",
    )

    not_an_image = tmp_path / "notes.txt"
    not_an_image.write_text("this is not a panorama")
    _export_fails(
        capsys,
        ["export", mask, "--landscape", "--texture", str(not_an_image)],
        "could not read the texture",
    )

    good = tmp_path / "t.png"
    Image.fromarray(np.zeros((8, 16, 3), np.uint8)).save(good)
    bad_cov = tmp_path / "c.npy"
    bad_cov.write_bytes(b"not a numpy array")
    _export_fails(
        capsys,
        ["export", mask, "--landscape", "--texture", str(good), "--coverage", str(bad_cov)],
        "could not read the coverage",
    )

    # A coverage from a different run must not be silently stretched to fit.
    wrong = tmp_path / "w.npy"
    np.save(wrong, np.ones((4, 4)))
    _export_fails(
        capsys,
        ["export", mask, "--landscape", "--texture", str(good), "--coverage", str(wrong)],
        "does not match texture",
    )


def test_a_texture_with_nothing_to_draw_on_is_refused(tmp_path, capsys):
    """Silently ignoring --texture is the bad outcome.

    The person believes they rendered a photo-real horizon and got the plain
    .hrz they already had, with nothing said about it. The check has to sit
    before the early return, not inside the texture loader, because that return
    is exactly what skips the loader.
    """
    from PIL import Image

    mask = _picture_mask(tmp_path)
    tex = tmp_path / "t.png"
    Image.fromarray(np.zeros((8, 16, 3), np.uint8)).save(tex)

    _export_fails(capsys, ["export", mask, "--texture", str(tex)], "add --skysafari")
    _export_fails(capsys, ["export", mask, "--coverage", str(tex)], "add --skysafari")
    # Coverage without a texture describes nothing.
    _export_fails(
        capsys,
        ["export", mask, "--landscape", "--coverage", str(tex)],
        "pass one or neither",
    )


def test_the_missing_coverage_warning_reaches_stderr(tmp_path, capsys):
    """Without coverage the uncovered canvas is drawn as ground.

    That is a real loss of honesty, so it must be said out loud rather than left
    for the viewer to discover as a black wall they have no reason to doubt.
    """
    from PIL import Image

    from terminus.cli import main

    mask = _picture_mask(tmp_path)
    tex = tmp_path / "t.png"
    Image.fromarray(np.full((16, 32, 3), 90, np.uint8)).save(tex)

    main(["export", mask, "--landscape", "--texture", str(tex)])
    err = capsys.readouterr().err
    assert "--coverage" in err and "drawn" in err
    assert (tmp_path / "h_landscape" / "maptex.png").exists(), "and it still produces the package"


def test_an_allow_unoriented_landscape_says_so_where_a_person_will_read_it(tmp_path):
    """A picture cannot carry a `#` comment, but a landscape browser shows a name.

    Exporting an unoriented mask is a legitimate workflow — someone who set
    north by hand — and the flag exists for it. But the azimuth is still not
    true north, and the description line is the only part of the package a
    person reliably sees.
    """
    from terminus.landscape import write_landscape

    d = tmp_path / "ls"
    write_landscape(str(d), _LS_ROWS, {"oriented": False, "lat": 1.0, "lon": 2.0},
                    allow_unoriented=True)  # fmt: skip
    ini = (d / "landscape.ini").read_text()
    assert "UNORIENTED" in ini and "NOT true north" in ini

    oriented = tmp_path / "ok"
    write_landscape(str(oriented), _LS_ROWS, _LS_META)
    assert "UNORIENTED" not in (oriented / "landscape.ini").read_text()
    assert "POSITION-SPECIFIC" in (oriented / "landscape.ini").read_text()


def test_a_mask_covering_part_of_the_circle_still_renders(tmp_path):
    """A partial sweep is a normal intermediate state, not an error.

    `_ascending_pairs` closes the wrap by repeating the first column at 0 and
    360, so the gap is filled by interpolation rather than crashing. Whether
    that is the RIGHT filling is a separate question — it is a straight line
    across ground nobody measured — but it must not raise.
    """
    from terminus.landscape import horizon_altitudes, render

    partial = [(10, 12.0, "structure"), (20, 15.0, "structure"), (30, 9.0, "structure")]
    alts = horizon_altitudes(partial, 72)
    assert np.isfinite(alts).all(), "every column must get a value"
    assert abs(alts[2] - 12.0) < 1.0, "and a measured column keeps its own"
    rgba = render(partial, (72, 36))
    assert rgba.shape == (36, 72, 4)

    single = [(90, 20.0, "structure")]
    assert np.allclose(horizon_altitudes(single, 8), 20.0), "one column is a flat horizon"


def test_a_failed_write_leaves_no_half_package_either(tmp_path, monkeypatch):
    """Building before writing only protects against bad INPUT.

    The writes can still fail — a full disk on the last of three — and that
    leaves the same corrupt-looking directory the ordering exists to prevent,
    just from I/O instead. So the writes are undone.
    """
    import pytest

    from terminus import landscape

    real = landscape._write

    def fail_on_ini(path, text):
        if path.endswith("landscape.ini"):
            raise OSError(28, "No space left on device")
        return real(path, text)

    monkeypatch.setattr(landscape, "_write", fail_on_ini)
    d = tmp_path / "ls"
    with pytest.raises(OSError):
        landscape.write_landscape(str(d), _LS_ROWS, _LS_META)
    assert not d.exists(), "a directory we created must not survive a failed write"

    # A directory the person already had is not ours to delete, but our own
    # half-written files in it still are.
    theirs = tmp_path / "theirs"
    theirs.mkdir()
    (theirs / "notes.txt").write_text("mine")
    with pytest.raises(OSError):
        landscape.write_landscape(str(theirs), _LS_ROWS, _LS_META)
    assert theirs.exists() and (theirs / "notes.txt").exists(), "their files are untouched"
    assert not (theirs / "horizon.txt").exists(), "ours are cleaned up"


def test_re_exporting_without_a_texture_drops_the_old_one(tmp_path):
    """A directory holding a picture its ini does not name lies about itself.

    Stellarium reads only what landscape.ini references, so a stale maptex.png
    is harmless to the program — and misleading to the person who opens the
    folder and sees a photograph that is no longer part of the landscape.
    """
    from terminus.landscape import write_landscape

    d = tmp_path / "ls"
    write_landscape(str(d), _LS_ROWS, _LS_META, texture=np.zeros((8, 16, 3), np.uint8))
    assert (d / "maptex.png").exists()

    write_landscape(str(d), _LS_ROWS, _LS_META)  # same directory, no texture now
    assert not (d / "maptex.png").exists(), "the old picture is not part of this landscape"
    assert "type = polygonal" in (d / "landscape.ini").read_text()


def test_an_unwritable_destination_is_explained_not_traced(tmp_path, capsys):
    """A read-only directory is an ordinary thing to hit.

    The round-1 wrap made it a sentence rather than a traceback, and nothing
    was holding that in place — deleting the wrap left the whole suite green.

    Writing the test moved the fix: the wrap covered only the picture exporters,
    so plain `terminus export` still traced back on the identical failure. The
    whole command is wrapped now.
    """
    import os
    import stat

    import pytest

    mask = _picture_mask(tmp_path)
    if os.geteuid() == 0:
        pytest.skip("root ignores the write bit, so there is nothing to test")
    mode = os.stat(tmp_path).st_mode
    os.chmod(tmp_path, mode & ~stat.S_IWUSR)
    try:
        _export_fails(capsys, ["export", mask, "--skysafari"], "could not write the export")
        # And the plain export, which the first version of the wrap missed.
        _export_fails(capsys, ["export", mask], "could not write the export")
    finally:
        os.chmod(tmp_path, mode)


def test_the_hor_profile_is_readable_by_pvsyst_s_own_rules(tmp_path):
    """PVsyst: 'all lines containing text are considered comment lines'.

    So the header needs no marker character — but every DATA line must contain
    no text at all, or PVsyst silently drops it as a comment and the profile
    comes back short with no error.
    """
    from terminus.export import to_pvsyst_hor

    text = to_pvsyst_hor(
        [(0, 10.0, "tree"), (90, 30.0, "structure"), (180, 5.0, "structure")],
        {"lat": 39.79, "lon": -104.89},
    )
    lines = [ln for ln in text.splitlines() if ln.strip()]
    data = [ln for ln in lines if not any(c.isalpha() for c in ln)]
    assert len(data) >= 4, "0, three measured columns and the 360 wrap"
    for ln in data:
        az, alt = ln.split()
        float(az), float(alt)  # both parse as plain numbers
    # The convention is stated, because PVsyst's import dialog asks for it and
    # guessing produces a mirrored horizon that still looks plausible.
    assert any("Clockwise" in ln for ln in lines)
    assert any("true north" in ln for ln in lines)
    # PVsyst reads a latitude/longitude comment as the profile's metadata.
    assert any("Latitude 39.79" in ln and "Longitude -104.89" in ln for ln in lines)
    # The vegetation margin is applied here as everywhere else, and declared.
    first = next(ln for ln in data if ln.startswith("0 "))
    assert float(first.split()[1]) == 13.0, "10 measured + 3 degrees of foliage margin"
    assert any("Vegetation" in ln for ln in lines)


def test_the_hor_profile_refuses_an_unoriented_mask():
    """A solar siting tool has no more business with a rotated horizon than a
    planner does, and the file's own header would be claiming true north."""
    import pytest

    from terminus.export import UnorientedMask, to_pvsyst_hor

    with pytest.raises(UnorientedMask):
        to_pvsyst_hor([(0, 10.0, "tree")], {"oriented": False})


def test_our_hrz_matches_what_the_existing_generators_emit(tmp_path):
    """Drop-in for people already using HRZ-Creator or panorama-horizon-maker.

    Their writer is `f"{azimuth} {elevation:.1f}\\n"` — space separated, one
    pair per line, ascending, starting at azimuth 0. Ours adds `#` comment
    lines, which N.I.N.A. accepts and they simply never wrote. The test is that
    stripping comments leaves a file with their exact shape, so a mask from
    terminus goes where one of theirs went.
    """
    from terminus.export import to_nina_hrz

    text = to_nina_hrz([(0, 10.0, "structure"), (90, 30.0, "structure")], {"lat": 1, "lon": 2})
    data = [ln for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]
    assert data[0].split()[0] == "0", "their README: the file needs to start with Az 0"
    azimuths = [float(ln.split()[0]) for ln in data]
    assert azimuths == sorted(azimuths), "ascending azimuth"
    assert azimuths[-1] == 360.0, "and closed at 360 so nothing extrapolates the wrap"
    for ln in data:
        assert len(ln.split()) == 2, "one space-separated az/alt pair per line"


def test_export_writes_the_hor_only_when_asked(tmp_path, capsys):
    """Opt-in, like the pictures: the default export is unchanged."""
    from terminus.cli import main

    mask = _picture_mask(tmp_path)
    main(["export", mask])
    assert not (tmp_path / "h.HOR").exists()

    main(["export", mask, "--pvsyst"])
    hor = tmp_path / "h.HOR"
    assert hor.exists()
    assert "Clockwise" in hor.read_text()
    assert "north azimuth 0 on import" in capsys.readouterr().out


def test_a_non_finite_altitude_is_refused_by_every_exporter():
    """`alt: nan` loads without complaint, because float("nan") is a valid float.

    PVsyst is the sharp case and the reason this is checked at all: it treats
    any line containing text as a comment, so `180 nan` is not a malformed
    number, it is a COMMENT. The column vanishes and the profile comes back one
    short with nothing said. Checked in `_ascending_pairs` because that is where
    every exporter passes.
    """
    import pytest

    from terminus.export import MaskError, to_nina_hrz, to_pvsyst_hor, to_stellarium_txt
    from terminus.landscape import horizon_altitudes

    rows = [(0, 10.0, "structure"), (180, float("nan"), "structure")]
    meta = {"lat": 1, "lon": 2}
    for call in (
        lambda: to_pvsyst_hor(rows, meta),
        lambda: to_nina_hrz(rows, meta),
        lambda: to_stellarium_txt(rows, meta),
        lambda: horizon_altitudes(rows, 64),
    ):
        with pytest.raises(MaskError, match="non-finite"):
            call()
    # Infinity is the same class of mistake and names the column too.
    with pytest.raises(MaskError, match=r"\[180\]"):
        to_pvsyst_hor([(0, 10.0, "structure"), (180, float("inf"), "structure")], meta)


def test_the_hor_never_writes_a_number_in_scientific_notation():
    """`format(1e-05, "g")` is "1e-05" — a letter, so PVsyst reads it as a comment.

    Not a parse error the user would see: the column is silently dropped. A
    hair-thin altitude is implausible from a real sweep and entirely plausible
    from a hand-edited mask, which is exactly the file this format invites
    people to edit.
    """
    from terminus.export import to_pvsyst_hor

    rows = [(0, 1e-05, "structure"), (90, 0.0000123, "structure"), (180, 12.5, "structure")]
    text = to_pvsyst_hor(rows, {"lat": 1, "lon": 2}, tree_buffer=0)
    data = [ln for ln in text.splitlines() if ln and not any(c.isalpha() for c in ln)]
    assert len(data) == 4, "every column must survive as data, not become a comment"
    assert "e-" not in text and "e+" not in text
    for ln in data:
        az, alt = ln.split()
        float(az), float(alt)


# ---- the guided orientation loop (terminus-20) ----------------------------
def _synthetic_horizon(step=5):
    """A photo horizon with real structure: steep stretches carry the yaw."""
    import math

    return [
        (a, 20.0 + 12.0 * math.sin(math.radians(2 * a)) + 6.0 * math.cos(math.radians(a)), "structure")
        for a in range(0, 360, step)
    ]  # fmt: skip


# The accurate grid costs tens of seconds per refit — fine against the minutes a
# real column takes to measure, hopeless in a suite. These tests are about the
# LOOP (does it converge, when does it stop, what does it do with an absence),
# not about the fit's resolution, so they run it coarse.
_COARSE = {"yaw_step": 2.0, "tilt_max": 6.0, "tilt_step": 3.0, "pitch_range": 6.0}


def _truth_measure(rows, truth, ceiling=60.0, snr=9.0):
    """A `measure(az)` that reports what a scope WOULD see under `truth`."""
    from terminus import guide, orient

    sample = guide.photo_sample(rows)

    def measure(az):
        phi, raw = orient.native_column(
            sample, az, truth["yaw"], truth["tilt_mag"], truth["tilt_dir"]
        )
        _, alt = orient.rotate(
            phi + truth["yaw"], raw, truth["pitch"], truth["tilt_mag"], truth["tilt_dir"]
        )
        return {"alt": float(alt), "snr": snr}, ceiling, 0.5

    return measure


def test_the_loop_recovers_an_orientation_it_was_never_told():
    """The claim the whole project rests on, as a test rather than a hope.

    A handful of information-chosen columns should pin the photo horizon to the
    sky. Nothing here tells the loop the answer: it plans a column, is told what
    a telescope under a known orientation would have seen, refits, and stops
    when the yaw holds still.
    """
    from terminus import guide

    rows = _synthetic_horizon()
    truth = {"yaw": 36.0, "pitch": 1.5, "tilt_mag": 3.0, "tilt_dir": 110.0}
    solution, steps = guide.run(
        rows,
        _truth_measure(rows, truth),
        max_columns=12,
        log=lambda *a, **k: None,
        fit_kw=_COARSE,
    )
    assert solution is not None
    err = abs(((solution["yaw"] - truth["yaw"] + 180) % 360) - 180)
    assert err < 2.5, f"yaw off by {err:.2f} deg (grid is {_COARSE['yaw_step']} deg)"
    assert abs(solution["pitch"] - truth["pitch"]) < 2.0
    assert abs(solution["tilt_mag"] - truth["tilt_mag"]) < 3.5
    # And it did it with a handful of columns, not a blind circle.
    used = [s for s in steps if s.fiducial is not None]
    assert len(used) <= 12, "the point is that a few chosen columns suffice"


def test_the_loop_stops_when_the_yaw_settles_not_when_the_residual_is_small():
    """Four points fitting four parameters interpolate: the RMS reads near zero
    however wrong the answer is, and it is blind to uniform bias because the fit
    absorbs a constant offset into pitch. What settles is the yaw."""
    from terminus import guide

    rows = _synthetic_horizon()
    truth = {"yaw": 200.0, "pitch": 0.0, "tilt_mag": 1.0, "tilt_dir": 0.0}
    solution, steps = guide.run(
        rows,
        _truth_measure(rows, truth),
        max_columns=16,
        log=lambda *a, **k: None,
        fit_kw=_COARSE,
    )
    assert not any(s.note == "did not settle" for s in steps), "this one should converge"
    fits = [s for s in steps if s.solution is not None]
    # The first fit already had a tiny residual — that is exactly the trap.
    assert fits[0].solution["rms"] < 1.0, "four points interpolate four parameters"
    assert len(fits) > 1, "and stopping there would have been wrong"


def test_an_unsettled_fit_says_so_rather_than_looking_converged():
    """Running out of columns is not convergence.

    The number that comes back is the best estimate available and is returned
    rather than withheld — but an unsettled fit reads exactly like a settled one
    unless something says otherwise.
    """
    from terminus import guide

    rows = _synthetic_horizon()
    truth = {"yaw": 37.0, "pitch": 1.5, "tilt_mag": 3.0, "tilt_dir": 110.0}
    messages = []
    solution, steps = guide.run(
        rows, _truth_measure(rows, truth), max_columns=5,  # too few to settle
        window=3, log=lambda m, **k: messages.append(m), fit_kw=_COARSE,
    )  # fmt: skip
    assert solution is not None, "the estimate is still returned"
    assert any(s.note == "did not settle" for s in steps)
    assert any("STOPPED WITHOUT SETTLING" in m for m in messages)


def test_a_column_that_finds_nothing_is_a_bound_and_one_never_tried_is_not():
    """Two different absences, and conflating them loses the mask's meaning.

    'Measured, and blocked above the ceiling' is information the fit uses as a
    one-sided constraint. 'Never attempted' is not, and recording it as a bound
    would invent a measurement.
    """
    from terminus import guide

    rows = _synthetic_horizon()
    truth = {"yaw": 15.0, "pitch": 0.0, "tilt_mag": 0.0, "tilt_dir": 0.0}
    honest = _truth_measure(rows, truth)

    def measure(az):
        if az in (90, 95):
            return None  # never attempted
        if az in (180, 185):
            return None, 60.0, 0.5  # attempted, found nothing
        return honest(az)

    solution, steps = guide.run(
        rows, measure, candidates=[0, 90, 95, 180, 185, 270, 45, 135, 225, 315],
        max_columns=10, log=lambda *a, **k: None, fit_kw=_COARSE,
    )  # fmt: skip
    by_az = {s.az: s for s in steps if s.az is not None}
    # THE POSITIVE CASE, which is the half that was missing: a column measured
    # and found empty must arrive as a bound AT THE CEILING, not be dropped.
    for az in (180, 185):
        assert az in by_az, f"az {az} was measured and must appear"
        f = by_az[az].fiducial
        assert f is not None, f"az {az} found nothing, which is still a measurement"
        assert f.bound is True, f"az {az} must be a BOUND, not an exact edge"
        assert f.alt == 60.0, "and the bound sits at the ceiling it searched to"
    # The negative case: never attempted is a skip, and nothing is invented.
    for az in (90, 95):
        assert by_az[az].note == "skipped"
        assert by_az[az].fiducial is None, "nothing was invented for a column never tried"
    assert solution is not None


def test_replay_reads_a_saved_night_and_needs_no_telescope():
    """A column costs minutes of scope time and a clear night.

    The judgement made from its brightness profile is a few lines of arithmetic
    that keep changing, so being able to re-run the planner against a real night
    without needing another one is the difference between tuning against
    evidence and tuning against memory.
    """
    from terminus import guide

    profiles = {
        "0": [[35, 60.0], [30, 61.0], [25, 59.0], [20, 12.0], [15, 11.0], [10, 10.5]],
        "90": [[35, 62.0], [30, 60.0], [25, 61.0], [20, 60.0], [15, 59.0], [10, 58.0]],
    }
    measure = guide.replay(profiles, uncertainty=1.0)
    edge, ceiling, unc = measure(0)
    assert ceiling == 35 and unc == 1.0
    # The LAST CLEAR SKY sample, not the first terrain one: the mask's own
    # definition is "altitude = lowest clear sky", and the true crossing lies
    # somewhere between 20 and 25 where the replay cannot resolve it.
    assert edge is not None and edge["alt"] == 25, "the step from 59 to 12 counts"
    # A column with no step is NOT automatically a bound. Which it is depends on
    # whether the column was dark or bright, and replay must answer that the same
    # way the live path does or a replayed night manufactures fiducials the real
    # one refused (terminus-58).
    #
    # az 90 is bright all the way down — clear sky to the search floor. That
    # bounds the horizon from BELOW, which `Fiducial` cannot express, so it is
    # dropped rather than recorded as "at least 35" (M-19). Reading it as an
    # upper bound was worth 40 degrees of yaw on real data.
    assert measure(90) is None, "an open column is not a bound at the ceiling"

    # A column dark throughout IS an upper bound: nothing was found up to the
    # ceiling, so the horizon is at or above it.
    blocked = guide.replay(
        {
            "0": profiles["0"],
            "90": profiles["90"],
            "180": [[35, 8.0], [30, 7.0], [25, 9.0], [20, 8.0], [15, 7.0], [10, 8.0]],
        },
        uncertainty=1.0,
    )
    edge, ceiling, _unc = blocked(180)
    assert edge is None and ceiling == 35, "a dark column is a bound at the ceiling"

    # A column that night never visited is not attempted at all.
    assert measure(270) is None


def test_orienting_a_mask_is_not_a_relabelling_when_there_is_tilt():
    """Yaw alone renames columns; tilt moves a point in azimuth as well.

    So the photo column that ENDS at a true azimuth is not the one that started
    at `az - yaw`, and shifting the mask by the yaw would reintroduce exactly
    the small-angle error the fit exists to avoid.
    """
    from terminus import guide

    rows = _synthetic_horizon()
    sol = {"yaw": 30.0, "pitch": 0.0, "tilt_mag": 8.0, "tilt_dir": 45.0}
    oriented = dict((az, alt) for az, alt, _ in guide.orient_mask(rows, sol))
    naive = {az: alt for az, alt, _ in rows}
    # A pure relabelling would put the native column at az-30 exactly here.
    diffs = [
        abs(oriented[az] - naive[(az - 30) % 360]) for az in oriented if (az - 30) % 360 in naive
    ]
    assert max(diffs) > 0.5, "with 8 deg of tilt a shift is not the same answer"

    # ...but that difference is mostly the ROTATION, which any implementation
    # would apply. Isolate the fixed point itself: compare against reading the
    # photo at the naive column `az - yaw` and rotating THAT. Both rotate; only
    # one solves for the column that actually lands at the target azimuth.
    sample = guide.photo_sample(rows)
    from terminus.orient import native_column
    from terminus.orient import rotate as rot

    worst = 0.0
    for az in range(0, 360, 5):
        phi, raw = native_column(sample, az, 30.0, 8.0, 45.0)
        _, solved = rot(phi + 30.0, raw, 0.0, 8.0, 45.0)
        naive_phi = (az - 30.0) % 360.0
        _, shortcut = rot(naive_phi + 30.0, sample(naive_phi), 0.0, 8.0, 45.0)
        worst = max(worst, abs(float(solved) - float(shortcut)))
    assert worst > 0.5, (
        f"the fixed point must change the answer by more than {worst:.3f} deg, or "
        "native_column is doing nothing and a shortcut would pass this test"
    )

    # With no tilt it IS a relabelling, and must agree.
    flat = guide.orient_mask(rows, {"yaw": 30.0, "pitch": 0.0, "tilt_mag": 0.0, "tilt_dir": 0.0})
    for az, alt, _ in flat:
        # 0.01 because the mask stores two decimals, not because the maths is
        # approximate — with no tilt this is exactly a relabelling.
        assert abs(alt - naive[(az - 30) % 360]) < 0.01


def test_an_inconclusive_column_is_not_recorded_as_a_bound(tmp_path):
    """ "I could not tell" and "it is at least 60 degrees" are different claims.

    `scan_horizon` returns `no_reference` when it has no open-sky brightness to
    compare against, and `inconclusive` when the column neither resolved nor
    read as blocked. Both were being turned into a confident bound at the search
    ceiling, which the fit then trusts one-sidedly — an unevidenced value in a
    file other software consumes, and it happened on EVERY column when the sky
    reference failed to seed.
    """
    from types import SimpleNamespace
    from unittest.mock import MagicMock, patch

    from terminus import cli

    sc = MagicMock()
    sc.is_eq_mode.return_value = True
    cfg = {
        "site": {"lat": 39.79, "lon": -104.89, "elev_m": 1600},
        "sweep": {"az_step": 10, "alt_min": 0, "alt_max": 60, "alt_tol": 0.5,
                  "sun_cone_deg": 30, "slew_step_deg": 5, "clear_thresh": 0.6},
    }  # fmt: skip
    args = SimpleNamespace(dry_run=True, uncertainty=None, stop_above_sun_alt=None)

    verdicts = {
        "no_reference": None,
        "inconclusive": None,
        "blocked_above": "bound",
        "edge(rel 0.42)": "edge",
    }
    for status, want in verdicts.items():
        with patch.object(cli, "scan_horizon", return_value=(60.0 if want != "edge" else 22.0,
                                                             status, "", [])):  # fmt: skip
            measure, _reachable, _stop, _st = cli._scope_measure(sc, cfg, args)
            got = measure(90)
        if want is None:
            assert got is None, f"{status}: must be 'not attempted', got {got}"
        elif want == "bound":
            assert got == (None, 60, None) or got[0] is None, f"{status} is a bound"
        else:
            assert got[0] is not None and got[0]["alt"] == 22.0, f"{status} is a measurement"


def test_a_mount_that_cannot_point_gives_up_instead_of_trying_every_column():
    """`run_sweep` counts consecutive misses; this loop had no equivalent.

    A dead mount would be retried on every remaining candidate — thirty-six
    no-ops — before the run reported "fewer than four columns". Skipping one
    column that will not arrive is right; spending the night proving the mount
    is broken is not.
    """
    from types import SimpleNamespace
    from unittest.mock import MagicMock, patch

    import pytest

    from terminus import cli
    from terminus.sweep import MAX_POINTING_MISSES, PointingError

    sc = MagicMock()
    sc.is_eq_mode.return_value = True
    cfg = {
        "site": {"lat": 39.79, "lon": -104.89, "elev_m": 1600},
        "sweep": {"az_step": 10, "alt_min": 0, "alt_max": 60, "alt_tol": 0.5,
                  "sun_cone_deg": 30, "slew_step_deg": 5, "clear_thresh": 0.6},
    }  # fmt: skip
    args = SimpleNamespace(dry_run=True, uncertainty=None, stop_above_sun_alt=None)

    with patch.object(cli, "scan_horizon", side_effect=PointingError("arm closed")):
        measure, _r, _s, _state = cli._scope_measure(sc, cfg, args)
        for i in range(MAX_POINTING_MISSES - 1):
            assert measure(10 * i) is None, "one miss is a skip, not a verdict on the mount"
        with pytest.raises(cli.SeestarError, match="did not arrive"):
            measure(999)


def test_the_loop_can_be_stopped_by_a_deadline_it_checks_itself():
    """terminus-17: the cutoff was enforced by the caller, before launch.

    A run overran by fourteen minutes because each column took longer than
    estimated — the sky brightened toward dawn, more columns resolved, and it
    slowed exactly as the deadline approached. A caller starting an N-column run
    cannot know how long N columns take.
    """
    from terminus import guide

    rows = _synthetic_horizon()
    truth = {"yaw": 12.0, "pitch": 0.0, "tilt_mag": 0.0, "tilt_dir": 0.0}
    calls = {"n": 0}

    def should_stop():
        calls["n"] += 1
        return calls["n"] > 6  # the window closes partway through

    solution, steps = guide.run(
        rows, _truth_measure(rows, truth), max_columns=20, should_stop=should_stop,
        log=lambda *a, **k: None, fit_kw=_COARSE,
    )  # fmt: skip
    assert any(s.note == "stopped early" for s in steps)
    measured = [s for s in steps if s.fiducial is not None]
    assert len(measured) < 20, "the deadline, not the budget, ended this run"


def test_a_provisional_orientation_does_not_claim_to_be_oriented(tmp_path):
    """`fit_settled` was write-only: nothing in the codebase read it.

    So a run that stopped with the yaw still moving wrote a mask that exported
    silently, with no --allow-unoriented needed — the warning went to stderr and
    the file itself said nothing. Every exporter gates on `oriented` through
    `require_oriented`, so that is the flag a provisional result must set.
    """
    import json
    import math

    import pytest

    from terminus.cli import main
    from terminus.export import load_columns, write_mask

    photo = tmp_path / "photo.yaml"
    rows = {a: (20.0 + 12.0 * math.sin(math.radians(2 * a)), "structure") for a in range(0, 360, 5)}
    write_mask(str(photo), rows, [], {"oriented": False, "lat": 39.79, "lon": -104.89})

    profiles = {
        str(a): [[60 - 5 * i, 80.0 if (60 - 5 * i) > rows[a][0] else 10.0] for i in range(13)]
        for a in range(0, 360, 5)
    }
    prof = tmp_path / "p.json"
    prof.write_text(json.dumps(profiles))

    out = tmp_path / "solved.yaml"
    # Far too few columns to settle: the fit is provisional by construction.
    main(["orient", str(photo), "--replay", str(prof), "--out", str(out),
          "--max-columns", "5"])  # fmt: skip

    meta, _ = load_columns(str(out))
    assert meta["fit_settled"] is False
    assert meta["oriented"] is False, "an unsettled yaw is not an orientation"
    # main() turns UnorientedMask into `error: ...` and exit 1.
    with pytest.raises(SystemExit) as exc:
        main(["export", str(out)])
    assert exc.value.code == 1, "a provisional mask must not export silently"
    # And the escape hatch still works for someone who has decided it is enough.
    main(["export", str(out), "--allow-unoriented"])
    assert (tmp_path / "solved.hrz").exists()


def test_a_column_open_to_the_floor_is_not_an_edge_at_the_floor(tmp_path):
    """ "Found nothing between 0 and 60" is not "the horizon is at 0".

    Recording it as an exact edge at the search floor invents a measurement, and
    a photo predicting 20 degrees there would be scored as 20 degrees wrong when
    the column said nothing of the kind. `orient.from_mask` reaches the same
    conclusion about the same status and excludes it. See terminus-50 for giving
    `Fiducial` the downward bound that would let the fit use these honestly.
    """
    from types import SimpleNamespace
    from unittest.mock import MagicMock, patch

    from terminus import cli

    sc = MagicMock()
    sc.is_eq_mode.return_value = True
    cfg = {
        "site": {"lat": 39.79, "lon": -104.89, "elev_m": 1600},
        "sweep": {"az_step": 10, "alt_min": 0, "alt_max": 60, "alt_tol": 0.5,
                  "sun_cone_deg": 30, "slew_step_deg": 5, "clear_thresh": 0.6},
    }  # fmt: skip
    args = SimpleNamespace(dry_run=True, uncertainty=None, stop_above_sun_alt=None)

    with patch.object(cli, "scan_horizon", return_value=(0.0, "open_to_min", "open", [])):
        measure, _r, _s, _state = cli._scope_measure(sc, cfg, args)
        assert measure(90) is None, "an open column must not become an edge at alt_min"


def test_orient_explains_a_bad_replay_file_instead_of_tracing_back(tmp_path, capsys):
    """The path is typed by hand and the JSON is hand-editable.

    Both are the likeliest things to go wrong here, and both arrived as
    tracebacks while a mask with too few columns got a clean sentence.
    """
    from terminus.cli import main
    from terminus.export import write_mask

    photo = tmp_path / "photo.yaml"
    write_mask(str(photo), {a: (20.0, "structure") for a in range(0, 360, 10)}, [], {})

    bad_json = tmp_path / "bad.json"
    bad_json.write_text("{not json at all")
    wrong_shape = tmp_path / "list.json"
    wrong_shape.write_text("[1, 2, 3]")

    for path, message in (
        (tmp_path / "nope.json", "could not read the profiles"),
        (bad_json, "not valid JSON"),
        (wrong_shape, "azimuth -> "),
    ):
        with pytest.raises(SystemExit) as exc:
            main(["orient", str(photo), "--replay", str(path)])
        assert exc.value.code == 1, f"{path.name} must refuse cleanly, not crash"
        assert message in capsys.readouterr().err, f"{path.name} must say what is wrong"


# ---- terminus-47: the skyglow fit, against the columns that broke it -------
# The measured columns terminus-47 was diagnosed from, COPIED INTO THE REPO
# rather than read out of captures/, which is gitignored as too large to track.
# Left there they skipped everywhere but the machine that measured them — in CI,
# in every fresh clone, and in every reviewer's worktree — which for a
# regression test is worse than not existing, because the suite still reports
# green. They are a few hundred numbers; the evidence belongs with the test.
_COLUMNS_JSON = os.path.join(os.path.dirname(__file__), "data", "terminus_47_columns.json")


def _real_column(name):
    import json

    with open(_COLUMNS_JSON) as f:
        return [(r[0], r[1]) for r in json.load(f)[name]]


def test_the_skyglow_model_is_fitted_to_sky_not_to_the_ground_beneath_it():
    """terminus-47, on the column it was measured from.

    az 60 has a 103-count cliff at altitude 36.46 that is obvious by eye, in a
    column searched from 55 — so more than half its samples are ground. Fitted
    on a fixed top half, the terrain set the slope: +4.244 counts/deg, intercept
    -55.115, predicting NEGATIVE sky below altitude 13. Everything else followed
    from that, and `find_horizon` returned None, "open to the search floor".
    """
    from terminus.night import find_horizon, is_skyglow, sky_model

    prof = _real_column("az60_coarse")
    model, n_top = sky_model(prof)
    assert model["slope"] < 0, "skyglow brightens toward the horizon; this fit is +4.244 when wrong"
    assert n_top < len(prof), "the fit must not have swallowed the terrain"
    assert is_skyglow(model, min(a for a, _ in prof)), "and must predict positive sky throughout"

    alt, detail = find_horizon(prof)
    assert alt is not None, f"the cliff is unmissable by eye; got {detail['reason']}"
    # Samples every 2 deg, true crossing 36.46, so 37 is the last clear sky.
    assert alt == 37.0, f"expected the last clear sample above the cliff, got {alt}"


def test_deep_terrain_never_scores_as_perfect_sky():
    """The worst of the three defects, and the one that poisoned the rest.

    Where the broken model's prediction crossed zero, the ratio was 421.8; below
    that the `pred > 1e-6` guard clamped it to exactly 1.0. So the further into
    the ground a column went, the more sky-like it looked — and any median taken
    across it was ruined. No sample may score as sky where the model has no
    opinion.
    """
    import numpy as np

    from terminus.night import sky_model

    prof = _real_column("az60_coarse")
    model, _ = sky_model(prof)
    alts = np.array([a for a, _ in prof])
    lums = np.array([lum for _, lum in prof])
    pred = model["slope"] * alts + model["intercept"]
    assert (pred > 0).all(), "a usable model predicts positive brightness everywhere it is applied"
    frac = np.where(pred > 0, lums / pred, np.nan)
    deep = frac[alts <= 15]
    assert (deep < 0.5).all(), f"terrain below alt 15 must read as terrain, got {deep.max():.2f}"


def test_a_roofline_thirty_sigma_clear_is_not_missed_by_one_part_in_fifty():
    """az 190 falls 62.88 -> 32.66, a ratio of 0.519 against a 0.5 rule.

    A genuine roofline, 53.6 sigma clear of the sky's own scatter, rejected by
    one part in fifty. That is the mirror of az 60 clearing the same rule by
    0.004 and being discarded by the persistence check — the ratio simply is not
    the instrument.
    """
    from terminus.night import find_horizon

    alt, detail = find_horizon(_real_column("az190_recheck"))
    assert alt is not None, f"got {detail['reason']}"
    # Samples every 1 deg, true crossing 38.59, so 39 is the last clear sky.
    assert alt == 39.0, f"expected 39.0, got {alt}"


def test_a_narrow_refinement_scan_is_refused_rather_than_guessed():
    """A confident wrong answer in the unsafe direction is the failure to avoid.

    The az 190 refinement scan spans 3.5 degrees and is mostly roof. The skyglow
    premise needs a measurable gradient — about a count per degree against one
    to six counts of scatter — so over that window there is nothing to fit, and
    the module answered "open to the search floor" for a column full of
    building. It now declines, and names the tool that does handle a narrow
    two-level window.
    """
    from terminus.night import find_horizon
    from terminus.sweep import find_edge

    prof = _real_column("az190_fine")
    alt, detail = find_horizon(prof)
    assert alt is None
    assert "spans only" in detail["reason"] and "find_edge" in detail["reason"]

    # And the tool it points at does resolve it, conservatively.
    k, _step, _snr = find_edge(prof, max(lum for _, lum in prof))
    assert k is not None and prof[k][0] >= 38.59, "find_edge handles the narrow window"


def test_a_curved_but_open_sky_is_not_mistaken_for_terrain():
    """The model is a straight line; the real sky is a curve. Does it false-fire?

    This is the risk the threshold change creates: `MIN_DIP_FRAC` moved from a
    factor of two to a quarter, so a shallower departure now counts. If real
    skyglow curvature could produce a quarter-drop below the fitted line, the
    detector would invent a horizon in open sky — and a mask too pessimistic is
    a mask that throws away good targets.

    It cannot, and the reason is worth stating because it is not obvious from
    the code: the curvature runs the SAFE way. Skyglow brightens toward the
    horizon faster than linearly, so an air-mass curve sits ABOVE its own
    straight-line fit at low altitude. The departure test only fires on a FALL.
    """
    import math

    import numpy as np

    from terminus.night import find_horizon

    # An air-mass curve anchored to this site's measured numbers: 22 counts at
    # altitude 60 rising to 71 at 12.
    x1, x2 = 1 / math.sin(math.radians(60)), 1 / math.sin(math.radians(12))
    a = (71.0 - 22.0) / (x2 - x1)
    b = 22.0 - a * x1

    def sky(alt):
        return a / math.sin(math.radians(max(alt, 1.0))) + b

    for lo, step in ((10, 2), (5, 2), (12, 1)):
        prof = [(float(al), sky(al)) for al in np.arange(60, lo - 0.01, -step)]
        alt, detail = find_horizon(prof)
        assert alt is None, f"invented a horizon at {alt} in open sky ({detail['reason']})"

    # The other direction is not what skyglow does, but the failure mode there
    # must still not be a fabricated horizon. Strong dimming toward the horizon
    # refuses outright rather than answering.
    # Asserting the REASON, not just the absence of a number. The docstring used
    # to claim these all refuse with "no usable sky model"; the shallow ones
    # report "open" instead, and a test that only checked `alt is None` could not
    # tell the difference — so the claim drifted from the behaviour undetected.
    reasons = {}
    for power in (0.5, 1.5, 3.0):
        prof = [(float(al), 60.0 * (al / 60.0) ** power) for al in np.arange(60, 9.9, -2)]
        alt, detail = find_horizon(prof)
        assert alt is None, f"dimming as alt^{power} produced a horizon at {alt}"
        reasons[power] = detail["reason"]
    assert "open" in reasons[0.5], f"shallow dimming reads as open, got {reasons[0.5]!r}"
    for power in (1.5, 3.0):
        assert (
            "no usable sky model" in reasons[power]
        ), f"steep dimming must refuse outright, got {reasons[power]!r}"


def test_a_lit_wall_filling_the_frame_is_not_sky_with_a_horizon_under_it():
    """The sky-floor test was one-sided and only caught a column too DIM on top.

    A column too BRIGHT on top is just as certainly not sky: a floodlit wall, a
    lit sign, a neighbour's security light filling the frame. Such a column
    passes every check that asks "is there sky up here" and gets a confident
    horizon reported at the wall's own bottom edge — which is optimistic, and
    optimistic is the direction that starts an imaging run into a building.
    """
    from terminus.night import find_horizon

    lit = [(a, 90.0) for a in (30, 29, 28, 27, 26, 25)] + [(a, 5.0) for a in range(24, 10, -1)]
    alt, detail = find_horizon(lit, sky_ref=22.0)
    assert alt is None, f"90 counts against a 22-count sky is a lit surface, got {alt}"
    assert "lit, not sky" in detail["reason"]

    # The floor test still works in the other direction, and an ordinary column
    # whose top is legitimately brighter than a zenith reference still passes —
    # skyglow does brighten toward the horizon, by a factor of about three.
    ordinary = [(a, 30.0 + 0.4 * (60 - a)) for a in range(60, 20, -2)] + [
        (a, 6.0) for a in range(18, 8, -2)
    ]
    alt, _ = find_horizon(ordinary, sky_ref=22.0)
    assert alt is not None, "a column 1.4x the reference at its top is ordinary sky"


def test_a_steeply_graded_column_is_not_mistaken_for_a_glint():
    """The first glint screen compared each sample to the top-of-column MEDIAN.

    On a steeply graded column the highest samples are legitimately far brighter
    than the median beneath them, so that screen threw away the genuine top and
    seeded the fit to the bottom of the column — the opposite of what it was
    written to protect. A glint is a spike above its NEIGHBOURS, which is local
    and survives any gradient.
    """
    from terminus.night import sky_model

    steep = [(60.0, 100.0), (58.0, 80.0), (56.0, 60.0), (54.0, 40.0), (52.0, 20.0)]
    model, n_top = sky_model(steep + [(float(a), 4.0) for a in (50, 48, 46)])
    # The model must pass through the TOP of the column. A positive slope alone
    # is not enough to prove that — the median screen also produced one, fitted
    # to the tail — so this checks where the line actually sits.
    assert (
        abs(model["slope"] * 60.0 + model["intercept"] - 100.0) < 10.0
    ), "the fit must pass near the topmost sample, not the flat tail beneath it"
    assert n_top >= 4


def test_one_glint_at_the_top_does_not_discard_the_whole_column():
    """A satellite, an aircraft light or a bright planet in the topmost sample.

    The seed was the top four samples unconditionally, so one transient made the
    seed model nonsense and `is_skyglow` then threw away a column that was
    otherwise perfectly measurable. A non-result rather than a wrong one, but
    the loss is avoidable and the doctrine is that execution should be robust.
    """
    from terminus.night import find_horizon

    prof = _real_column("az60_coarse")
    clean = find_horizon(prof)[0]
    assert clean == 37.0, "precondition: the clean column still resolves"

    for factor in (3.0, 5.0, 10.0):
        glinted = [(prof[0][0], prof[0][1] * factor)] + prof[1:]
        alt, detail = find_horizon(glinted)
        assert (
            alt == clean
        ), f"a {factor}x glint in the top sample changed the answer to {alt} ({detail['reason']})"


def test_every_refusal_path_is_exercised_not_merely_written():
    """Guards nothing exercises are guards nobody knows are there.

    Mutating the `j < 0` step-back to return a confident altitude instead of
    refusing left the whole suite green — a fabricated horizon, silently, which
    is precisely what this change exists to prevent. Chasing that down turned up
    something more useful than a test: see the note at the end.
    """
    import numpy as np

    from terminus.night import find_horizon, is_skyglow, sky_model

    # `is_skyglow` False: a model predicting negative brightness over the range
    # it would be applied to. These are az 60's actual broken coefficients.
    assert not is_skyglow({"slope": 4.244, "intercept": -55.115}, 3.0)
    assert is_skyglow({"slope": -0.559, "intercept": 178.16}, 3.0)
    steep = [(float(a), 60.0 * (a / 60.0) ** 3) for a in np.arange(60, 9.9, -2)]
    alt, detail = find_horizon(steep)
    assert alt is None and "no usable sky model" in detail["reason"]

    # THE `pred <= 0` BREAK IN GROWTH CANNOT CHANGE AN OUTCOME, which I only
    # established by trying to test it. Where the model predicts non-positive
    # brightness, any real sample is brighter than the prediction, so the
    # lamp branch one line below catches it and steps over instead. And pred is
    # linear, so once it has gone non-positive going down it stays there — every
    # remaining sample takes the same branch. Break or continue, `kept` ends up
    # identical. It stays as a statement of intent; it is not load-bearing.
    plunge = [(60.0, 100.0), (58.0, 80.0), (56.0, 60.0), (54.0, 40.0), (52.0, 20.0)] + [
        (float(a), 4.0) for a in np.arange(50, 9.9, -2)
    ]
    model, n_top = sky_model(plunge)
    assert n_top < len(plunge), "growth stops rather than swallowing the tail"
    assert model["slope"] * 10.0 + model["intercept"] <= 0, "precondition: it does go negative"
    assert find_horizon(plunge)[0] is None, "and the column is then refused, not guessed"

    # THE `j < 0` GUARD IS NOT REACHED BY ANY INPUT I COULD BUILD, and that is
    # worth recording rather than faking. It needs every sample above the
    # departure to be a lamp — but `sky_model` fits the model TO those samples,
    # so they cannot sit far above their own prediction, which is what
    # `mask_lights` requires. A column whose top really is that bright trips the
    # SKY_CEIL_FACTOR test several lines earlier instead. The guard stays as
    # defence in depth, because `mask_lights` and the seed are independently
    # changeable, but nobody should believe it is covered.
    lamps = [(60.0, 300.0), (58.0, 310.0), (56.0, 305.0), (54.0, 308.0)] + [
        (float(a), 4.0) for a in np.arange(52, 9.9, -2)
    ]
    alt, detail = find_horizon(lamps, sky_ref=100.0)
    assert alt is None, f"no clear sky to report, got {alt}"
    assert "lit, not sky" in detail["reason"], "caught by the ceiling test, not the step-back"


def test_a_lamp_is_stepped_over_during_growth_not_stopped_at():
    """`continue` and `break` give the same answer here, and different models.

    Sky usually continues below a lamp, and it is still sky. Stopping at one
    throws away every sample beneath it, so the gradient is fitted to a shorter
    baseline than the column actually offers — which matters most on the columns
    where a lamp sits high and the real sky runs well below it.
    """
    from terminus.night import sky_model

    sky = [(float(a), 40.0 - 0.2 * a) for a in range(60, 20, -2)]
    with_lamp = sky[:5] + [(50.0, 400.0)] + sky[5:]
    _model, n_over = sky_model(with_lamp)
    assert n_over > 6, f"growth must continue past the lamp, only reached {n_over}"


def test_from_mask_reads_every_mask_this_repo_has_ever_written():
    """It returned ZERO fiducials for every mask written since 2026-08-02.

    `from_mask` matched `type == "edge"` and `type.startswith("blocked")`, which
    was the vocabulary of that date — `blocked>35` literally encoded "above the
    35 degree ceiling". When the vocabulary became tree/structure/open the
    encoding went with it, and this returned an empty list for every mask since.
    Nothing failed, because an empty list is a legal result, and the only caller
    was the worked example in the package docstring — so the documented way in
    returned nothing and said nothing.
    """
    from terminus.orient import from_mask

    # The old vocabulary still exists on disk and still means something.
    old = {
        0: {"alt": 35.0, "type": "blocked>35"},
        40: {"alt": 32.5, "type": "edge"},
        90: {"alt": 20.0, "type": "unknown"},
    }
    fids = from_mask(old, ceiling=35.0)
    assert len(fids) == 2, "a failed measurement carries no information; the other two do"
    assert [f.az for f in fids if f.bound] == [0.0]

    # The current one, where boundedness is its own field rather than a spelling.
    now = {
        0: {"alt": 60.0, "type": "structure", "bound": True},
        40: {"alt": 32.5, "type": "tree"},
        90: {"alt": 12.0, "type": ""},
    }
    fids = from_mask(now, ceiling=60.0)
    assert len(fids) == 3, f"an untyped column is an ordinary measurement, got {len(fids)}"
    assert [f.az for f in fids if f.bound] == [0.0]

    # A column with no altitude is not a measurement at all.
    assert from_mask({0: {"type": "tree"}}) == []

    # `clipped` is the PHOTO's way of saying the same thing a scope bound says:
    # the obstruction ran off the top of the frame, so the altitude beside it is
    # a lower bound. Read as a confirmed exact edge it is the same mistake this
    # function exists to stop making, one instrument over.
    clipped = from_mask({0: {"alt": 60.0, "type": "tree", "clipped": True}}, ceiling=60.0)
    assert [f.bound for f in clipped] == [True], "a clipped photo column is a bound"


def test_bound_survives_a_round_trip_through_the_mask_file():
    """A one-sided constraint the file cannot express is a constraint that is lost.

    `orient.fit` scores a bound one-sidedly — a photo that also exceeds the
    ceiling confirms it exactly, and only falling short contradicts it. Written
    back as an ordinary measurement it becomes "the horizon is exactly at 60",
    which is a different and much stronger claim than the scope made.
    """
    import tempfile

    from terminus.export import load_columns, write_mask
    from terminus.orient import from_mask

    path = os.path.join(tempfile.mkdtemp(), "m.yaml")
    write_mask(
        path,
        {0: {"alt": 60.0, "type": "structure", "bound": True}, 90: {"alt": 22.0, "type": "tree"}},
        [],
        {"lat": 1, "lon": 2},
    )
    _meta, cols = load_columns(path)
    assert cols[0]["bound"] is True
    assert cols[90].get("bound") is None, "an ordinary column claims nothing"
    assert [f.az for f in from_mask(cols, ceiling=60.0) if f.bound] == [0.0]
    assert any("bound" in ln for ln in open(path) if ln.startswith("#")), "and it is explained"


def test_a_lamp_inside_terrain_is_not_read_as_a_horizon():
    """az 340 and az 350, measured 2026-08-03, settled from the saved frames.

    Both columns read 5-9 counts from altitude 60 all the way down, with a
    BRIGHT BAND at 12-15 degrees and darkness below it again. Dark above and
    dark below a bright band is a lamp sitting inside the obstruction — a
    horizon would be bright above and dark below. The sweep's original verdict
    of "blocked above 60" was right; the 10.0 degree reading a trend-insensitive
    noise estimate produced was the lamp's lower edge.

    Both detectors are asked, because the disagreement between them is what made
    this ambiguous at the time. And az 340 was measured on two separate nights;
    both runs are in this file, they differ by up to 5 counts, and both are
    refused. That is the strongest form the answer comes in.
    """
    from terminus.night import find_horizon
    from terminus.sweep import find_edge

    # captures/2026-08-03-night-targeted/scan/, luminance from the frame names.
    # A SECOND run of az 340 from 2026-08-04 is used by
    # test_night_ignores_a_streetlight_inside_terrain above; the two differ by up
    # to 5 counts because they are different nights, and both show the same lamp
    # signature and are both refused. Independent confirmation, not a conflict.
    columns = {
        340: [(60, 5.7), (50, 6.0), (45, 8.7), (40, 7.0), (35, 6.0), (30, 6.3), (25, 6.0),
              (20, 7.7), (17.5, 11.0), (15, 50.7), (12.5, 53.3), (10, 27.7), (7.5, 8.0),
              (5, 6.7), (2.5, 5.7), (0, 6.0)],
        350: [(60, 4.7), (50, 5.0), (45, 5.0), (40, 7.0), (35, 6.0), (30, 6.0), (25, 7.0),
              (20, 6.0), (17.5, 7.0), (15, 9.0), (12.5, 20.0), (10, 20.7), (7.5, 4.7),
              (5, 5.0), (2.5, 5.0), (0, 5.0)],
    }  # fmt: skip
    sky_ref = 20.7  # the open sky measured that night

    for az, prof in columns.items():
        k, _step, snr = find_edge(prof, sky_ref)
        assert k is None, f"az {az}: find_edge found an edge at {prof[k][0]} (snr {snr:.1f})"
        alt, detail = find_horizon(prof, sky_ref=sky_ref)
        assert alt is None, f"az {az}: night detector reported {alt} ({detail['reason']})"
        assert "blocked" in detail["reason"], f"az {az}: {detail['reason']}"


def test_a_blocked_column_reaches_the_mask_as_a_bound(tmp_path):
    """terminus-51: the sweep knew, logged it, and threw it away.

    `scan_horizon` returns `blocked_above` when it searched from the ceiling down
    and never found an edge — the horizon is AT LEAST alt_max, which `orient.fit`
    scores one-sidedly. That status lived in a local variable that was logged and
    discarded, so the mask could not tell it from an exact measurement at the
    ceiling, and `orient.from_mask` could not reconstruct it. Which matters most
    for `--replay`, since a replay is only as good as what the night saved.
    """
    from unittest.mock import MagicMock, patch

    import numpy as np

    from terminus.export import load_columns
    from terminus.orient import from_mask
    from terminus.sweep import Sky, run_sweep

    class NightSky(Sky):
        """The Sun pinned below the horizon.

        This test used a real `Sky` at wall-clock time and so depended on the
        hour it was run: it passed at 21:00 with the Sun down and failed at 09:09
        the next morning, when az 120 became genuinely Sun-blocked and never
        reached the mask. A test about how a BOUND is recorded should not have an
        opinion about the time of day.
        """

        def sun(self):
            # -5: below SUN_SAFE_ALT so the guard stands down, above
            # NIGHT_SUN_ALT so the sweep keeps the day channel this test's
            # patched scanner belongs to.
            return 270.0, -5.0

    sky = NightSky(39.7917, -104.894, 1600)
    sc = MagicMock()
    sc.equ_coord.return_value = (12.0, 20.0)
    sc.capture_rgb.return_value = np.full((8, 8, 3), 120.0, dtype=np.float32)
    cfg = {
        "sun_cone_deg": 30, "slew_step_deg": 5, "az_step": 120, "alt_min": 0,
        "alt_max": 60, "alt_tol": 2.5, "clear_thresh": 0.6,
    }  # fmt: skip

    def fake_scan(ptr, sc_, az, *a, **k):
        if az == 120:
            return 60.0, "blocked_above", "structure", []
        return 20.0, "edge(rel 0.4)", "tree", []

    with patch("terminus.sweep.scan_horizon", side_effect=fake_scan):
        mask, _skipped, _prof = run_sweep(
            sc, sky, cfg, az_start=0, az_end=240, dry=True, log=lambda *a, **k: None
        )

    assert mask[120]["bound"] is True, "a blocked column is a bound"
    assert mask[0]["bound"] is False, "an edge is not"

    # And it survives to the file and back out as a one-sided fiducial.
    from terminus.export import write_mask

    out = tmp_path / "m.yaml"
    write_mask(str(out), mask, [], {"lat": 39.79, "lon": -104.89})
    _meta, cols = load_columns(str(out))
    assert [f.az for f in from_mask(cols, ceiling=60.0) if f.bound] == [120.0]


def test_the_sweep_stops_itself_when_the_window_closes(tmp_path):
    """terminus-17: the deadline was enforced by the caller, before launch.

    A run overran by fourteen minutes, and the reason is the part worth keeping:
    the sky brightened toward dawn, so MORE columns resolved and each took
    longer — the run slowed down exactly as its deadline approached. An estimate
    made at the start degrades in the direction that matters, so the check has
    to live where the answer is current.
    """
    from unittest.mock import MagicMock, patch

    import numpy as np

    from terminus.sweep import Sky, run_sweep

    sky = Sky(39.7917, -104.894, 1600)
    sc = MagicMock()
    sc.equ_coord.return_value = (12.0, 20.0)
    sc.capture_rgb.return_value = np.full((8, 8, 3), 120.0, dtype=np.float32)
    cfg = {
        "sun_cone_deg": 30, "slew_step_deg": 5, "az_step": 30, "alt_min": 0,
        "alt_max": 60, "alt_tol": 2.5, "clear_thresh": 0.6,
    }  # fmt: skip

    def sweep(should_stop):
        with patch("terminus.sweep.scan_horizon", return_value=(20.0, "edge", "tree", [])):
            return run_sweep(
                sc, sky, cfg, az_start=0, az_end=330, dry=True,
                log=lambda *a, **k: None, should_stop=should_stop,
            )[0]  # fmt: skip

    # Differential, because Sun-cone skips already shorten a sweep on their own —
    # a bare count would pass with no deadline check at all, which is exactly
    # what a first version of this test did.
    calls = {"n": 0}

    def closes_after_three():
        calls["n"] += 1
        return calls["n"] > 3

    stopped = sweep(closes_after_three)
    full = sweep(None)
    assert len(stopped) < len(
        full
    ), f"the deadline must shorten the run: {len(stopped)} measured with it, {len(full)} without"
    assert len(stopped) > 0, "and what was measured before it closed is kept"
    # A sweep that stops is not a sweep that failed.
    assert all("alt" in c for c in stopped.values())


def test_refinement_checks_the_deadline_before_every_column():
    """Per column, not per round — a round is up to 24 columns long.

    The check used to sit at the top of the refine `while`, so once a round had
    started it ran to completion: with the default `refine_max_columns` that is
    24 further slews, each a coarse walk plus a bisection, after the window had
    closed. Refinement is the LAST phase of a sweep, which is when a dawn
    deadline is nearest, and it accelerates as the sky brightens and more
    columns resolve — terminus-17's failure mode, one scope deeper. SAFE-02.
    """
    from unittest.mock import MagicMock, patch

    import numpy as np

    from terminus.sweep import Sky, run_sweep

    sky = Sky(39.7917, -104.894, 1600)
    sc = MagicMock()
    sc.equ_coord.return_value = (12.0, 20.0)
    sc.capture_rgb.return_value = np.full((8, 8, 3), 120.0, dtype=np.float32)
    cfg = {
        "sun_cone_deg": 0, "slew_step_deg": 5, "az_step": 30, "alt_min": 0,
        "alt_max": 60, "alt_tol": 2.5, "clear_thresh": 0.6,
        # Every neighbouring pair disagrees, so refinement wants to subdivide
        # everywhere and only the budget holds it back.
        "az_refine_deg": 1, "refine_threshold_deg": 0, "refine_max_columns": 24,
    }  # fmt: skip

    seq = {"n": 0}

    def alternating(*a, **k):
        seq["n"] += 1
        return (10.0 if seq["n"] % 2 else 50.0, "edge", "tree", [])

    # THE WINDOW MUST CLOSE *MID-ROUND*, which is the whole point. The main
    # sweep asks 12 times (one per column); the old code's next question was at
    # the top of the refine `while`, the new code's is before each candidate
    # pair. Closing on question 13 stops BOTH versions identically and the test
    # passes against the bug — it did, on the first attempt. Letting 13 through
    # and closing on 14 separates them: the old code has already committed to a
    # whole round of up to `refine_max_columns`, the new one has committed to
    # exactly one column.
    asked = {"n": 0}

    def closes_as_refinement_opens():
        asked["n"] += 1
        return asked["n"] > 13

    from terminus.sweep import Pointer, Sky

    with (
        patch.object(Sky, "sun", lambda self: (297.0, -5.0)),  # terminus-63: pin the channel
        patch("terminus.sweep.scan_horizon", side_effect=alternating),
        patch("terminus.sweep.save_boundary_frame"),
        patch.object(Pointer, "point_to", lambda self, az, alt: (az, alt)),
    ):
        mask, _skipped, _prof = run_sweep(
            sc, sky, cfg, az_start=0, az_end=330, dry=False,
            log=lambda *a, **k: None, should_stop=closes_as_refinement_opens,
        )  # fmt: skip

    refined = {az for az in mask if az % cfg["az_step"]}
    assert len(refined) <= 1, (
        f"refinement continued past the deadline: {len(refined)} columns added "
        f"after the window closed ({sorted(refined)})"
    )


def test_a_run_the_window_closed_on_does_not_look_like_a_finished_one():
    """Exit code and mask must both say the sweep was cut short.

    Both ways a sweep ends early used to be distinguishable only by log text: a
    PointingError abort printed SWEEP ABANDONED and exited 2, while a closed
    observing window returned normally, wrote and exported the mask exactly as a
    complete run would, and exited 0. A scheduler — which is the whole reason
    the cutoff flag exists — could not tell "the window closed and columns are
    missing" from "everything asked for was measured".
    """
    from terminus.cli import _sun_deadline

    class FakeSky:
        def __init__(self, alt):
            self._alt = alt

        def sun(self):
            return 180.0, self._alt

    # Never reached: the deadline must not claim it fired.
    open_window = _sun_deadline(FakeSky(-30.0), -12.0)
    assert open_window() is False
    assert open_window.fired is False, "an unfired deadline must not report a truncated run"

    closed = _sun_deadline(FakeSky(-5.0), -12.0)
    assert closed.fired is False, "it has not been asked yet"
    assert closed() is True
    assert closed.fired is True, "the deadline has to remember, or the caller cannot tell"

    assert _sun_deadline(FakeSky(0.0), None) is None, "no cutoff asked for, nothing to enforce"


def test_the_sun_deadline_reads_the_sun_each_time_it_is_asked():
    """Not once at the start — that is the bug, not the implementation detail."""
    from types import SimpleNamespace

    from terminus.cli import _sun_deadline

    reads = {"n": 0}

    def sun():
        reads["n"] += 1
        return 180.0, -20.0 + reads["n"] * 2.0  # climbing toward dawn

    stop = _sun_deadline(SimpleNamespace(sun=sun), -14.0)
    assert stop() is False, "-18 is still dark"
    assert stop() is False, "-16 too"
    assert stop() is True, "-14 has reached the cutoff"
    assert reads["n"] == 3, "the Sun must be re-read every time, not cached"

    assert _sun_deadline(SimpleNamespace(sun=sun), None) is None, "no cutoff, no check"


def test_a_column_is_checked_along_its_whole_length_not_just_its_ends():
    """Found 2026-08-05 17:46, with the scope set up and about to sweep.

    `column_touches_sun` tested `alt_min` and `alt_max` only. The Sun spends
    most of the day at a middling altitude, which is the MIDDLE of a 0-60
    column, so both ends can be clear while the scan passes straight through it.
    With the Sun at az 271 alt 25.5, az 250 and az 290 were reported safe and
    come within 19.1 and 16.9 degrees of it.

    The same mistake this module's docstring warns about for slew paths, in the
    function that decides which columns to attempt at all. `Pointer.point_to`
    refused the individual slews so nothing was in danger — but the planner
    proposed columns the mount would abandon partway up, wasting the observing
    time it exists to save.
    """
    from terminus.sweep import ang_sep, column_touches_sun

    class FixedSky:
        """The Sun parked mid-column, which is where it lives most of the day."""

        @staticmethod
        def sun():
            return 271.0, 25.5

    sky = FixedSky()
    for az in range(0, 360, 5):
        closest = min(ang_sep(az, alt / 2.0, 271.0, 25.5) for alt in range(0, 121))
        assert column_touches_sun(sky, az, 0, 60, 30) == (
            closest < 30
        ), f"az {az}: closest approach {closest:.1f} deg disagrees with the guard"

    # The two that were wrong, named so a regression is unmistakable.
    assert column_touches_sun(sky, 250, 0, 60, 30), "az 250 passes 19.1 deg from the Sun"
    assert column_touches_sun(sky, 290, 0, 60, 30), "az 290 passes 16.9 deg from the Sun"
    # A column genuinely clear of it is still allowed.
    assert not column_touches_sun(sky, 90, 0, 60, 30), "the anti-Sun column is safe"

    # And a Sun below the horizon blocks nothing, whatever the geometry says.
    class NightSky:
        @staticmethod
        def sun():
            return 271.0, -20.0

    assert not column_touches_sun(NightSky(), 271, 0, 60, 30)


def test_a_mask_written_from_real_instruments_is_still_yaml(tmp_path):
    """Found by running the CLI on the actual scope, not by any test here.

    `write_mask` serialises meta as a Python repr, which is YAML only by
    coincidence: it holds for str, int, float, bool, list and dict, and breaks
    for anything else. Numpy 2 changed scalar repr from `39.7917` to
    `np.float64(39.7917)`, and lat/lon reach `default_meta` from astropy as
    numpy scalars — so every mask written by a real sweep since that numpy
    release had a lat and lon that `yaml.safe_load` reads back as a STRING.

    `float()` on that raises, which means `cli._check_mergeable` — whose whole
    job is refusing a merge across a tripod move — could not run without a
    traceback. The guard was inoperative on exactly the files it guards.

    Every test in this suite builds meta from Python literals, which is why none
    of them saw it.
    """
    import numpy as np
    import yaml

    from terminus.export import load_columns, write_mask

    path = str(tmp_path / "m.yaml")
    write_mask(
        path,
        {0: (60.0, "structure"), 90: (12.5, "tree")},
        [],
        {
            "measured": "2026-08-05 18:03",
            "lat": np.float64(39.7917),  # as astropy hands it over
            "lon": np.float64(-104.894),
            "alt_search": [np.int64(0), np.int64(60)],
            "clear_thresh": np.float32(0.85),
        },
    )
    raw = yaml.safe_load(open(path))
    for key in ("lat", "lon", "clear_thresh"):
        value = raw["meta"][key]
        assert isinstance(value, float), f"{key} came back as {type(value).__name__}"
        float(value)  # the operation _check_mergeable performs
    assert raw["meta"]["alt_search"] == [0, 60]
    assert all(isinstance(v, int) for v in raw["meta"]["alt_search"])

    # And the guard that could not run now can.
    from types import SimpleNamespace

    from terminus.cli import _check_mergeable

    meta, _cols = load_columns(path)
    here = SimpleNamespace(loc=SimpleNamespace(lat=SimpleNamespace(deg=39.7917),
                                               lon=SimpleNamespace(deg=-104.894)))  # fmt: skip
    _check_mergeable(dict(meta, oriented=True), here)  # same spot: allowed, no traceback


def test_labels_are_voted_not_last_wins_and_never_invented_where_no_frame_looked():
    """Combining remapped label layers, which is where a class can be fabricated.

    Two frames overlapping may disagree, and the answer more of the evidence
    supports beats the one that happened to be stitched second — the same
    reasoning `segment_classes` already uses across tiles. And a pixel no frame
    covered has no class at all: -1, not a guess.
    """
    import numpy as np
    from PIL import Image

    from terminus.mosaic import combine_labels

    def layer(tmp, name, value, box):
        """A remapped layer: class `value` inside `box`, transparent elsewhere."""
        x0, y0, x1, y1 = box
        rgb = np.zeros((y1 - y0, x1 - x0, 4), np.uint8)
        rgb[..., :3] = value
        rgb[..., 3] = 255
        path = os.path.join(tmp, name)
        im = Image.fromarray(rgb, "RGBA")
        im.save(path, tiffinfo={286: ((x0, 1),), 287: ((y0, 1),), 282: ((1, 1),), 283: ((1, 1),)})
        return path

    import tempfile

    tmp = tempfile.mkdtemp()
    # Two frames call the same strip 'tree' (4); one calls it 'building' (1).
    paths = [
        layer(tmp, "a.tif", 4, (0, 0, 6, 4)),
        layer(tmp, "b.tif", 4, (2, 0, 8, 4)),
        layer(tmp, "c.tif", 1, (2, 0, 8, 4)),
    ]
    out = combine_labels(paths, 10, 4)
    assert out[0, 3] == 4, "two votes for tree beat one for building"
    assert out[0, 0] == 4, "a pixel only one frame saw takes that frame's answer"
    assert (out[:, 8:] == -1).all(), "no frame looked here, so there is no class"


def test_composite_applies_the_gains_it_solves(tmp_path):
    """The gains contract, made unmissable (terminus-52 item 1).

    `render()` returns RAW layers; `composite()` solves a per-frame gain and
    APPLIES it before averaging. A naive mean of the same layers reintroduces
    the exposure steps — and the manifest's `gains` field is an output of the
    composite, not an input to reapply. This is the test the ticket asks for:
    composite with solved gains must differ from the naive mean, so removing
    the `* g` in `composite()` fails here and nowhere else.
    """
    import numpy as np
    from PIL import Image

    from terminus.mosaic import composite

    def layer(name, value, box):
        x0, y0, x1, y1 = box
        rgba = np.zeros((y1 - y0, x1 - x0, 4), np.uint8)
        rgba[..., :3] = value
        rgba[..., 3] = 255
        path = str(tmp_path / name)
        Image.fromarray(rgba, "RGBA").save(
            path, tiffinfo={286: ((x0, 1),), 287: ((y0, 1),), 282: ((1, 1),), 283: ((1, 1),)}
        )
        return path

    # The same patch of sky, metered two stops apart: one frame reads 100,
    # the other 200. The overlap is total, so the naive mean is exactly 150
    # everywhere and any deviation from it is the gains at work.
    paths = [layer("dim.tif", 100, (0, 0, 8, 4)), layer("bright.tif", 200, (0, 0, 8, 4))]
    img, coverage, gains = composite(paths, 8, 4)

    assert (coverage == 2).all(), "the fixture must overlap fully for the arithmetic to hold"
    # Geometric-mean-1 gains put both frames at 100*sqrt(2) ~= 141, not 150.
    assert (
        abs(gains[0] * 100.0 - gains[1] * 200.0) < 2.0
    ), f"the gains must make the overlap agree, got {gains}"
    got = float(img[..., 0].mean())
    assert (
        abs(got - 150.0) > 4.0
    ), f"composite returned the naive mean ({got}): the solved gains were not applied"
    assert abs(got - 141.4) < 3.0, f"expected ~141 from geometric-mean-1 gains, got {got}"


def test_photometric_is_opt_in_and_never_touches_the_measurement_render(tmp_path):
    """terminus-52 item 2: --photometric is for figures, not for numbers.

    Off by default, and when on it writes a SEPARATE <out>.figure.png from the
    same geometric solve — the plain render, the coverage, and everything the
    measurement path reads must be byte-identical with and without the flag,
    because every published residual was produced without photometric
    correction and a changed pixel value there is a changed number downstream.
    """
    from types import SimpleNamespace
    from unittest.mock import MagicMock, patch

    import numpy as np

    from terminus import cli, mosaic

    plain = np.full((4, 8, 3), 90, np.uint8)
    pretty = np.full((4, 8, 3), 120, np.uint8)
    cov = np.ones((4, 8))

    def run(photometric):
        out = tmp_path / ("with" if photometric else "without")
        out.mkdir()
        args = SimpleNamespace(
            image_dir=str(tmp_path), work=str(out / "w"), out=str(out / "pano"),
            lens=None, min_points=13, no_celeste=False, width=8, height=4,
            segment=False, photometric=photometric,
        )  # fmt: skip
        fig_render = MagicMock(return_value=(["f0.tif"], "photometric.pto"))

        def fake_composite(tiffs, w, h):
            return (pretty if tiffs == ["f0.tif"] else plain, cov, [1.0])

        with (
            patch.object(mosaic, "require_hugin"),
            patch.object(mosaic, "solve", return_value=("p.pto", set())),
            patch.object(mosaic, "control_point_counts", return_value={"a.jpg": 20}),
            patch.object(mosaic, "render", return_value=(["l0.tif"], "final.pto")),
            patch.object(mosaic, "composite", side_effect=fake_composite),
            patch.object(mosaic, "render_photometric", fig_render),
        ):
            cli.cmd_mosaic(None, {}, args)
        return out, fig_render

    out_off, fig_off = run(photometric=False)
    assert not fig_off.called, "photometric must be strictly opt-in"
    assert not (out_off / "pano.figure.png").exists()

    out_on, fig_on = run(photometric=True)
    fig_on.assert_called_once_with("final.pto", str(out_on / "w")), (
        "the figure render must hang off the SAME geometric solve"
    )
    assert (out_on / "pano.figure.png").exists(), "the figure render gets its own file"
    assert (out_on / "pano.png").read_bytes() == (
        out_off / "pano.png"
    ).read_bytes(), "the measurement render must be byte-identical with and without the flag"


def test_a_failed_photometric_render_does_not_cost_the_manifest(tmp_path):
    """Round 1 P2: the figure block ran before write_manifest with no guard.

    The photometric fit is a separate optimisation that can fail after the
    geometric one succeeded; aborting there lost the manifest — the run's
    reprocessing record — for a purely cosmetic failure. The figure is the one
    output whose absence costs nothing, so its failure costs a warning.
    """
    from types import SimpleNamespace
    from unittest.mock import MagicMock, patch

    import numpy as np

    from terminus import cli, mosaic

    out = tmp_path / "pano"
    args = SimpleNamespace(
        image_dir=str(tmp_path), work=str(tmp_path / "w"), out=str(out), lens=None,
        min_points=13, no_celeste=False, width=8, height=4, segment=False, photometric=True,
    )  # fmt: skip
    with (
        patch.object(mosaic, "require_hugin"),
        patch.object(mosaic, "solve", return_value=("p.pto", set())),
        patch.object(mosaic, "control_point_counts", return_value={"a.jpg": 20}),
        patch.object(mosaic, "render", return_value=(["l0.tif"], "final.pto")),
        patch.object(
            mosaic,
            "composite",
            return_value=(np.zeros((4, 8, 3), np.uint8), np.ones((4, 8)), [1.0]),
        ),
        patch.object(
            mosaic,
            "render_photometric",
            MagicMock(side_effect=mosaic.MosaicError("autooptimiser -m failed")),
        ),
    ):
        cli.cmd_mosaic(None, {}, args)  # must not raise

    assert (
        tmp_path / "pano.manifest.json"
    ).exists(), "the manifest is the run's reprocessing record; a cosmetic failure must not cost it"
    assert not (tmp_path / "pano.figure.png").exists()


def test_the_figure_render_feeds_nona_the_photometric_solve(tmp_path):
    """Round 1's sharpest surviving mutation: nona fed final.pto, silently.

    render_photometric's whole job is the second pto - autooptimiser -m writes
    photometric.pto and nona must render FROM IT. Feeding nona the original
    final.pto skips the photometric model entirely while producing an
    identical-looking figure file, which nothing downstream can detect.
    """
    import subprocess
    from unittest.mock import patch

    from terminus.mosaic import render_photometric

    seen = []

    def fake_run(cmd, *a, **kw):
        seen.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    with (
        patch("terminus.mosaic.require_hugin"),
        patch("terminus.mosaic._run", side_effect=fake_run),
    ):
        _tiffs, photo = render_photometric(str(tmp_path / "final.pto"), str(tmp_path))

    assert seen[0][0] == "autooptimiser" and "-m" in seen[0], seen[0]
    assert seen[0][-1].endswith("final.pto"), "the photometric solve starts from the geometry"
    assert seen[1][0] == "nona", seen[1]
    assert seen[1][-1].endswith(
        "photometric.pto"
    ), f"nona must render FROM the photometric solve, got {seen[1][-1]}"
    assert photo.endswith("photometric.pto")


def test_a_label_whose_name_does_not_match_the_project_is_refused(tmp_path):
    """Silent success is the failure mode this whole path exists to remove.

    `remap_labels` swaps filenames by matching the project's own spelling. A
    caller whose keys differ by so much as a directory prefix rewrites nothing,
    nona cheerfully warps the PHOTOGRAPHS, and `combine_labels` then reads RGB
    brightness as ADE20K class ids. Exit code 0, plausible output, completely
    wrong — which is precisely the shape of corruption the coordinate-lookup
    rewrite was written to eliminate, arriving through a different door.
    """
    from unittest.mock import patch

    import pytest

    from terminus.mosaic import MosaicError, remap_labels

    project = tmp_path / "final.pto"
    project.write_text(
        'p f2 w2880 h1440 v360 n"TIFF_m"\nm i0\ni w3000 h4000 f0 n"stage/frame1.jpg"\n'
    )
    # The subject is the NAME guard, which fires before nona is ever invoked.
    # Without this patch the test asserted what the dev box happens to satisfy
    # (E-04): on a host with no hugin, require_hugin raises first with a
    # different message, and CI is such a host.
    with patch("terminus.mosaic.require_hugin"), pytest.raises(MosaicError, match="does not contain"):  # fmt: skip
        # Right file, wrong spelling: no directory prefix.
        remap_labels(str(project), str(tmp_path), {"frame1.jpg": "labels/001.png"})


def test_a_label_project_asks_nona_for_coordinates_not_for_pixels(tmp_path):
    """A class id must never pass through nona's pixel pipeline.

    THIS TEST USED TO ASSERT THE WRONG THING. It checked that the project's `m`
    line said `i6` — nearest neighbour — on the theory that poly3 would average
    tree (4) and building (1) into a class neither frame contained. The reasoning
    was right and the mechanism was not: **nona ignores the `m` line's
    interpolator entirely**, and `i0`, `i5` and `i6` produce byte-identical
    output. The guard had never once worked, and this test reported that it had,
    which is E-02 — a test pinning a bug instead of catching it. On the real
    2026-08-03 set, 75% of one frame's pixels came back holding a class that was
    never in the source.

    So the property to assert is the one that now makes it exact: nona is asked
    for COORDINATES (`-c`), and the ids are looked up afterwards in the
    full-resolution label frame.
    """
    import subprocess
    from unittest.mock import patch

    from terminus.mosaic import remap_labels

    project = tmp_path / "final.pto"
    project.write_text(
        'p f2 w2880 h1440 v360 E14.08 n"TIFF_m"\n'
        "m i0\n"
        'i w3000 h4000 f0 Eev15.0 n"stage/frame1.jpg"\n'
        'i w3000 h4000 f0 Eev13.1 n"stage/frame2.jpg"\n'
    )
    seen = []

    def fake_run(cmd, *a, **kw):
        seen.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    # require_hugin is patched for the same reason _run is: the subject is the
    # project rewrite and the nona ARGUMENTS, and a host with no hugin (CI)
    # must exercise them identically to one with it (E-04).
    with patch("terminus.mosaic.require_hugin"), patch("terminus.mosaic._run", side_effect=fake_run):  # fmt: skip
        layers = remap_labels(str(project), str(tmp_path), {"stage/frame1.jpg": "labels/001.png"})

    assert seen, "nona was never invoked"
    assert "-c" in seen[0], f"nona must be asked for coordinate images:\n{seen[0]}"
    assert layers == [], "no coordinate images on disk means no layers, not a crash"

    written = (tmp_path / "label.pto").read_text()
    assert 'n"labels/001.png"' in written, "the label image must replace the photograph"
    assert 'n"stage/frame2.jpg"' in written, "a frame with no label given is left alone"
    assert "w3000 h4000" in written, "geometry must be copied through untouched"


def test_the_inverse_rotation_undoes_the_forward_one():
    """`polar.project` draws the photograph by asking the inverse question.

    Forward-mapping — carry each panorama pixel to where it lands — leaves
    scatter holes that look exactly like missing data, and missing data is a real
    and separately meaningful thing in these renders. So the disc is inverse
    mapped, and the inverse has to actually be one.
    """
    import numpy as np

    from terminus.orient import rotate, rotate_inverse

    pitch, tilt_mag, tilt_dir = 4.5, 7.0, 210.0
    az = np.array([0.0, 37.0, 129.0, 251.0, 359.0])
    alt = np.array([-15.0, 0.0, 22.0, 61.0, 84.0])

    out_az, out_alt = rotate(az, alt, pitch, tilt_mag, tilt_dir)
    back_az, back_alt = rotate_inverse(out_az, out_alt, pitch, tilt_mag, tilt_dir)

    # Compare azimuth on the circle: 359.9 and 0.1 are two tenths apart.
    delta = (back_az - az + 180.0) % 360.0 - 180.0
    assert np.allclose(delta, 0.0, atol=1e-9), f"azimuth did not come back: {delta}"
    assert np.allclose(back_alt, alt, atol=1e-9), f"altitude did not come back: {back_alt - alt}"


def test_the_polar_page_lists_the_fit_columns_with_their_fates(tmp_path):
    """Devon asked for the columns ON the report, not only in the mask meta.

    The table is built from `meta.fit_fiducials` — the record the fit writes of
    every column it was offered (terminus-53) — so the page shows the value,
    the kind, whether the fit used it, and the reason when it did not. A mask
    without the record gets no table: inventing rows from the horizon block
    would show numbers the fit never saw. Reasons are hand-written strings in a
    hand-editable file, so they are escaped.
    """
    from terminus import polar

    rows = [(a, 20.0, "structure") for a in range(0, 360, 10)]
    sol = {"yaw": 133.0, "pitch": 1.0, "tilt_mag": 2.0, "tilt_dir": 100.0}
    meta = {
        "yaw": 133.19, "fit_rms": 0.31,
        "fit_fiducials": [
            {"az": 90.0, "alt": 6.2, "bound": False, "used": True, "residual": -0.12},
            {"az": 0.0, "alt": 60.0, "bound": True, "used": True, "residual": None},
            {"az": 20.0, "alt": 32.5, "used": False, "reason": "dawn transition",
             "excluded_by": "mask"},
            {"az": 33.0, "used": False, "reason": "inconclusive <profile>"},
        ],
    }  # fmt: skip
    html_page = polar.page(rows, sol, meta=meta, size=200)
    assert "Telescope columns (2 used of 4 offered)" in html_page
    assert "&lt;profile&gt;" in html_page, "hand-written reasons must be escaped"

    # PER ROW, not page-wide substrings: round 1 showed swapped bound/edge and
    # excluded/not-used labels survive containment checks, because some row
    # somewhere always says each word.
    import re

    table_rows = {}
    for m in re.finditer(r"<tr[^>]*>(<td>.*?)</tr>", html_page):
        cells = re.findall(r"<td>(.*?)</td>", m.group(1))
        table_rows[cells[0]] = cells
    assert table_rows["0"][2] == "bound" and table_rows["0"][3] == "used"
    assert table_rows["90"][2] == "edge" and table_rows["90"][3] == "used"
    assert table_rows["90"][4] == "-0.12&deg;", "a used column shows its own residual"
    assert table_rows["20"][3] == "excluded" and "dawn transition" in table_rows["20"][4]
    assert table_rows["33"][3] == "not used"

    bare = polar.page(rows, sol, meta={"yaw": 133.0}, size=200)
    assert "Telescope columns (" not in bare, "no record, no table"


def test_the_polar_page_is_self_contained_and_layered(tmp_path):
    """One file, no network, and the claims separable from each other.

    The page is the artifact a person actually looks at and forwards, so it may
    not depend on a CDN, a font host, or a sibling image that will not travel
    with it. And the horizon, the telescope's own columns and the unphotographed
    region are three different claims about the same sky: "does the yellow line
    follow the roofline" cannot be answered while the yellow line covers it.
    """
    import re

    from terminus import polar
    from terminus.orient import Fiducial

    rows = [(float(az), 20.0 + 5.0 * (az % 3), "structure") for az in range(0, 360, 10)]
    solution = {"yaw": 130.0, "pitch": 2.0, "tilt_mag": 3.0, "tilt_dir": 180.0}
    fids = [Fiducial(10.0, 30.0, 60.0), Fiducial(200.0, 60.0, 60.0, bound=True)]

    html = polar.page(rows, solution, fiducials=fids, meta={"yaw": 130.0, "fit_rms": 0.8})

    external = [
        m
        for m in re.findall(r'(?:src|href)="([^"]+)"', html)
        if not m.startswith("data:") and not m.startswith("#")
    ]
    assert not external, f"the page reaches outside itself: {external}"

    for layer in ("gap", "grid", "hz", "pts"):
        assert f'data-layer="{layer}"' in html, f"missing the {layer} layer"
        assert f'data-t="{layer}"' in html, f"missing the {layer} toggle"

    # A bound is not a measurement, and must not be drawn as one.
    assert html.count('class="edg"') == 1, "the ordinary column should be a circle"
    assert 'class="bnd"' in html, "the ceiling-limited column needs its own marker"

    path = polar.write_page(str(tmp_path / "p.html"), rows, solution)
    assert os.path.getsize(path) > 0


def test_the_fit_writes_down_which_fiducials_it_used_and_why(tmp_path):
    """terminus-53: the published fit used 16 of 30 and nothing records which.

    That is not a small gap. Anyone refitting from the same sweeps gets ~29
    fiducials including the hard columns the published run discarded, and
    therefore a much worse residual BY CONSTRUCTION rather than by error — then
    goes looking for bugs in frame registration that are not there. It is also a
    comparison error waiting to happen: 3.38 on 29 and 0.69 on 16 are not
    comparable numbers at all (F-25).

    So the fit records the set, not just its size: every column, its value,
    whether it was one-sided, and for anything excluded, the reason.
    """
    import math

    from terminus import guide
    from terminus.orient import Fiducial, fit

    rows = [
        (float(az), 20.0 + 6.0 * math.sin(math.radians(az)), "structure") for az in range(0, 360, 5)
    ]
    sample = guide.photo_sample(rows)
    fids = [
        Fiducial(0.0, 20.0, 60.0),
        Fiducial(90.0, 26.0, 60.0),
        Fiducial(180.0, 20.0, 60.0),
        Fiducial(270.0, 14.0, 60.0),
        Fiducial(45.0, 59.5, 60.0),  # half a degree of headroom: a manufactured edge
    ]
    sol = fit(fids, sample, yaw_step=5.0, tilt_max=3.0, tilt_step=3.0, pitch_range=6.0,
              min_headroom=2.0)  # fmt: skip

    record = {f["az"]: f for f in sol["fiducials"]}
    assert set(record) == {0.0, 45.0, 90.0, 180.0, 270.0}, (
        "every fiducial handed in must appear, used or not — an omitted one is "
        "indistinguishable from one that was never measured"
    )
    assert record[45.0]["used"] is False
    assert "headroom" in record[45.0]["reason"], "and the reason must say which gate rejected it"
    assert record[0.0]["used"] is True and record[0.0]["reason"] is None
    for f in record.values():
        assert {"az", "alt", "bound", "weight", "sigma"} <= set(
            f
        ), "enough to refit from this record alone"


def test_a_column_excluded_in_the_mask_is_never_offered_to_the_planner():
    """The az 60/190 dawn exclusions were folklore: a memory file and a doc.

    Now they live in the mask beside the column, with the reason attached, and
    `from_mask` both honours and RECORDS them — D-12, a thing that failed must
    be recorded as failed rather than quietly omitted.
    """
    from terminus.orient import from_mask

    mask = {
        10: {"alt": 12.0, "type": "structure"},
        20: {"alt": 32.5, "type": "structure", "exclude": "dawn transition"},
        30: {"alt": 8.0, "type": "open"},
        40: {"alt": 60.0, "type": "unknown"},
    }
    excluded = {}
    fids = {int(f.az): f for f in from_mask(mask, ceiling=60.0, excluded=excluded)}

    assert set(fids) == {10}, f"only the usable column should survive, got {sorted(fids)}"
    assert excluded[20] == "dawn transition", "the mask's own reason, verbatim"
    assert "open" in excluded[30], "an open column bounds from below; say so"
    assert "unknown" in excluded[40] or "failed" in excluded[40]
    assert 10 not in excluded


def test_a_falsy_exclude_is_refused_rather_than_read_as_not_excluded():
    """E-12: truthiness fails open, and `exclude` is hand-edited.

    `exclude: false`, `exclude: 0` and `exclude: ""` all look like exclusions to
    the person who typed them; read by truthiness they all silently re-enter the
    fit. The field's vocabulary is closed — a non-empty string reason — and
    anything else raises. An excluded column with no altitude records the
    author's reason, not "no altitude recorded": the hand-written reason is the
    more informative of the two.
    """
    import pytest

    from terminus.orient import exclusion_reason, from_mask

    assert exclusion_reason({"alt": 10.0}) is None
    assert exclusion_reason({"exclude": " dawn transition "}) == "dawn transition"
    for bad in (False, True, 0, 1, "", "   ", ["x"]):
        with pytest.raises(ValueError, match="exclude"):
            exclusion_reason({"exclude": bad})
        with pytest.raises(ValueError, match="exclude"):
            from_mask({20: {"alt": 5.0, "type": "tree", "exclude": bad}})

    excluded = {}
    from_mask({20: {"exclude": "glare"}}, excluded=excluded)
    assert excluded[20] == "glare", "the reason outranks 'no altitude recorded'"


def test_the_cli_writes_mask_exclusions_into_the_fit_record(tmp_path):
    """terminus-53 at the CLI layer, where round 1 found it missing twice over.

    `_fiducial_source` filtered excluded columns and printed them to stderr —
    and nothing else. The written mask's `fit_fiducials` never heard of them,
    so the one artifact the PR exists to create silently omitted the inputs
    someone removed; and no test exercised the CLI path at all, so both the
    filter and the record could be deleted with the suite green (both
    mutations demonstrated on review round 1). Also pinned here: a used
    fiducial has NO `reason` key in the file — this meta is serialised via
    repr, where a literal None round-trips through YAML as the STRING 'None'.
    """
    from types import SimpleNamespace
    from unittest.mock import patch

    import yaml

    from terminus import cli, guide
    from terminus.export import write_mask

    photo = tmp_path / "photo.yaml"
    rows = {a: (20.0 + 5.0 * (a % 20 == 0), "structure") for a in range(0, 360, 10)}
    write_mask(str(photo), rows, [], {"oriented": False, "lat": 39.79, "lon": -104.89})

    fid = tmp_path / "sweep.yaml"
    fid.write_text(
        yaml.safe_dump(
            {
                "meta": {"alt_search": [0, 60]},
                "horizon": {
                    0: {"alt": 20.0, "type": "structure"},
                    90: {"alt": 25.0, "type": "structure"},
                    180: {"alt": 20.0, "type": "structure"},
                    270: {"alt": 15.0, "type": "structure"},
                    20: {"alt": 32.5, "type": "structure", "exclude": "dawn transition"},
                },
            }
        )
    )

    out = tmp_path / "solved.yaml"
    args = SimpleNamespace(
        mask=str(photo), out=str(out), replay=None, fiducials=[str(fid)], seed=3,
        max_columns=5, window=3, yaw_tol=1.0, uncertainty=None, min_headroom=None,
        dry_run=False, frames=None, stop_above_sun_alt=None,
    )  # fmt: skip
    canned = {
        "yaw": 10.0, "pitch": 0.0, "tilt_mag": 0.0, "tilt_dir": 0.0, "rms": 0.1,
        "n": 4, "n_bound": 0, "residuals": {0.0: 0.1},
        "fiducials": [
            {"az": 0.0, "alt": 20.0, "bound": False, "weight": 1.0, "sigma": 1.0,
             "used": True, "residual": 0.1, "reason": None},
        ],
    }  # fmt: skip
    with patch.object(guide, "fit", return_value=canned):
        cli.cmd_orient(None, {"site": {"lat": 39.79, "lon": -104.89, "elev_m": 1600}}, args)

    meta = yaml.safe_load(out.read_text())["meta"]
    record = {f["az"]: f for f in meta["fit_fiducials"]}
    assert 20.0 in record, "the mask's exclusion must reach the written record"
    assert record[20.0]["used"] is False
    assert record[20.0]["reason"] == "dawn transition"
    assert record[20.0]["excluded_by"] == "mask"
    assert record[20.0]["alt"] == 32.5
    assert 20.0 not in meta["fit_columns"], "an excluded column must never enter the fit"
    assert "reason" not in record[0.0], (
        "a used column carries no reason key: repr-serialised None reads back "
        "as the string 'None', so absence is the only honest spelling"
    )
    assert all(v != "None" for f in meta["fit_fiducials"] for v in f.values())


def test_a_malformed_exclude_reaches_the_cli_as_a_clean_maskerror(tmp_path):
    """The CLI translation is load-bearing, not decorative (round 2, E-12).

    `exclusion_reason` raises ValueError; `main()` reports MaskError. Without
    the translation in `_fiducial_source`, the one failure with a five-second
    fix — a typo in a hand-edited `exclude` — arrives as a raw traceback while
    every legitimate refusal gets a clean sentence. The from_mask layer was
    tested; this pins the CLI layer's own wrap, which a round-2 mutation
    showed the suite did not reach.
    """
    import pytest
    import yaml

    from terminus.cli import MaskError, _fiducial_source

    bad = tmp_path / "sweep.yaml"
    bad.write_text(
        yaml.safe_dump({"horizon": {20: {"alt": 32.5, "type": "structure", "exclude": False}}})
    )
    with pytest.raises(MaskError) as exc:
        _fiducial_source([str(bad)], None)
    message = str(exc.value)
    assert (
        "sweep.yaml" in message and "20" in message
    ), "the error must name the file and the column the typo lives in"
    assert "exclude" in message


def test_two_masks_disagreeing_about_a_column_follow_first_wins_either_way(tmp_path):
    """A column is a measurement OR an exclusion, never both (round 2's P1).

    `columns` had first-file-wins while `excluded` overwrote, so two masks
    disagreeing about one azimuth left it simultaneously excluded and live —
    `reachable` said yes, `measure` answered, and the written record carried
    both verdicts. The two maps are one namespace: the first file to speak
    about an azimuth wins, whatever it said, exactly as the merged accepted
    columns already behaved.
    """
    import yaml

    from terminus.cli import _fiducial_source

    excludes = tmp_path / "a.yaml"
    excludes.write_text(
        yaml.safe_dump(
            {"horizon": {20: {"alt": 32.5, "type": "structure", "exclude": "dawn transition"}}}
        )
    )
    measures = tmp_path / "b.yaml"
    measures.write_text(yaml.safe_dump({"horizon": {20: {"alt": 30.0, "type": "structure"}}}))

    _measure, reachable, excluded = _fiducial_source([str(excludes), str(measures)], None)
    assert 20 in excluded and not reachable(
        20
    ), "the excluding file spoke first, so the column is excluded — not also live"

    _measure, reachable, excluded = _fiducial_source([str(measures), str(excludes)], None)
    assert (
        reachable(20) and 20 not in excluded
    ), "the measuring file spoke first, so the column is live — not also excluded"


def _orient_measure_fixture(tmp_path, sun_alt, scan_stub=None):
    """A `measure` built by `_scope_measure` with the hardware mocked out.

    The Sun is pinned (a test about night behaviour must not depend on when the
    suite runs — terminus-63), the seeding slew is refused so the try/except
    absorbs it, and the scanner is a stub the test controls.
    """
    from types import SimpleNamespace
    from unittest.mock import MagicMock, patch

    from terminus import cli
    from terminus.sweep import PointingError, Sky

    sc = MagicMock()
    sc.is_eq_mode.return_value = True
    sc.equ_coord.return_value = (5.0, 20.0)
    cfg = {
        "site": {"lat": 39.79, "lon": -104.89, "elev_m": 1600},
        "sweep": {
            "az_step": 10, "alt_min": 0, "alt_max": 60, "alt_tol": 1.5,
            "coarse_step": 5.0, "sun_cone_deg": 30, "clear_thresh": 0.85,
            "slew_step_deg": 5, "samples_per_point": 1,
        },
    }  # fmt: skip
    args = SimpleNamespace(
        dry_run=False, out=str(tmp_path / "out.yaml"), mask=str(tmp_path / "mask.yaml"),
        frames=str(tmp_path / "frames"), uncertainty=1.0, stop_above_sun_alt=None,
    )  # fmt: skip
    patches = [
        patch.object(cli, "_start_locked"),
        patch.object(Sky, "sun", return_value=(180.0, sun_alt)),
        patch.object(cli.Pointer, "point_to", side_effect=PointingError("mocked")),
    ]
    if scan_stub is not None:
        patches.append(patch.object(cli, "scan_horizon", side_effect=scan_stub))
        patches.append(patch.object(cli, "scan_horizon_night", side_effect=scan_stub))
    started = [pt.start() for pt in patches]
    try:
        measure, reachable, _stop, state = cli._scope_measure(sc, cfg, args)
        yield_val = (measure, reachable, args)
    finally:
        pass  # patches stopped by the caller via the returned stopper
    return yield_val, patches, started


def test_orient_checkpoints_every_column_and_resumes_without_the_scope(tmp_path):
    """terminus-58: a run that dies leaves its columns behind, and a rerun
    serves them from the file instead of re-observing.

    The first prototype of this fix checkpointed only successes, and its very
    first crash — before any success — left nothing. So the test's first
    assertion is that the file exists after ONE measurement, not at the end.
    """
    calls = {"n": 0}

    def scan(*a, **k):
        calls["n"] += 1
        return 12.0, "edge(rel 3.0)", "structure", [(60, 100.0), (30, 90.0), (0, 10.0)]

    (measure, _reach, args), patches, _ = _orient_measure_fixture(tmp_path, 20.0, scan)
    try:
        got = measure(90)
        assert got is not None and got[0]["alt"] == 12.0
        assert calls["n"] == 1
        ckpt = tmp_path / "out_fiducials.jsonl"
        assert ckpt.exists(), "the column must be on disk the moment it completes"
        # Same azimuth again in the SAME run: served from cache, no new scan.
        assert measure(90)[0]["alt"] == 12.0
        assert calls["n"] == 1, "a measured column is never re-observed"
    finally:
        for p in patches:
            p.stop()

    # A FRESH run — new measure, same files — must resume from the checkpoint.
    (measure2, _r, _a), patches2, _ = _orient_measure_fixture(tmp_path, 20.0, scan)
    try:
        assert measure2(90)[0]["alt"] == 12.0
        assert calls["n"] == 1, "the rerun re-observed a column the checkpoint already held"
    finally:
        for p in patches2:
            p.stop()


def test_a_night_checkpoint_is_rejudged_by_the_current_detector(tmp_path):
    """Judge is code, data is data. The stored verdict is what an OLD detector
    thought; the stored profile is what the sky did. Re-judging on load turned
    two wrong verdicts into right ones the night this was built, without
    re-observing either column.
    """
    import json as _json

    # A profile with an unmistakable persistent edge at 45 -> 40, stored with
    # the WRONG verdict, as the buggy detector wrote it that night.
    profile = [[60, 900.0], [55, 950.0], [50, 1000.0], [45, 1050.0],
               [40, 400.0], [35, 380.0], [30, 360.0]]  # fmt: skip
    ckpt = tmp_path / "out_fiducials.jsonl"
    ckpt.write_text(
        _json.dumps(
            {
                "az": 120,
                "verdict": "blocked",
                "alt": None,
                "ceiling": 60,
                "channel": "star4800",
                "profile": profile,
            }
        )
        + "\n"
    )

    def scan(*a, **k):
        raise AssertionError("re-judging must not touch the scope")

    (measure, _reach, _args), patches, _ = _orient_measure_fixture(tmp_path, -30.0, scan)
    try:
        got = measure(120)
        assert got is not None, "the re-judged column must be served"
        edge, ceiling, _unc = got
        assert (
            edge is not None and edge["alt"] == 42.5
        ), f"blocked -> edge under the current detector, at the bracket midpoint; got {edge}"
    finally:
        for p in patches:
            p.stop()


def test_at_night_the_planner_never_offers_a_near_pole_column(tmp_path):
    """az 0 cost 206 seconds and a connection reset on 2026-08-06; the walk
    crosses declinations where RA cannot converge. Day keeps them: daytime
    sweeps have measured az 0, and the difference is not yet understood."""
    (m, reach_night, _a), patches, _ = _orient_measure_fixture(
        tmp_path, -30.0, lambda *a, **k: None
    )
    try:
        assert not reach_night(0), "due north at night must not be a candidate"
        assert not reach_night(5) and not reach_night(355)
        assert reach_night(20), "the zone is 8 degrees, not a hemisphere"
    finally:
        for p in patches:
            p.stop()
    (m2, reach_day, _a2), patches2, _ = _orient_measure_fixture(
        tmp_path, 20.0, lambda *a, **k: None
    )
    try:
        assert reach_day(0), "daytime keeps az 0 until the difference is understood"
    finally:
        for p in patches2:
            p.stop()


def test_the_night_detector_reads_real_profiles_the_way_the_sky_did():
    """Six real columns from 2026-08-06, each checked against an independent source.

    The fixture IS the data (E-11): raw16 medians from the star-mode imaging
    channel, walked live, with the evening telescope sweep (a different night,
    a different channel) as ground truth. The six cover every night regime met
    so far: clean cliffs on shallow and steep sections, a canopy column rough
    from the very top, and a glare column that brightens to the floor and must
    be refused rather than guessed at (M-19).

    Two rules in `night_find_edge` exist because live columns broke their
    absence, and each has a fixture that fails without it:
      * only NEGATIVE steps are roughness — the skyglow gradient accelerates
        toward the horizon, and bounding step magnitude called az 120 and 240
        "blocked" across textbook edges
      * an edge's drop is never recovered — a transient dark sample 52.5
        degrees up az 325 fired as an edge while the real answer (canopy,
        blocked) sat in the roughness the transient rule then has to preserve
    """
    import json

    with open(
        os.path.join(os.path.dirname(__file__), "data", "night_profiles_2026_08_07.json")
    ) as fh:
        fixtures = json.load(fh)

    from terminus.sweep import night_find_edge

    assert len(fixtures) == 6
    for az, fx in sorted(fixtures.items(), key=lambda kv: int(kv[0])):
        profile = [(a, lum) for a, lum in fx["profile"]]
        idx, verdict = night_find_edge(profile)
        want_verdict, want_alt = fx["expect"]
        assert (
            verdict == want_verdict
        ), f"az {az}: {verdict!r}, want {want_verdict!r} ({fx['ground_truth']})"
        if want_alt is not None:
            assert idx is not None and profile[idx][0] == want_alt, (
                f"az {az}: edge above {profile[idx][0] if idx is not None else None}, "
                f"want {want_alt} ({fx['ground_truth']})"
            )


def test_a_second_imaging_death_costs_one_column_not_the_night(tmp_path):
    """The client reopens the socket once on its own; when that also fails the
    scope has torn down the star session. The column gets a view restart and
    one more try; a further failure is checkpointed and the LOOP CONTINUES —
    a crashed run needing a manual rerun is the exact loss terminus-58 removes.
    Reproduced by review with a flaky-scope mock before this test existed."""
    import json as _json

    calls = {"n": 0}

    def scan(*a, **k):
        calls["n"] += 1
        raise ConnectionError("imaging socket closed")

    (measure, _r, args), patches, _ = _orient_measure_fixture(tmp_path, -30.0, scan)
    try:
        got = measure(120)
        assert got is None, "a dead column yields no constraint, not an exception"
        assert calls["n"] == 2, "the column gets exactly one view-restart retry"
        lines = [_json.loads(x) for x in open(tmp_path / "out_fiducials.jsonl")]
        assert (
            lines and lines[-1]["verdict"] == "failed"
        ), "the attempt must be on disk with its reason"
        # and the loop is still alive for the next column
        assert measure(130) is None
        assert calls["n"] == 4
    finally:
        for p in patches:
            p.stop()

    # THE RECOVERY ITSELF CAN FAIL WITH A DIFFERENT TYPE. stop_view/start_view
    # route through client.call, which raises SeestarError when re-auth fails
    # after a reconnect — a scope that tore down the star session plausibly
    # took the control channel with it. Catching only the socket types
    # reintroduced "the same bug one exception class over" (the daytime
    # sweep's own comment); review reproduced it against the real client.
    from terminus.client import SeestarError

    def scan_control_death(*a, **k):
        raise SeestarError("reconnected but authentication failed")

    (measure2, _r2, _a2), patches2, _ = _orient_measure_fixture(tmp_path, -30.0, scan_control_death)
    try:
        got = measure2(140)  # must not raise
        assert got is None
        lines = [_json.loads(x) for x in open(tmp_path / "out_fiducials.jsonl")]
        assert lines[-1]["verdict"] == "failed" and lines[-1]["az"] == 140
    finally:
        for p in patches2:
            p.stop()


def test_a_night_scan_skips_the_pole_band_sample_and_keeps_the_column():
    """I-16, the per-sample half: one lost altitude is recoverable, a lost
    column is not. az 0's walk crosses declinations where RA cannot converge;
    the failing sample is skipped and the profile keeps its edge."""
    from unittest.mock import MagicMock

    from terminus.sweep import Pointer, PointingError, Sky, scan_horizon_night

    class NightSky(Sky):
        def sun(self, when=None):
            return 270.0, -30.0

    sky = NightSky(39.7917, -104.894, 1600)
    sc = MagicMock()
    # A clean skyglow rise with a persistent cliff between 30 and 25.
    levels = {60: 860, 55: 900, 50: 950, 45: 1000, 40: 1060, 35: 1130,
              30: 1250, 25: 700, 20: 680, 15: 660, 10: 640, 5: 620, 0: 600}  # fmt: skip
    at = {"alt": None}
    ptr = Pointer(sc, sky, 30, 5, False)

    def point_to(az, alt):
        if abs(alt - 45.0) < 0.6:
            raise PointingError("pole band: RA will not converge")
        at["alt"] = alt
        return az, alt

    ptr.point_to = point_to
    sc.capture_raw16_median = lambda **k: float(
        levels[min(levels, key=lambda a: abs(a - at["alt"]))]
    )

    alt, status, typ, profile = scan_horizon_night(ptr, sc, 0.0, 0, 60, 5.0, 1.5)
    sampled = [a for a, _ in profile]
    assert 45.0 not in sampled, "the pole-band sample must be skipped, not fought"
    assert len(sampled) == 12, "and only that sample is lost"
    assert status.startswith("edge"), f"the column survives its gap: {status}"
    assert 25 <= alt <= 30, f"edge in the right bracket, got {alt}"


def test_the_night_detector_refuses_what_it_cannot_confirm():
    """The boundary of the data is not the boundary of the sky.

    A drop on the FINAL sample has nothing after it to confirm persistence, so
    it must never fire — az 175 ended 1936 -> dark at the floor and the first
    version called that an edge at 3.75 degrees, eighteen degrees below the
    real horizon. And fewer than three samples is not a profile.
    """
    from terminus.sweep import night_find_edge

    idx, verdict = night_find_edge([(60, 900.0), (55, 950.0), (50, 1000.0), (45, 400.0)])
    assert idx is None and verdict == "open", "a final-sample drop is unconfirmable"

    idx, verdict = night_find_edge([(60, 900.0), (55, 400.0)])
    assert idx is None and verdict == "short"


def test_a_column_at_its_ceiling_is_a_bound_however_it_is_typed():
    """M-09, applied to the masks that are actually on disk.

    A sweep with a 60 degree ceiling cannot report a horizon above 60, so a
    column reading exactly 60.0 means the search ran out of sky — not that the
    horizon is at 60. The 2026-08-03 evening masks predate the explicit `bound`
    field and record four such columns as ordinary edges; read that way the fit
    scores them TWO-sided, so a photo horizon genuinely above the ceiling is
    penalised for being too high.
    """
    from terminus.orient import from_mask

    mask = {
        10: {"alt": 60.0, "type": "structure"},  # sitting on the ceiling
        20: {"alt": 59.5, "type": "structure"},  # close, but a real measurement
        30: {"alt": 12.0, "type": "tree"},
    }
    fids = {int(f.az): f for f in from_mask(mask, ceiling=60.0)}

    assert fids[10].bound, "a column at the ceiling is a bound, whatever its type says"
    assert not fids[20].bound, "half a degree of headroom is still a measurement"
    assert not fids[30].bound

    # Without a ceiling there is nothing to compare against and nothing to infer.
    loose = {int(f.az): f for f in from_mask(mask)}
    assert not loose[10].bound, "no ceiling given, no inference possible"


def test_both_ways_round_the_ra_circle_are_offered():
    """There are always two, and only one may be clear.

    `wrap_ra` gives the SHORT way, and the old check only ever considered that
    one — so a slew whose short route grazes the Sun was refused outright, with
    no way to express "go the other way round". Measured on 2026-08-05 the long
    way was the worse of the two, which is exactly why it has to be evaluated
    rather than assumed either way.

    The long way is split into legs so a single goto cannot quietly shortcut it
    back to the short way, which would silently drive the route that was
    rejected.
    """
    from unittest.mock import MagicMock

    from terminus.sweep import Pointer, Sky, wrap_ra

    sky = Sky(39.7917, -104.894, 1600)
    ptr = Pointer(MagicMock(), sky, 30, 5, True)
    rd0, rd1 = (2.0, 10.0), (14.0, 40.0)

    names = [name for name, _ in ptr.routes(rd0, rd1)]
    assert any(n.endswith("/short") for n in names) and any(n.endswith("/long") for n in names)
    assert len(names) == len(set(names)), "each route offered once"

    for name, wps in ptr.routes(rd0, rd1):
        assert wps[-1][0] % 24.0 == pytest.approx(rd1[0] % 24.0, abs=1e-9), f"{name} misses in RA"
        assert wps[-1][1] == pytest.approx(rd1[1]), f"{name} misses in declination"
        # No single leg may exceed the split, or a goto could take the short way.
        prev = rd0
        for wp in wps:
            assert (
                abs(wrap_ra(wp[0] - prev[0])) <= ptr.MAX_RA_LEG_H + 1e-9
            ), f"{name} has a leg a goto could shortcut"
            prev = wp


def test_the_cheapest_safe_route_wins_and_an_unsafe_one_is_never_chosen():
    """Plan every shape, discard what grazes the Sun, take the shortest of the rest.

    Requiring that ALL shapes be safe is a test no route has to pass once we are
    the one driving. What must never happen is choosing a cheap route that is not
    safe.
    """
    from unittest.mock import MagicMock

    from terminus.sweep import Pointer, Sky

    sky = Sky(39.7917, -104.894, 1600)
    ptr = Pointer(MagicMock(), sky, 30, 5, True)
    rd0, rd1 = (2.0, 10.0), (8.0, 40.0)
    all_routes = ptr.routes(rd0, rd1)

    # Only the most expensive route is safe: it must still be the one chosen.
    costs = {name: ptr.route_cost(rd0, wps) for name, wps in all_routes}
    dearest = max(costs, key=costs.get)
    ptr.route_min_sep = lambda a, w, samples=None, _d=dearest: (
        90.0 if w == dict(all_routes)[_d] else 1.0
    )
    name, wps, cost = ptr.plan_route(rd0, rd1)
    assert name == dearest, f"chose {name} over the only safe route {dearest}"

    # Nothing safe at all is a refusal, not a fallback to the cheapest.
    ptr.route_min_sep = lambda a, w, samples=None: 1.0
    assert ptr.plan_route(rd0, rd1) is None


def test_route_waypoints_are_coordinates_the_mount_can_accept():
    """RA -3.795 was commanded to a real mount on 2026-08-05, three times.

    `routes` built waypoints as `ra0 + dra * t` and never wrapped. The geometry
    was fine — `route_min_sep` wraps before converting — so the SAFETY maths was
    right and the value handed to the mount was not. It reported never arriving,
    the miss counter read three of those as a broken mount, and abandoned a run
    that had already solved its orientation.

    Wrapping is safe only because the legs are split: a step under MAX_RA_LEG_H
    has an unambiguous shortest direction, so wrapping cannot quietly turn a
    deliberate long way round back into the short one. That is asserted here
    too, because the two properties have to hold together or neither is worth
    anything.
    """
    from unittest.mock import MagicMock

    from terminus.sweep import Pointer, Sky, wrap_ra

    ptr = Pointer(MagicMock(), Sky(39.7917, -104.894, 1600), 30, 5, True)
    starts = [(1.0, 20.0), (23.5, 10.0), (12.0, -30.0), (0.1, 5.0)]
    targets = [(22.0, 40.0), (2.0, 60.0), (13.0, -5.0), (23.9, 0.0)]

    for rd0 in starts:
        for rd1 in targets:
            for name, waypoints in ptr.routes(rd0, rd1):
                prev = rd0
                for ra, dec in waypoints:
                    assert 0.0 <= ra < 24.0, f"{name}: RA {ra} is not a coordinate"
                    assert -90.0 <= dec <= 90.0, f"{name}: Dec {dec} is not a coordinate"
                    assert (
                        abs(wrap_ra(ra - prev[0])) <= ptr.MAX_RA_LEG_H + 1e-9
                    ), f"{name}: a leg long enough for a goto to shortcut"
                    prev = (ra, dec)
                assert abs(wrap_ra(waypoints[-1][0] - rd1[0])) < 1e-9, f"{name} misses in RA"
                assert abs(waypoints[-1][1] - rd1[1]) < 1e-9, f"{name} misses in declination"


def test_one_false_bound_cannot_capture_the_fit():
    """The property that matters, and it failed on real hardware.

    az 140 read a clean edge at 26.2 with the sky reference at 49.3, and an hour
    later — same roofline, darker sky — the same column read "blocked above 60".
    That one false bound moved the solved yaw by 164 degrees and the RMS from
    0.18 to 1.63.

    A bound is scored ONE-SIDED: a photo above the ceiling confirms it for free,
    only falling short contradicts it. So a false bound is not a symmetric error
    the robust loss can absorb — it is a lever, and the fit can only reduce that
    residual by rotating the whole sphere. This asserts the damage is bounded.
    """
    import math

    from terminus import guide, orient
    from terminus.orient import Fiducial

    rows = [
        (a, 20.0 + 12.0 * math.sin(math.radians(2 * a)) + 6.0 * math.cos(math.radians(a)), "structure")
        for a in range(0, 360, 5)
    ]  # fmt: skip
    sample = guide.photo_sample(rows)
    truth = {"yaw": 40.0, "pitch": 0.0, "tilt_mag": 0.0, "tilt_dir": 0.0}

    def honest(az):
        phi, raw = orient.native_column(sample, az, truth["yaw"], 0.0, 0.0)
        _, alt = orient.rotate(phi + truth["yaw"], raw, truth["pitch"], 0.0, 0.0)
        return Fiducial(az, float(alt), ceiling=60.0, weight=1.0, sigma=1.0)

    good = [honest(az) for az in (0, 70, 140, 210, 280)]
    clean = orient.fit(good, sample, yaw_step=2.0, tilt_max=6.0, tilt_step=3.0, pitch_range=6.0)
    assert abs(((clean["yaw"] - 40.0 + 180) % 360) - 180) < 3.0, "precondition: the truth is found"

    # Now one column that saw nothing and said "at least 60" instead.
    false_bound = Fiducial(175, 60.0, ceiling=60.0, bound=True, weight=1.0, sigma=1.0)
    poisoned = orient.fit(
        good + [false_bound], sample, yaw_step=2.0, tilt_max=6.0, tilt_step=3.0, pitch_range=6.0
    )
    swing = abs(((poisoned["yaw"] - clean["yaw"] + 180) % 360) - 180)
    assert (
        swing < 15.0
    ), f"one false bound moved the yaw by {swing:.0f} deg; on 2026-08-05 it moved it by 164"


def test_a_bound_needs_contrast_that_stands_clear_of_the_column_s_own_scatter():
    """ "I cannot see a step" is not "there is terrain above the ceiling".

    As the sky falls toward the terrain's own brightness the two overlap, and a
    non-detection stops being evidence of anything. The test is self-calibrating
    rather than a threshold: at a bright reference the gap is enormous and this
    passes trivially; at twilight it cannot be made.
    """
    from unittest.mock import MagicMock

    import numpy as np

    from terminus.sweep import scan_horizon

    def column(sky_ref, values):
        """A column whose brightness cycles through `values` as it descends."""
        ptr = MagicMock()
        ptr.dry = False
        sc = MagicMock()
        seq = iter(values * 40)
        sc.capture_rgb.side_effect = lambda **kw: np.full((8, 8, 3), next(seq), dtype=np.float32)
        return scan_horizon(ptr, sc, 140, 0, 60, 5.0, 1.5, sky_ref)[1]

    # Daylight: terrain at 6 against a reference of 100. Unmistakable.
    assert column(100.0, [6.0, 7.0, 5.0]) == "blocked_above"

    # Twilight: the same terrain, but the sky is now as dark as it is. The gap
    # between the column and half the reference is smaller than the column's own
    # scatter, so no bound may be asserted.
    assert column(7.0, [2.0, 5.0, 3.0]) == "inconclusive"

    # Genuinely dark terrain under a dark sky is still a bound: contrast is what
    # matters, not absolute brightness.
    assert column(7.0, [0.5, 0.6, 0.4]) == "blocked_above"


def test_a_scope_that_stops_responding_is_not_reported_as_a_file_problem(tmp_path):
    """On 2026-08-05 a mid-run timeout was reported as:

        error: could not read or write beside .../photo_mask.yaml: timed out

    Nothing was wrong with that file. `socket.timeout` is an `OSError`, so a
    network fault was caught by whatever file-handling wrapper happened to be
    outermost — sending the operator to inspect a healthy file, at night, with
    the mount possibly mid-slew.
    """
    import socket

    import pytest
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    from terminus.client import Seestar, SeestarError

    # A throwaway key, because the interop key exists only on the dev box and
    # this test previously asserted what that box happens to satisfy (E-04):
    # on a host without the key — CI — the constructor failed on the pem
    # instead and the error named a file, not the unreachable host.
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = tmp_path / "key.pem"
    pem.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )

    # A closed local port refuses immediately: the same OSError family as the
    # timeout that caused this, without spending ten seconds waiting for one.
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()

    with pytest.raises(SeestarError) as exc:
        Seestar("127.0.0.1", str(pem))
    message = str(exc.value)
    assert "127.0.0.1" in message, "it must name what it could not reach"
    assert "yaml" not in message and "could not write" not in message, "not a file problem"
    assert port is not None


def test_a_twilight_day_verdict_is_discarded_on_resume_and_refused_live(tmp_path):
    """M-08 wired into orient: below DAY_REF_FLOOR the day judge invents edges.

    Live 2026-08-07: three columns in a treeline the sweeps put above 60 deg
    came back as confident 13.8-15.0 deg edges at sky_ref 21.3. Two teeth, both
    here: a cached day edge recorded below the floor is DISCARDED on resume so
    the current channel re-measures it (serving it would feed the night fit the
    exact numbers the floor refuses), and a live day column under a floored
    reference is checkpointed failed without ever slewing.
    """
    import json
    import math
    from types import SimpleNamespace
    from unittest.mock import MagicMock, patch

    from terminus import cli, guide
    from terminus.export import write_mask

    photo = tmp_path / "photo.yaml"
    rows = {
        a: (20.0 + 10.0 * math.sin(math.radians(2 * a)), "structure") for a in range(0, 360, 10)
    }
    write_mask(str(photo), rows, [], {"oriented": False, "lat": 39.79, "lon": -104.89})

    out = tmp_path / "solved.yaml"
    ckpt = tmp_path / "solved_fiducials.jsonl"
    ckpt.write_text(
        json.dumps({"az": 320, "verdict": "edge", "alt": 13.8, "ceiling": 60,
                    "channel": "scenery", "sky_ref": 21.3, "profile": []}) + "\n"
        + json.dumps({"az": 90, "verdict": "edge", "alt": 6.2, "ceiling": 60,
                      "channel": "scenery", "sky_ref": 245.7, "profile": []}) + "\n"
    )  # fmt: skip

    args = SimpleNamespace(
        mask=str(photo), out=str(out), replay=None, fiducials=None, seed=3, max_columns=4,
        window=3, yaw_tol=1.0, uncertainty=None, min_headroom=None, dry_run=False,
        frames=None, stop_above_sun_alt=None,
    )  # fmt: skip
    cfg = {
        "site": {"lat": 39.79, "lon": -104.89, "elev_m": 1600},
        "sweep": {"az_step": 10, "alt_min": 0, "alt_max": 60, "alt_tol": 1.5,
                  "sun_cone_deg": 30, "slew_step_deg": 5, "clear_thresh": 0.6},
    }  # fmt: skip

    import pytest

    from terminus.export import MaskError

    sc = MagicMock()
    sc.is_eq_mode.return_value = True
    scans = MagicMock()
    with (
        patch.object(cli.Sky, "sun", lambda self: (297.0, -5.0)),  # terminus-63: day channel
        patch.object(cli, "scan_horizon", scans),
        patch.object(cli, "sky_reference", return_value=21.3),  # a floored evening
        patch.object(cli, "Pointer"),
        patch.object(cli, "column_touches_sun", return_value=False),
        patch.object(cli, "is_stowed", return_value=False),
        patch.object(
            guide,
            "fit",
            return_value={
                "yaw": 10.0,
                "pitch": 0.0,
                "tilt_mag": 0.0,
                "tilt_dir": 0.0,
                "rms": 0.1,
                "n": 4,
                "n_bound": 0,
                "residuals": {0.0: 0.1},
                "fiducials": [],
            },
        ),
    ):
        # Only the one healthy cached column survives, so the run rightly
        # refuses to fit — the floor's job is exactly to shrink a twilight run
        # to what was actually measurable.
        with pytest.raises(MaskError, match="needs four"):
            cli.cmd_orient(sc, cfg, args)

    lines = [json.loads(x) for x in ckpt.read_text().splitlines()]
    floored = [d for d in lines if d.get("error", "").startswith("day reference")]
    assert floored, "live columns under a floored reference must checkpoint as failed"
    assert not scans.called, "and must never be scanned into a plausible wrong number"
    retried = [d for d in lines if d["az"] == 320]
    assert (
        len(retried) > 1
    ), "the cached twilight edge must be discarded and re-attempted, not served"


def test_the_reference_is_allowed_to_fall_and_the_floor_then_catches_it(tmp_path):
    """Round 1's P1: within a run sky_ref only ratcheted up, so the floor gate
    could never fire in the exact bright-into-dark run it was built for — the
    gate read the daylight 250 while the sky read 12. The day branch now
    refreshes the reference past SKY_REF_MAX_AGE, REPLACING it as run_sweep
    does, and the gate reads the fresh value.
    """
    import json
    import math
    from types import SimpleNamespace
    from unittest.mock import MagicMock, patch

    import pytest

    from terminus import cli, guide
    from terminus.export import MaskError, write_mask

    photo = tmp_path / "photo.yaml"
    rows = {
        a: (20.0 + 10.0 * math.sin(math.radians(2 * a)), "structure") for a in range(0, 360, 10)
    }
    write_mask(str(photo), rows, [], {"oriented": False, "lat": 39.79, "lon": -104.89})

    out = tmp_path / "solved.yaml"
    args = SimpleNamespace(
        mask=str(photo), out=str(out), replay=None, fiducials=None, seed=3, max_columns=4,
        window=3, yaw_tol=1.0, uncertainty=None, min_headroom=None, dry_run=False,
        frames=None, stop_above_sun_alt=None,
    )  # fmt: skip
    cfg = {
        "site": {"lat": 39.79, "lon": -104.89, "elev_m": 1600},
        "sweep": {"az_step": 10, "alt_min": 0, "alt_max": 60, "alt_tol": 1.5,
                  "sun_cone_deg": 30, "slew_step_deg": 5, "clear_thresh": 0.6},
    }  # fmt: skip

    sc = MagicMock()
    sc.is_eq_mode.return_value = True
    scans = MagicMock()
    with (
        patch.object(cli.Sky, "sun", lambda self: (297.0, -5.0)),  # terminus-63
        patch.object(cli, "SKY_REF_MAX_AGE", -1.0),  # every column finds the seed stale
        patch.object(cli, "scan_horizon", scans),
        # Daylight at the seed, 12 counts at the refresh: the run has gone dark.
        patch.object(cli, "sky_reference", MagicMock(side_effect=[250.0] + [12.0] * 60)),
        patch.object(cli, "Pointer"),
        patch.object(cli, "column_touches_sun", return_value=False),
        patch.object(cli, "is_stowed", return_value=False),
        patch.object(guide, "fit", return_value={
            "yaw": 10.0, "pitch": 0.0, "tilt_mag": 0.0, "tilt_dir": 0.0, "rms": 0.1,
            "n": 4, "n_bound": 0, "residuals": {0.0: 0.1}, "fiducials": [],
        }),  # fmt: skip
    ):
        with pytest.raises(MaskError, match="needs four"):
            cli.cmd_orient(sc, cfg, args)

    assert not scans.called, (
        "with the refreshed reference under the floor, no column may be scanned "
        "into a plausible wrong number — the old ratchet let every one through"
    )
    lines = [json.loads(x) for x in (tmp_path / "solved_fiducials.jsonl").read_text().splitlines()]
    assert any(
        d.get("error", "").startswith("day reference") for d in lines
    ), "the floored columns must be checkpointed as failed for a night re-attempt"


def test_a_failed_reference_refresh_backs_off_instead_of_retrying_every_column(tmp_path):
    """Round 2's P2: the refresh fix forgot run_sweep's failure backoff.

    On failure the clock must advance anyway — without it the very next column
    finds the reference stale again, and every remaining day column pays a
    failed anti-Sun slew before being measured, for the rest of the run. The
    discriminator: with backoff the anti-Sun goto fails a bounded number of
    times (the seed plus one refresh); without it, once per column.
    """
    import math
    import time as real_time
    from types import SimpleNamespace
    from unittest.mock import MagicMock, patch

    from terminus import cli, guide
    from terminus.export import MaskError, write_mask
    from terminus.sweep import PointingError

    class FakeTime:
        def __init__(self, t):
            self.t = t

        def time(self):
            return self.t

        def sleep(self, dt):
            self.t += dt

        def strftime(self, fmt):
            return real_time.strftime(fmt)

    photo = tmp_path / "photo.yaml"
    rows = {
        a: (20.0 + 10.0 * math.sin(math.radians(2 * a)), "structure") for a in range(0, 360, 10)
    }
    write_mask(str(photo), rows, [], {"oriented": False, "lat": 39.79, "lon": -104.89})

    out = tmp_path / "solved.yaml"
    args = SimpleNamespace(
        mask=str(photo), out=str(out), replay=None, fiducials=None, seed=3, max_columns=3,
        window=3, yaw_tol=1.0, uncertainty=None, min_headroom=None, dry_run=False,
        frames=None, stop_above_sun_alt=None,
    )  # fmt: skip
    cfg = {
        "site": {"lat": 39.79, "lon": -104.89, "elev_m": 1600},
        "sweep": {"az_step": 10, "alt_min": 0, "alt_max": 60, "alt_tol": 1.5,
                  "sun_cone_deg": 30, "slew_step_deg": 5, "clear_thresh": 0.6},
    }  # fmt: skip

    sc = MagicMock()
    sc.is_eq_mode.return_value = True
    prof = [(float(a), 80.0 if a > 25 else 8.0) for a in range(60, -1, -5)]
    clock = FakeTime(20_000.0)
    anti_sun = {"n": 0}

    # EVERY anti-Sun goto fails tonight — the seed and any refresh alike.
    # sky_ref still gets set by the first column's own profile peak, so the
    # refresh machinery arms, attempts once, and must then back off.
    def flaky_point_to(az, alt):
        if alt == 75.0:
            anti_sun["n"] += 1
            raise PointingError("anti-Sun target unreachable tonight")
        return az, alt

    ptr = MagicMock()
    ptr.point_to = MagicMock(side_effect=flaky_point_to)

    def cheap_scan(*a, **k):
        clock.t += 10.0  # columns far cheaper than SKY_REF_MAX_AGE
        return (25.0, "edge(rel 3.0)", "tree", prof)

    with (
        patch.object(cli, "time", clock),
        patch.object(cli.Sky, "sun", lambda self: (297.0, -5.0)),  # terminus-63
        patch.object(cli, "scan_horizon", side_effect=cheap_scan),
        patch.object(cli, "sky_reference", return_value=250.0),
        patch.object(cli, "Pointer", return_value=ptr),
        patch.object(cli, "column_touches_sun", return_value=False),
        patch.object(cli, "is_stowed", return_value=False),
        patch.object(cli, "SKY_REF_MAX_AGE", 100.0),
        patch.object(guide, "fit", return_value={
            "yaw": 10.0, "pitch": 0.0, "tilt_mag": 0.0, "tilt_dir": 0.0, "rms": 0.1,
            "n": 4, "n_bound": 0, "residuals": {0.0: 0.1}, "fiducials": [],
        }),  # fmt: skip
    ):
        try:
            cli.cmd_orient(sc, cfg, args)
        except MaskError:
            pass

    # Failed seed (1) + one armed refresh attempt (2), then backoff holds for
    # SKY_REF_MAX_AGE across the remaining cheap columns. Without the backoff,
    # the count grows by one per column after the ratchet arms the refresh.
    assert 1 <= anti_sun["n"] <= 2, (
        f"a failed refresh must back off for SKY_REF_MAX_AGE, not retry every "
        f"column: {anti_sun['n']} anti-Sun attempts"
    )


def test_an_unverified_exposure_lock_blocks_the_next_column(tmp_path):
    """Round 1's other P1: a failed re-lock left later columns silently
    measuring under auto-exposure (M-06's exact sin). The lock is now a
    precondition: after any relock failure, every day column re-establishes it
    or is refused — never measured unverified.
    """
    import math
    from types import SimpleNamespace
    from unittest.mock import MagicMock, patch

    from terminus import cli, guide
    from terminus.client import SeestarError
    from terminus.export import MaskError, write_mask

    photo = tmp_path / "photo.yaml"
    rows = {
        a: (20.0 + 10.0 * math.sin(math.radians(2 * a)), "structure") for a in range(0, 360, 10)
    }
    write_mask(str(photo), rows, [], {"oriented": False, "lat": 39.79, "lon": -104.89})

    out = tmp_path / "solved.yaml"
    args = SimpleNamespace(
        mask=str(photo), out=str(out), replay=None, fiducials=None, seed=3, max_columns=3,
        window=3, yaw_tol=1.0, uncertainty=None, min_headroom=None, dry_run=False,
        frames=None, stop_above_sun_alt=None,
    )  # fmt: skip
    cfg = {
        "site": {"lat": 39.79, "lon": -104.89, "elev_m": 1600},
        "sweep": {"az_step": 10, "alt_min": 0, "alt_max": 60, "alt_tol": 1.5,
                  "sun_cone_deg": 30, "slew_step_deg": 5, "clear_thresh": 0.6},
    }  # fmt: skip

    sc = MagicMock()
    sc.is_eq_mode.return_value = True
    prof = [(float(a), 80.0 if a > 25 else 8.0) for a in range(60, -1, -5)]
    scan_calls = {"n": 0, "locks_at_scan": []}

    def scans(*a, **k):
        scan_calls["n"] += 1
        scan_calls["locks_at_scan"].append(relocks["n"])
        if scan_calls["n"] == 1:
            raise SeestarError("RTSP capture failed: timed out")
        return (25.0, "edge(rel 3.0)", "tree", prof)

    # The retry's relock fails, the next column's re-establishment fails, the
    # one after that heals.
    relocks = {"n": 0}

    def flaky_lock(*a, **k):
        relocks["n"] += 1
        # Call 1 is the unguarded run-start lock: it succeeds. The RETRY's
        # relock (2) and the next column's re-establishment (3) fail; 4 heals.
        if relocks["n"] in (2, 3):
            raise SeestarError("lock refused" if relocks["n"] == 2 else "still dead")

    restarts = MagicMock(side_effect=flaky_lock)
    with (
        patch.object(cli.Sky, "sun", lambda self: (297.0, -5.0)),  # terminus-63
        patch.object(cli, "scan_horizon", side_effect=scans),
        patch.object(cli, "_start_locked", restarts),
        patch.object(cli, "sky_reference", return_value=80.0),
        patch.object(cli, "Pointer"),
        patch.object(cli, "column_touches_sun", return_value=False),
        patch.object(cli, "is_stowed", return_value=False),
        patch.object(guide, "fit", return_value={
            "yaw": 10.0, "pitch": 0.0, "tilt_mag": 0.0, "tilt_dir": 0.0, "rms": 0.1,
            "n": 4, "n_bound": 0, "residuals": {0.0: 0.1}, "fiducials": [],
        }),  # fmt: skip
    ):
        try:
            cli.cmd_orient(sc, cfg, args)
        except MaskError:
            pass  # too few columns is a legitimate outcome of the refusals

    assert (
        restarts.call_count >= 4
    ), f"the lock must be re-attempted before each day column, saw {restarts.call_count}"
    # THE INVARIANT: no scan while the lock is unverified. Relock calls 2 and 3
    # fail, call 4 heals — so every scan after the first (which triggered the
    # failure) must observe the healed lock. A scan seeing relock state 2 or 3
    # is a column measured under auto-exposure.
    assert all(n >= 4 for n in scan_calls["locks_at_scan"][1:]), (
        f"a column measured under an unverified lock: relock states at scan "
        f"time were {scan_calls['locks_at_scan']}"
    )
    assert len(scan_calls["locks_at_scan"]) >= 2, "the healed run must resume measuring"


def test_a_dead_scenery_stream_costs_a_retry_not_the_run(tmp_path):
    """Live 2026-08-07: the RTSP stream died at column ten and killed the orient.

    Nine measured columns sat stranded in the checkpoint while the command
    exited with a raw error — an observing window spent for no fit. The day
    branch now recovers like the night branch always did: cycle the view
    (which re-locks the exposure, M-06), retry the column once, and only a
    second failure gives the column up — as a checkpointed failure, not a
    crashed run (D-12).
    """
    import math
    from types import SimpleNamespace
    from unittest.mock import MagicMock, patch

    from terminus import cli, guide
    from terminus.client import SeestarError
    from terminus.export import write_mask

    photo = tmp_path / "photo.yaml"
    rows = {
        a: (20.0 + 10.0 * math.sin(math.radians(2 * a)), "structure") for a in range(0, 360, 10)
    }
    write_mask(str(photo), rows, [], {"oriented": False, "lat": 39.79, "lon": -104.89})

    out = tmp_path / "solved.yaml"
    args = SimpleNamespace(
        mask=str(photo), out=str(out), replay=None, fiducials=None, seed=3, max_columns=5,
        window=3, yaw_tol=1.0, uncertainty=None, min_headroom=None, dry_run=False,
        frames=None, stop_above_sun_alt=None,
    )  # fmt: skip
    cfg = {
        "site": {"lat": 39.79, "lon": -104.89, "elev_m": 1600},
        "sweep": {"az_step": 10, "alt_min": 0, "alt_max": 60, "alt_tol": 1.5,
                  "sun_cone_deg": 30, "slew_step_deg": 5, "clear_thresh": 0.6},
    }  # fmt: skip

    sc = MagicMock()
    sc.is_eq_mode.return_value = True
    prof = [(float(a), 80.0 if a > 25 else 8.0) for a in range(60, -1, -5)]
    calls = {"n": 0}

    def dying_scan(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise SeestarError("RTSP capture failed (is scenery view running?): timed out")
        return (25.0, "edge(rel 3.0)", "tree", prof)

    restarts = MagicMock()
    with (
        patch.object(cli.Sky, "sun", lambda self: (297.0, -5.0)),  # terminus-63: day channel
        patch.object(cli, "scan_horizon", side_effect=dying_scan),
        patch.object(cli, "_start_locked", restarts),
        patch.object(cli, "sky_reference", return_value=80.0),
        patch.object(cli, "Pointer"),
        patch.object(cli, "column_touches_sun", return_value=False),
        patch.object(cli, "is_stowed", return_value=False),
        patch.object(
            guide,
            "fit",
            return_value={
                "yaw": 10.0,
                "pitch": 0.0,
                "tilt_mag": 0.0,
                "tilt_dir": 0.0,
                "rms": 0.1,
                "n": 4,
                "n_bound": 0,
                "residuals": {0.0: 0.1},
                "fiducials": [],
            },
        ),
    ):
        cli.cmd_orient(sc, cfg, args)

    assert calls["n"] >= 2, "the failed column must be retried, not abandoned"
    restarts.assert_called_with(sc, cfg["sweep"]), (
        "the retry must cycle the view with the scope and the sweep config, in "
        "that order - round 1 found swapped arguments survive a bare .called"
    )
    assert out.exists(), "the run must survive to write its mask"


def test_orient_keeps_what_it_measured(tmp_path):
    """A column costs minutes of clear sky; discarding it after one use is waste.

    `cmd_orient` passed `frames_dir=None` and dropped the returned profile, so
    the loop that `--replay` was built around was the one command saving nothing
    to replay. It also made terminus-58 undiagnosable: the column that poisoned
    the fit left no record of what it actually saw.
    """
    import json
    import math
    from types import SimpleNamespace
    from unittest.mock import MagicMock, patch

    from terminus import cli, guide
    from terminus.export import write_mask

    photo = tmp_path / "photo.yaml"
    rows = {
        a: (20.0 + 10.0 * math.sin(math.radians(2 * a)), "structure") for a in range(0, 360, 10)
    }
    write_mask(str(photo), rows, [], {"oriented": False, "lat": 39.79, "lon": -104.89})

    out = tmp_path / "solved.yaml"
    args = SimpleNamespace(
        mask=str(photo), out=str(out), replay=None, seed=3, max_columns=5, window=3,
        yaw_tol=1.0, uncertainty=None, min_headroom=None, dry_run=False, frames=None,
        stop_above_sun_alt=None,
    )  # fmt: skip
    cfg = {
        "site": {"lat": 39.79, "lon": -104.89, "elev_m": 1600},
        "sweep": {"az_step": 10, "alt_min": 0, "alt_max": 60, "alt_tol": 1.5,
                  "sun_cone_deg": 30, "slew_step_deg": 5, "clear_thresh": 0.6},
    }  # fmt: skip

    sc = MagicMock()
    sc.is_eq_mode.return_value = True
    prof = [(float(a), 80.0 if a > 25 else 8.0) for a in range(60, -1, -5)]
    from terminus.sweep import Sky as _Sky

    with (
        patch.object(cli, "scan_horizon", return_value=(25.0, "edge(rel 3.0)", "tree", prof)),
        patch.object(cli, "sky_reference", return_value=80.0),
        patch.object(cli, "Pointer"),
        patch.object(cli, "column_touches_sun", return_value=False),
        # PINNED. Without this the test's behaviour depends on when the suite
        # runs: after dark, orient auto-selects the night measurement path and
        # these mocks no longer reach it. Found the honest way — the suite ran
        # at 23:30 and two tests flipped (terminus-63, E-04).
        patch.object(_Sky, "sun", return_value=(180.0, 20.0)),
        patch.object(
            guide,
            "fit",
            return_value={
                "yaw": 10.0,
                "pitch": 0.0,
                "tilt_mag": 0.0,
                "tilt_dir": 0.0,
                "rms": 0.1,
                "n": 4,
                "n_bound": 0,
                "residuals": {0.0: 0.1},
                "fiducials": [],
            },
        ),
    ):
        cli.cmd_orient(sc, cfg, args)

    saved = tmp_path / "solved_profiles.json"
    assert saved.exists(), "the columns it measured must survive the run"
    data = json.loads(saved.read_text())
    assert data, "and must not be empty"
    # And what it saved is the shape --replay consumes.
    measure = guide.replay(data)
    assert measure(int(next(iter(data)))) is not None


def test_a_tube_inside_the_cone_can_still_be_moved_out():
    """Devon asked whether the mount could get trapped inside its own banned wedge.

    It could, completely. `point_to` Sun-checked the CURRENT pointing and raised
    before doing anything, so a tube 5.1 degrees from the Sun refused every slew
    — including one straight away from it. The refusal even named the current
    position rather than the target. That is not caution, it is the software
    welding the instrument in the one place it must not stay.

    And it arrives on its own: the Sun moves 15 degrees an hour, so a tube parked
    outside the cone and left alone is overtaken.
    """
    from unittest.mock import MagicMock, patch

    from terminus.sweep import Pointer, Sky, ang_sep

    class FixedSky(Sky):
        def sun(self):
            return 270.0, 20.0

    sky = FixedSky(39.7917, -104.894, 1600)
    trapped = (265.0, 22.0)
    assert ang_sep(*trapped, 270.0, 20.0) < 30.0, "precondition: it is inside the cone"

    sc = MagicMock()
    landed = {"rd": sky.altaz_to_radec(*trapped)}
    sc.equ_coord.side_effect = lambda: landed["rd"]
    ptr = Pointer(sc, sky, 30, 5)

    legs = []

    def fake_goto(ra, dec, settle):
        legs.append((ra, dec))
        landed["rd"] = (ra, dec)

    with patch.object(Pointer, "_goto_wait", side_effect=fake_goto):
        ptr.point_to(90.0, 45.0)

    assert legs, "it must move rather than refuse"
    final = sky.radec_to_altaz(*landed["rd"])
    assert (
        ang_sep(*final, 270.0, 20.0) >= 30.0
    ), f"ended at {final} — still {ang_sep(*final, 270.0, 20.0):.1f} deg from the Sun"


def test_the_way_out_is_a_turn_not_a_descent():
    """Devon's rule, and it is provable rather than heuristic.

    From ANY trapped pointing at least one azimuth direction increases
    separation from the Sun. Verified exhaustively here; there is no case where
    neither helps. The only pointings where turning changes nothing are near the
    zenith, and those are already tens of degrees clear.

    Better than the descent this replaced, because it asks the mount for nothing
    it has not been seen to do — how far below the horizon it can point is still
    unknown, and an escape that depends on an unmeasured limit is not one.
    """
    from unittest.mock import MagicMock

    from terminus.sweep import Pointer, Sky, ang_sep

    for sun_alt in (5.0, 20.0, 40.0, 60.0):

        class FixedSky(Sky):
            def sun(self, _s=sun_alt):
                return 270.0, _s

        ptr = Pointer(MagicMock(), FixedSky(39.79, -104.89, 1600), 30, 5, True)
        trapped = turned = 0
        for az in range(0, 360, 7):
            for alt in range(-20, 86, 7):
                if ang_sep(az, alt, 270.0, sun_alt) >= ptr.cone:
                    continue
                trapped += 1

                # One direction must always help.
                step = max(1.0, float(ptr.slew_step))
                here = ang_sep(az, alt, 270.0, sun_alt)
                cw = ang_sep((az + step) % 360, alt, 270.0, sun_alt)
                ccw = ang_sep((az - step) % 360, alt, 270.0, sun_alt)
                assert (
                    max(cw, ccw) > here or here > 60.0
                ), f"neither turn helps at az {az} alt {alt}, {here:.1f} deg out"

                target = ptr.escape_target(az, alt)
                assert ang_sep(*target, 270.0, sun_alt) >= ptr.cone + ptr.ESCAPE_MARGIN_DEG - 1e-9
                if abs(target[1] - alt) < 1e-9:
                    turned += 1

        assert trapped, f"Sun at {sun_alt}: the probe found nothing trapped"
        if sun_alt <= 40.0:
            assert (
                turned == trapped
            ), f"Sun at {sun_alt}: {trapped - turned} of {trapped} needed more than a turn"
        else:
            # A high summer Sun leaves near-zenith pointings where azimuth barely
            # moves the tube. Those fall back to the descent, which is what the
            # corridor depth is for.
            assert turned > trapped * 0.8


def test_an_escape_that_succeeds_is_verified_against_where_the_tube_ACTUALLY_is():
    """The last thing `escape` does is check it worked. Nothing tested that.

    Both of the other escape tests raise before the final check is reached, and
    both mock `current_azalt` as a constant — so they could not tell a successful
    escape from a broken one, because the mock keeps reporting the trapped
    starting position whatever the mount was told.

    This one moves. `current_azalt` reflects each `_goto_wait`, so the turn
    genuinely walks the tube out of the cone and the final verification runs on a
    real answer. Then the same scenario is run with the mount IGNORING commands —
    which is the fault that matters, since a goto that quietly fails to arrive
    voids every Sun-safety guarantee: the caller believes the scope is where it
    asked and plans the next path from a position the mount never reached.
    """
    from unittest.mock import MagicMock

    import pytest

    from terminus.sweep import Pointer, Sky, SunGuard, ang_sep

    class LowSun(Sky):
        def sun(self, when=None):
            return 90.0, 15.0

    sky = LowSun(39.7917, -104.894, 1600)

    def trapped(obedient):
        sc = MagicMock()
        ptr = Pointer(sc, sky, 30, 5, False)
        at = {"az": 70.0, "alt": 20.0}  # 20 deg from the Sun: inside the cone
        ptr.current_azalt = lambda: (at["az"], at["alt"])

        def goto(ra, dec, *a, **k):
            if obedient:
                az, alt = sky.radec_to_altaz(ra, dec)
                at["az"], at["alt"] = az, alt

        ptr._goto_wait = goto
        return ptr, at

    ptr, at = trapped(obedient=True)
    assert ang_sep(at["az"], at["alt"], *sky.sun()) < ptr.cone, "the premise: it starts trapped"
    out = ptr.escape()
    assert (
        ang_sep(*out, *sky.sun()) >= ptr.cone
    ), f"escape returned {out} which is still inside the cone"
    assert out == (at["az"], at["alt"]), "it must report where the tube IS, not where it aimed"

    # A mount that takes the commands and does not move is the dangerous case,
    # and the only thing standing between it and a false all-clear is that final
    # check. It must refuse rather than return a pointing nobody reached.
    ptr, _at = trapped(obedient=False)
    with pytest.raises(SunGuard, match="Cover the aperture"):
        ptr.escape()


def test_the_escape_descent_actually_steps_the_mount_down():
    """The descent branch executes, rather than being skipped or refused.

    It is the fallback for the case turning cannot solve, and no test had ever
    run its body: the two that reach it are both refused first by the floor
    guard. A wrong step direction or an off-by-one on the loop bound would have
    been caught by nothing here, and only discovered by wasting real goto time.
    """
    from unittest.mock import MagicMock

    from terminus.sweep import Pointer, Sky, ang_sep

    class NoonSun(Sky):
        def sun(self, when=None):
            return 180.0, 80.0

    sky = NoonSun(39.7917, -104.894, 1600)
    sc = MagicMock()
    # safe_depth = (30 + 5) - 80 -> 0, so the corridor is the horizon itself and
    # the descent target is comfortably above the floor.
    ptr = Pointer(sc, sky, 30, 5, False)
    at = {"az": 180.0, "alt": 88.0}  # near the zenith, so turning cannot help
    ptr.current_azalt = lambda: (at["az"], at["alt"])
    steps = []

    def goto(ra, dec, *a, **k):
        az, alt = sky.radec_to_altaz(ra, dec)
        at["az"], at["alt"] = az, alt
        steps.append(round(alt, 1))

    ptr._goto_wait = goto
    out = ptr.escape()

    assert steps, "the descent loop never issued a goto"
    assert steps == sorted(steps, reverse=True), f"the descent must go DOWN, got {steps}"
    assert ang_sep(*out, *sky.sun()) >= ptr.cone, "and it must end clear of the Sun"


def test_an_escape_turn_never_walks_through_the_pole():
    """Every other slew respects MAX_VIA_DEC; the escape turn did not.

    RA is singular near a pole and this mount has already stalled at Dec 89.8,
    which is why `point_to` routes through `avoid_pole` and the over-the-top
    fallback refuses any waypoint past MAX_VIA_DEC. `escape()`'s turn loop
    stepped raw, and turning north at a mid-latitude site climbs in declination
    fast: from az 25 alt 38 at 39.8N, five 5-degree steps reach Dec 85.6. The
    step that finally cleared the Sun cone was the one that crossed the limit —
    stalling the one manoeuvre whose whole job is guaranteeing an exit.
    """
    from unittest.mock import MagicMock

    from terminus.sweep import MAX_VIA_DEC, Pointer, Sky

    class MorningSun(Sky):
        def sun(self, when=None):
            return 45.0, 25.0

    sky = MorningSun(39.7917, -104.894, 1600)
    sc = MagicMock()
    visited = []

    ptr = Pointer(sc, sky, 30, 5, False)
    ptr.current_azalt = lambda: (25.0, 38.0)

    def record(ra, dec, *a, **k):
        visited.append((ra, dec))

    ptr._goto_wait = record
    try:
        ptr.escape()
    except Exception:
        pass  # refusing is a legal outcome; commanding the pole is not

    over = [(ra, dec) for ra, dec in visited if abs(dec) > MAX_VIA_DEC]
    assert not over, f"escape commanded {len(over)} pointing(s) past the pole limit: {over}"


def test_an_escape_that_cannot_reach_its_depth_refuses_instead_of_trying():
    """`corridor_alt` already guards this; `escape_target` did not.

    When turning cannot clear the cone the fallback descends, and the depth it
    wants is `-safe_depth()`. With a wide cone and a high Sun that can be below
    anything this mount has ever been shown to reach. Commanding it anyway is
    the thing `cli._pointer`'s docstring says the design will not do: say
    plainly when the corridor is unavailable rather than issue a slew the mount
    may refuse.
    """
    from unittest.mock import MagicMock

    import pytest

    from terminus.sweep import Pointer, Sky, SunGuard

    class HighSun(Sky):
        def sun(self, when=None):
            return 180.0, 44.0

    sky = HighSun(39.7917, -104.894, 1600)
    sc = MagicMock()
    # A wide cone is a legal config: nothing enforces an upper bound, and an
    # operator may reasonably widen it for margin.
    #
    # Both tube and Sun have to sit near the zenith for turning to be useless —
    # at 1 degree from zenith the far side of the sky is only 47 degrees away,
    # short of the 51 the cone plus margin demands — while the Sun stays low
    # enough that the depth needed to clear it, 7 degrees below the horizon, is
    # past the floor. That is the exact corner escape_target's fallback exists
    # for, and the corner it commanded blind.
    ptr = Pointer(sc, sky, 46, 5, False)
    ptr.current_azalt = lambda: (180.0, 89.0)
    commanded = []
    ptr._goto_wait = lambda ra, dec, *a, **k: commanded.append((ra, dec))

    with pytest.raises(SunGuard, match="below this mount's floor"):
        ptr.escape()
    assert not commanded, "it must refuse before commanding anything, not part way through"


def test_a_sweep_honours_the_measured_below_horizon_floor():
    """terminus-64's config knob was live for `point` and dead for `sweep`.

    `cli._pointer` applied `min_alt_deg`; `run_sweep` built its own Pointer and
    never read it. So the operator's measurement of how far this mount can
    actually point below the horizon was ignored by the one command that runs
    unattended for hours, and by the corridor and escape machinery most likely
    to need it.
    """
    from unittest.mock import MagicMock, patch

    import numpy as np

    from terminus.sweep import Sky, run_sweep

    sky = Sky(39.7917, -104.894, 1600)
    sc = MagicMock()
    sc.equ_coord.return_value = (12.0, 20.0)
    sc.capture_rgb.return_value = np.full((8, 8, 3), 120.0, dtype=np.float32)
    cfg = {
        "sun_cone_deg": 30, "slew_step_deg": 5, "az_step": 90, "alt_min": 0,
        "alt_max": 60, "alt_tol": 2.5, "clear_thresh": 0.6, "min_alt_deg": -1.0,
    }  # fmt: skip

    seen = {}

    def capture(ptr, *a, **k):
        seen["floor"] = ptr.MIN_ALT_DEG
        return 20.0, "edge", "tree", []

    with (
        patch.object(Sky, "sun", lambda self: (297.0, -5.0)),  # terminus-63: pin the channel
        patch("terminus.sweep.scan_horizon", side_effect=capture),
    ):
        run_sweep(sc, sky, cfg, az_start=0, az_end=270, dry=True, log=lambda *a, **k: None)

    assert seen.get("floor") == -1.0, (
        f"run_sweep ignored min_alt_deg and used {seen.get('floor')}; the default is a "
        "guess and the config exists to replace it with a measurement"
    )


def test_the_below_horizon_corridor_is_safe_at_every_azimuth():
    """Devon's route: get below the horizon and the whole circle opens up.

    Descending at a fixed azimuth moves monotonically away from a Sun that is
    above; travelling below the horizon is clear at every azimuth by
    construction; ascending is the descent in reverse at an azimuth already
    chosen to be safe. Three legs, each safe for its own reason.
    """
    from unittest.mock import MagicMock

    from terminus.sweep import Pointer, Sky

    class HighSun(Sky):
        def sun(self):
            return 270.0, 40.0

    sky = HighSun(39.7917, -104.894, 1600)
    ptr = Pointer(MagicMock(), sky, 30, 5, True)
    assert ptr.corridor_alt() is not None, "a high Sun leaves the horizon itself clear"

    start, end = (230.0, 25.0), (310.0, 25.0)
    rd0 = sky.altaz_to_radec(*start)
    for clockwise in (True, False):
        wps = ptr.corridor_route(*start, *end, clockwise)
        assert wps, "both directions exist"
        assert ptr.route_min_sep(rd0, wps) >= ptr.cone, "and both are clear of the Sun"
        # It ends where it was asked to.
        final = sky.radec_to_altaz(*wps[-1])
        assert final[0] == pytest.approx(end[0], abs=0.5)
        assert final[1] == pytest.approx(end[1], abs=0.5)

    # The corridor is a CANDIDATE, not a default: when something shorter is
    # clear, the cost comparison picks that instead.
    chosen = ptr.plan_route(rd0, sky.altaz_to_radec(*end))
    assert chosen is not None


def test_the_corridor_says_when_the_mount_cannot_reach_it():
    """The depth depends on the Sun's altitude; the floor is hardware.

    How far below the horizon this mount can point is NOT KNOWN — observed at
    -1.1 degrees and no further. So the corridor is unavailable for a low Sun
    until that is measured, and the code says so rather than commanding a slew
    the mount may refuse.
    """
    from unittest.mock import MagicMock

    from terminus.sweep import Pointer, Sky

    def pointer(sun_alt, floor):
        class F(Sky):
            def sun(self, _s=sun_alt):
                return 270.0, _s

        p = Pointer(MagicMock(), F(39.7917, -104.894, 1600), 30, 5, True)
        p.MIN_ALT_DEG = floor
        return p

    # A high Sun needs no depth at all: the horizon is already 40 degrees away.
    assert pointer(40.0, -5.0).corridor_alt() is not None
    # A low Sun needs real depth, and a shallow mount cannot provide it.
    assert pointer(20.0, -5.0).corridor_alt() is None
    assert pointer(20.0, -15.0).corridor_alt() is not None
    # Deepest ever demanded, and only with the Sun on the horizon.
    assert pointer(0.0, -35.0).corridor_alt() == pytest.approx(-35.0)
    assert pointer(0.0, -34.0).corridor_alt() is None


def test_a_tied_turn_goes_against_the_sun_s_own_drift():
    """When the tube shares the Sun's azimuth, neither turn is momentarily better.

    Devon's tiebreak, and it is the one with physics behind it: turn AGAINST the
    Sun's motion. Turning the way it is already going lets it follow, eroding
    what the turn just bought; turning the other way opens the gap from both
    ends.

    The drift is measured rather than assumed from the hemisphere, because the
    assumption has exceptions — inside the tropics the Sun can pass north and
    its azimuth motion is not monotonic through the day.
    """
    from unittest.mock import MagicMock

    from terminus.sweep import Pointer, Sky, _now

    class DriftingSky(Sky):
        """A Sun whose azimuth moves at a chosen rate."""

        def __init__(self, rate, *a, **kw):
            super().__init__(*a, **kw)
            self.rate = rate  # degrees per minute
            self.t0 = _now()

        def sun(self, when=None):
            minutes = ((when or self.t0) - self.t0).to_value("min") if when else 0.0
            return (270.0 + self.rate * minutes) % 360.0, 20.0

    for rate, expected in ((0.2, -1.0), (-0.2, 1.0)):
        sky = DriftingSky(rate, 39.7917, -104.894, 1600)
        ptr = Pointer(MagicMock(), sky, 30, 5, True)
        assert (ptr.sun_drift() > 0) == (rate > 0), "the drift must be measured, not guessed"
        # A tube directly above the Sun: both turns are identical by symmetry.
        assert (
            ptr.escape_turn(270.0, 25.0) == expected
        ), f"with the Sun drifting {rate:+} deg/min the tie must turn {expected:+}"

    # And a test double with no clock still gets an answer, from the hemisphere.
    class Frozen(Sky):
        def sun(self):
            return 270.0, 20.0

    north = Pointer(MagicMock(), Frozen(39.79, -104.89, 1600), 30, 5, True)
    south = Pointer(MagicMock(), Frozen(-33.87, 151.21, 1600), 30, 5, True)
    assert north.sun_drift() > 0 and south.sun_drift() < 0
    assert north.escape_turn(270.0, 25.0) == -1.0
    assert south.escape_turn(270.0, 25.0) == 1.0


def test_a_goto_that_moves_without_arriving_gives_up_instead_of_extending_forever():
    """Measured 2026-08-06: one goto spent 288 seconds not arriving.

    The deadline extended whenever the mount reported MOTION, so a mount that
    moves without converging renewed it indefinitely. The failing gotos reached
    the right declination exactly and never the right RA — so they were moving
    the whole time, and the run spent minutes per column discovering nothing.

    The bound has MOVED once since, deliberately. The first fix failed this
    case at NO_PROGRESS_S (~25 s); 2026-08-07 showed that misreads a healthy
    mount mid-reconfiguration — a large slew doglegs, pausing closure for more
    than that while still moving, and two gotos were reported failed that both
    landed. So a stall now stops EXTENDING the deadline rather than ending the
    attempt: the pathological case still fails, bounded by the un-extended
    deadline (GOTO_TIMEOUT past its last progress) instead of running to 288 s
    or forever. Fake clock: the bound is about modelled time, not suite time.
    """
    from unittest.mock import MagicMock, patch

    import pytest

    from terminus import sweep as sweep_mod
    from terminus.sweep import GOTO_TIMEOUT, NO_PROGRESS_S, Pointer, PointingError, Sky

    class FakeTime:
        def __init__(self):
            self.t = 0.0

        def time(self):
            return self.t

        def sleep(self, dt):
            self.t += dt

    clock = FakeTime()
    sky = Sky(39.7917, -104.894, 1600)
    sc = MagicMock()
    sc.goto.return_value = None
    # Dec arrives; RA never does — exactly what the mount did.
    sc.equ_coord.return_value = (4.551, 60.43)
    sc.call.return_value = {"result": {"mount": {"move_type": "ScopeGoto"}}}
    ptr = Pointer(sc, sky, 30, 5)

    with patch.object(sweep_mod, "time", clock):
        with pytest.raises(PointingError) as exc:
            ptr._goto_wait(8.604, 60.43, 0.1)

    assert (
        clock.t < GOTO_TIMEOUT + NO_PROGRESS_S + 30
    ), f"took {clock.t:.0f}s to give up; extending on motion alone took 288"
    assert "stopped improving" in str(
        exc.value
    ), "it must say the mount moved but would not converge, not that it never moved"


def test_a_mount_that_never_moves_is_reported_differently():
    """The other fault, which used to produce the same message.

    A stowed mount answers every query and never moves. Saying so plainly is the
    difference between opening the arm and hunting for a pointing bug.
    """
    from unittest.mock import MagicMock

    import pytest

    from terminus.sweep import Pointer, PointingError, Sky

    sc = MagicMock()
    sc.equ_coord.return_value = (5.759, -90.0)  # parked at the pole, stowed
    sc.call.return_value = {"result": {"mount": {"move_type": "none"}}}
    ptr = Pointer(sc, Sky(39.7917, -104.894, 1600), 30, 5)

    with pytest.raises(PointingError, match="never moved toward it at all"):
        ptr._goto_wait(8.604, 60.43, 0.1)
