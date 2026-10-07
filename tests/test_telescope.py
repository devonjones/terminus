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
