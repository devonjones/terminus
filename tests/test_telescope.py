"""A site's telescope columns: the import from a CLI run, and the live refit."""

import math
import os

import pytest
import yaml

from terminus import guide, orient, telescope
from terminus.export import write_mask

TRUE = {"yaw": 37.0, "pitch": 1.5, "tilt_mag": 2.0, "tilt_dir": 120.0}
NEAR = {"yaw": 33.0, "pitch": 0.0, "tilt_mag": 1.0, "tilt_dir": 90.0}  # the last fit, 4 deg off


def photo_alt(az):
    a = math.radians(az)
    return 12 + 8 * math.sin(2 * a) + 5 * math.sin(3 * a + 1)


@pytest.fixture
def site(tmp_path):
    mask = {az: (round(photo_alt(az), 2), "structure") for az in range(360)}
    write_mask(str(tmp_path / telescope.PHOTO_MASK), mask, [], {"lat": 39.8, "lon": -104.9})
    return tmp_path


def measured(site, azs, solution=TRUE):
    """What a telescope would measure at `azs` if the photo sat at `solution`."""
    from terminus.export import load_mask

    _, rows = load_mask(str(site / telescope.PHOTO_MASK))
    sample = guide.photo_sample(rows)
    fids = [orient.Fiducial(az, 0.0) for az in azs]
    pre = orient.predict(fids, sample, solution["yaw"], solution["tilt_mag"], solution["tilt_dir"])
    return {
        az: float(p) - solution["pitch"] for az, p in zip(azs, pre, strict=True)
    }  # orient.score


def columns(site, alts, **over):
    cols = [
        {"az": float(az), "alt": alt, "uncertainty": 0.5, "method": "focused", "included": True,
         "tags": [], "frames": [], "note": "", **over}
        for az, alt in alts.items()
    ]  # fmt: skip
    return {"note": "", "columns": cols}


def test_the_refit_recovers_a_known_rotation(site):
    azs = [10, 40, 75, 110, 150, 200, 250, 290, 330]
    fit = telescope.refit(str(site), columns(site, measured(site, azs)), near=NEAR)
    assert abs(((fit["solution"]["yaw"] - TRUE["yaw"] + 180) % 360) - 180) < 1.0
    assert abs(fit["solution"]["pitch"] - TRUE["pitch"]) < 0.5
    assert fit["summary"]["n"] == len(azs) and fit["summary"]["rms"] < 0.5
    assert fit["summary"]["within_2"] == len(azs)


def test_an_excluded_column_is_not_fitted_but_is_listed(site):
    alts = measured(site, [10, 40, 75, 110, 150, 200, 250, 290, 330])
    alts[150.0 if 150.0 in alts else 150] += 9.0  # a false edge
    doc = columns(site, alts)
    with_it = telescope.refit(str(site), doc, near=NEAR)
    doc["columns"][4]["included"] = False
    without = telescope.refit(str(site), doc, near=NEAR)
    assert without["summary"]["rms"] < with_it["summary"]["rms"]
    row = next(c for c in without["columns"] if c["az"] == 150)
    assert row["included"] is False and row["residual"] is None


def test_fewer_than_four_columns_is_no_fit(site):
    assert telescope.refit(str(site), columns(site, measured(site, [10, 40, 75]))) is None


def test_import_merges_first_wins_and_brings_the_frames(site, tmp_path):
    run = tmp_path / "run"
    (run / "frames").mkdir(parents=True)
    for name in ("az040_alt22.75_sky000.jpg", "az040_alt23.00_sky045.jpg", "notes.txt"):
        (run / "frames" / name).write_bytes(b"jpg")
    evening = {"horizon": {40: {"alt": 23.09, "uncertainty": 0.25}, 300: {"alt": 6.9}}}
    morning = {"horizon": {40: {"alt": 99.0}, 270: {"alt": 19.41, "exclude": "dawn"}}}
    (run / "evening.yaml").write_text(yaml.safe_dump(evening))
    (run / "morning.yaml").write_text(yaml.safe_dump(morning))
    doc = telescope.import_run(
        str(site), [str(run / "evening.yaml"), str(run / "morning.yaml")], str(run / "frames"),
        note="native link: 0.3 deg JNow bias (terminus-85)",
    )  # fmt: skip
    by_az = {c["az"]: c for c in doc["columns"]}
    assert sorted(by_az) == [40.0, 270.0, 300.0]
    assert by_az[40.0]["alt"] == 23.09 and by_az[40.0]["uncertainty"] == 0.25  # first wins
    assert by_az[40.0]["method"] == "focused" and by_az[300.0]["method"] == "coarse"
    assert [f["sky"] for f in by_az[40.0]["frames"]] == [0.0, 0.45]
    assert all(os.path.isfile(site / f["file"]) for f in by_az[40.0]["frames"])
    assert by_az[270.0]["included"] is False and by_az[270.0]["note"] == "dawn"
    assert telescope.load(str(site)) == doc


def test_a_near_fit_searches_only_near(site):
    azs = [10, 40, 75, 110, 150, 200, 250, 290, 330]
    far = {**NEAR, "yaw": 200.0}
    fit = telescope.refit(str(site), columns(site, measured(site, azs)), near=far)
    off = abs(((fit["solution"]["yaw"] - 200.0 + 180) % 360) - 180)
    assert off <= orient.NEAR_YAW_DEG + 2.0  # refinement may creep past the grid; not to 37


# ---- the sidecar's routes (the fit itself is stubbed: tested above) --------
from test_server import _site, call, conforms, serve  # noqa: E402, F401

FAKE_FIT = {
    "solution": {"yaw": 186.0, "pitch": -1.5, "tilt_mag": 7.0, "tilt_dir": 300.0},
    "yaw_pm": 4.0,
    "columns": [],
    "summary": {"n": 3, "rms": 0.4, "median": 0.3, "max": 0.7, "within_2": 3},
}


@pytest.fixture
def opened(serve, tmp_path, monkeypatch):  # noqa: F811
    d = _site(tmp_path)
    run = tmp_path / "run"
    (run / "frames").mkdir(parents=True)
    (run / "frames" / "az090_alt05.25_sky040.jpg").write_bytes(b"\xff\xd8 a frame")
    (run / "f.yaml").write_text(yaml.safe_dump({"horizon": {90: {"alt": 5.4}, 200: {"alt": 40.0},
                                                              250: {"alt": 12.0}}}))  # fmt: skip
    telescope.import_run(str(d), [str(run / "f.yaml")], str(run / "frames"))
    calls = []

    def fake_refit(site, doc=None, near=None):
        calls.append(near)
        cols = [{"az": c["az"], "alt": c["alt"], "method": c["method"], "included": c["included"],
                 "residual": 0.3 if c["included"] else None} for c in doc["columns"]]  # fmt: skip
        return {**FAKE_FIT, "columns": cols}

    monkeypatch.setattr(telescope, "refit", fake_refit)
    s = serve(sites_root=str(tmp_path))
    call(s, "POST", "/site/open", {"slug": d.name})
    return s, calls


def test_columns_route_lists_them_without_paths(opened):
    s, _ = opened
    code, v = call(s, "GET", "/site/columns")
    assert code == 200 and v["fit"] is None and [c["az"] for c in v["columns"]] == [90, 200, 250]
    conforms("Columns", v)
    assert v["columns"][0]["frames"] == [
        {"alt": 5.25, "sky": 0.4, "name": "az090_alt05.25_sky040.jpg"}
    ]
    assert "scope" not in json_text(v)  # names, never paths


def json_text(v):
    import json

    return json.dumps(v)


def test_a_full_fit_then_an_edit_refits_near_it(opened):
    s, calls = opened
    code, v = call(s, "POST", "/site/columns/fit", {})
    assert code == 200 and v["fit"]["summary"]["rms"] == 0.4 and calls == [None]
    conforms("Columns", v)
    code, v = call(
        s, "POST", "/site/columns/edit", {"az": 250, "included": False, "tags": ["false edge"]}
    )
    assert code == 200 and calls[-1] == FAKE_FIT["solution"]  # local, from the last fit
    row = next(c for c in v["columns"] if c["az"] == 250)
    assert row["included"] is False and row["tags"] == ["false edge"] and row["residual"] is None


@pytest.mark.parametrize(
    "body", [{"az": "250"}, {"az": 999}, {"az": 250, "included": "no"},
             {"az": 250, "tags": "pocket"}, {"az": 250, "tags": ["cloud"]}],
)  # fmt: skip
def test_an_edit_refuses_what_it_cannot_apply(opened, body):
    s, _ = opened
    assert call(s, "POST", "/site/columns/edit", body)[0] == 400


def test_a_frame_by_name_and_nothing_else(opened):
    s, _ = opened
    code, img = call(s, "GET", "/site/column/frame.jpg?az=90&name=az090_alt05.25_sky040.jpg")
    assert code == 200 and img.startswith(b"\xff\xd8")
    for q in ("az=90&name=../../../photo_mask.yaml", "az=90&name=az090_alt09.99_sky000.jpg",
              "az=x&name=az090_alt05.25_sky040.jpg"):  # fmt: skip
        assert call(s, "GET", f"/site/column/frame.jpg?{q}")[0] in (204, 400)


def test_a_site_without_columns(serve, tmp_path):  # noqa: F811
    d = _site(tmp_path)
    s = serve(sites_root=str(tmp_path))
    call(s, "POST", "/site/open", {"slug": d.name})
    assert call(s, "GET", "/site/columns")[0] == 204
    code, body = call(s, "POST", "/site/columns/fit", {})
    assert code == 400 and "no telescope columns" in body["error"]


def test_sky_fraction_is_measured_in_pixels():
    assert telescope.sky_fraction((100, 100), (0, 0.25), (1, 0.25), (0.5, 0.1)) == pytest.approx(
        0.25, abs=0.01
    )
    assert telescope.sky_fraction((100, 100), (0, 0.25), (1, 0.25), (0.5, 0.9)) == pytest.approx(
        0.75, abs=0.01
    )
    # The diagonal of a portrait frame halves it whichever way the frame is stretched.
    assert telescope.sky_fraction((1080, 1920), (0, 0), (1, 1), (0.9, 0.1)) == pytest.approx(
        0.5, abs=0.01
    )
    # A line from one corner to a side's middle: a quarter, in pixels.
    assert telescope.sky_fraction((1080, 1920), (0, 0), (1, 0.5), (0.9, 0.1)) == pytest.approx(
        0.25, abs=0.01
    )
    with pytest.raises(ValueError, match="off the edge"):
        telescope.sky_fraction((100, 100), (0, 0.5), (1, 0.5), (0.3, 0.5))


def test_a_click_becomes_the_measurement(site, tmp_path, monkeypatch):
    from PIL import Image

    from terminus.judge import FRAME_DEG

    run = tmp_path / "run"
    (run / "frames").mkdir(parents=True)
    Image.new("RGB", (108, 192)).save(run / "frames" / "az040_alt23.00_sky045.jpg")
    (run / "f.yaml").write_text(yaml.safe_dump({"horizon": {40: {"alt": 22.5}}}))
    telescope.import_run(str(site), [str(run / "f.yaml")], str(run / "frames"))
    monkeypatch.setattr(telescope, "refit", lambda *a, **k: None)
    v = telescope.click(
        str(site), 40, "az040_alt23.00_sky045.jpg", (0, 0.25), (1, 0.25), (0.5, 0.1)
    )
    col = v["columns"][0]
    assert col["method"] == "clicked"
    assert col["alt"] == pytest.approx(
        23.0 + 0.25 * FRAME_DEG, abs=0.02
    )  # a quarter sky: high in the frame
    with pytest.raises(ValueError, match="no frame"):
        telescope.click(str(site), 40, "az040_alt99.00_sky000.jpg", (0, 0.5), (1, 0.5), (0.5, 0.1))


@pytest.mark.parametrize(
    "body", [{"az": 90, "name": "az090_alt05.25_sky040.jpg", "p1": [0, 0.5], "p2": [1, 0.5], "sky": [0.5, 2]},
             {"az": 90, "name": "az090_alt05.25_sky040.jpg", "p1": [0, 0.5], "p2": [1], "sky": [0.5, 0.1]},
             {"az": "90", "name": "az090_alt05.25_sky040.jpg", "p1": [0, 0.5], "p2": [1, 0.5], "sky": [0.5, 0.1]},
             {"az": 90, "name": "nope.jpg", "p1": [0, 0.5], "p2": [1, 0.5], "sky": [0.5, 0.1]}],
)  # fmt: skip
def test_a_click_refuses_what_it_cannot_place(opened, body):
    s, _ = opened
    assert call(s, "POST", "/site/columns/click", body)[0] == 400
