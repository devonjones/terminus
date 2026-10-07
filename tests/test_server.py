"""The app sidecar's HTTP API: auth, Host/Origin checks, routes, and park-on-exit."""

import http.client
import json
import os
import pathlib
import socket
import subprocess
import sys
import threading
import time
import types

import pytest

import terminus
import terminus.server as server_module
from terminus.server import TABS, Sidecar

ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture
def serve():
    servers = []

    def start(dev=True, scope=None, sites_root=None, jobs=None):
        s = Sidecar(dev=dev, scope=scope, sites_root=sites_root, jobs=jobs)
        threading.Thread(target=s.serve_forever, args=(0.05,), daemon=True).start()
        servers.append(s)
        return s

    yield start
    for s in servers:
        s.shutdown()
        s.server_close()


def call(s, method, path, body=None, headers=None, token=True):
    h = {"Host": f"127.0.0.1:{s.port}"}
    if token:
        h["Authorization"] = f"Bearer {s.token}"
    h.update(headers or {})
    c = http.client.HTTPConnection("127.0.0.1", s.port, timeout=5)
    try:
        c.request(method, path, body=None if body is None else json.dumps(body), headers=h)
        r = c.getresponse()
        data = r.read()
        if r.getheader("Content-Type") == "application/json":
            return r.status, json.loads(data)
        return r.status, data
    finally:
        c.close()


def test_health_reports_version(serve):
    s = serve()
    assert call(s, "GET", "/health") == (200, {"ok": True, "version": terminus.__version__})


def test_app_and_sidecar_versions_match():
    # The Electron main process refuses a sidecar whose /health version differs
    # from its own, so the two must be bumped together.
    pkg = json.loads((ROOT / "app" / "package.json").read_text())
    assert pkg["version"] == terminus.__version__


@pytest.mark.parametrize(
    "headers,token,code",
    [
        ({}, False, 401),
        ({"Authorization": "Bearer wrong"}, False, 401),
        ({"Host": "evil.example:80"}, True, 403),  # DNS rebinding
        ({"Host": "127.0.0.1"}, True, 403),
        ({"Host": "127.0.0.1:1"}, True, 403),
        ({"Origin": "http://evil.example"}, True, 403),  # a page in the user's browser
        ({"Origin": "null"}, True, 403),
    ],
)
@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/health"),
        ("GET", "/state"),
        ("GET", "/dev/state"),
        ("POST", "/state/tab"),
        ("GET", "/sites"),
        ("POST", "/sites"),
        ("POST", "/site/open"),
        ("POST", "/site/spin"),
        ("GET", "/site/horizon"),
        ("GET", "/site/disc"),
        ("GET", "/site/panorama.jpg"),
        ("GET", "/site/disc.jpg"),
        ("GET", "/site/frames"),
        ("GET", "/site/frame/thumb.jpg?name=a.jpg"),
        ("GET", "/site/frame/footprint.png?name=a.jpg"),
        ("GET", "/site/frame/layer.webp?layer=layer0000.tif"),
        ("GET", "/site/frame/verdict.png?layer=layer0000.tif"),
        ("GET", "/site/disagree.png"),
        ("GET", "/site/outline.png"),
        ("GET", "/site/progress.png"),
        ("POST", "/site/frames"),
        ("POST", "/site/rename"),
        ("POST", "/site/delete"),
    ],
)
def test_refuses_without_token_host_or_with_origin(
    serve, tmp_path, method, path, headers, token, code
):
    _site(tmp_path / "sites")
    s = serve(sites_root=str(tmp_path / "sites"), jobs=FakeJobs())
    s.slug = "site-2026-10-05"
    body = {
        "tab": "fit",
        "slug": "site-2026-10-05",
        "deg": 90,
        "photos": [_photo(tmp_path / "a.jpg")],
    }
    status, _ = call(s, method, path, body, headers=headers, token=token)
    assert status == code
    assert s.state["tab"] == "connect" and s.state["spin"] == 0.0
    assert os.listdir(tmp_path / "sites") == ["site-2026-10-05"], "a refused drop made a site"


def test_localhost_host_is_accepted(serve):
    s = serve()
    assert call(s, "GET", "/health", headers={"Host": f"localhost:{s.port}"})[0] == 200


def test_state_defaults(serve):
    s = serve()
    code, st = call(s, "GET", "/state")
    assert code == 200
    assert st == {
        "version": terminus.__version__,
        "site": None,
        "tab": "connect",
        "tabs": list(TABS),
        "scope": {"link": "none", "host": None},
        "sun_mode": "sun",
        "spin": 0.0,
        "job": None,
    }


def test_dev_state_only_with_dev(serve):
    assert call(serve(dev=False), "GET", "/dev/state")[0] == 404
    s = serve(dev=True)
    call(s, "POST", "/state/tab", {"tab": "orient"})
    code, st = call(s, "GET", "/dev/state")
    assert code == 200
    assert st["tab"] == "orient"
    assert any("tab -> orient" in line for line in st["log"])


def test_set_tab(serve):
    s = serve()
    code, st = call(s, "POST", "/state/tab", {"tab": "export"})
    assert (code, st["tab"]) == (200, "export")
    assert call(s, "GET", "/state")[1]["tab"] == "export"


@pytest.mark.parametrize("body", [{"tab": "nope"}, {"tab": 3}, {"x": 1}, [1], "fit"])
def test_set_tab_rejects_bad_bodies(serve, body):
    s = serve()
    assert call(s, "POST", "/state/tab", body)[0] == 400
    assert s.state["tab"] == "connect"


def test_set_tab_rejects_empty_and_oversized_bodies(serve):
    s = serve()
    for size in ("0", str(10**6)):
        assert call(s, "POST", "/state/tab", headers={"Content-Length": size})[0] == 400


def test_sun_mode_cannot_be_set_through_the_api(serve):
    s = serve()
    for path in ("/state/sun_mode", "/state"):
        assert call(s, "POST", path, {"sun_mode": "shade"})[0] == 404
    assert s.state["sun_mode"] == "sun"


def test_unknown_route_404(serve):
    assert call(serve(), "GET", "/nope")[0] == 404


def test_errors_do_not_echo_the_token(serve):
    s = serve()
    _, body = call(s, "GET", "/state", headers={"Authorization": "Bearer guess"}, token=False)
    assert s.token not in json.dumps(body)


def test_an_idle_connection_does_not_block_shutdown(serve):
    s = serve()
    with socket.create_connection(("127.0.0.1", s.port)):  # connects, sends nothing
        time.sleep(0.2)
        done = threading.Thread(target=s.shutdown)
        done.start()
        done.join(timeout=3)
        assert not done.is_alive()


def test_unusable_stdin_shuts_down_instead_of_running_forever(serve, monkeypatch, caplog):
    s = serve()
    monkeypatch.setattr(sys, "stdin", None)  # e.g. launched with no stdin at all
    t = threading.Thread(target=server_module._stop_on_eof, args=(s,), daemon=True)
    t.start()
    t.join(timeout=3)
    assert not t.is_alive()  # shutdown() returned, so serve_forever stopped
    assert "cannot read stdin, shutting down" in caplog.text


def _sidecar_process(sites_root):
    p = subprocess.Popen(
        [sys.executable, "-m", "terminus.server", "--dev", "--sites", str(sites_root)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
    )
    watchdog = threading.Timer(30, p.kill)  # readline() has no timeout of its own
    watchdog.daemon = True  # never hold up pytest's exit
    watchdog.start()
    hello = json.loads(p.stdout.readline() or "null")
    assert hello, "sidecar printed no startup line"
    return p, types.SimpleNamespace(**hello), watchdog  # namespace: just enough for call()


@pytest.mark.parametrize("stop", ["stdin", "sigterm"])
def test_process_prints_port_and_token_and_parks_on_exit(stop, tmp_path):
    if stop == "sigterm" and sys.platform == "win32":
        pytest.skip("Windows has no SIGTERM handler path; stdin is how it stops")
    p, s, watchdog = _sidecar_process(tmp_path / "sites")
    with p:
        try:
            assert call(s, "GET", "/health")[0] == 200
            if stop == "stdin":
                p.stdin.close()
            else:
                p.terminate()
            assert p.wait(timeout=20) == 0
        finally:
            watchdog.cancel()
            p.kill()
        assert "park: no scope linked" in p.stderr.read()


# ---- sites, the pipeline job, the views, and the schema contract ----

SCHEMA = json.loads((ROOT / "src/terminus/server/schema.json").read_text())


def conforms(name, payload):
    """Validate a payload against schema.json's $defs/<name> (the TS types' source)."""
    import jsonschema

    jsonschema.Draft202012Validator({"$defs": SCHEMA["$defs"], "$ref": f"#/$defs/{name}"}).validate(
        payload
    )


def _photo(path):
    from PIL import Image

    Image.new("RGB", (8, 8), (90, 140, 200)).save(path)
    return str(path)


def _site(root, slug="site-2026-10-05", oriented=False, panorama=True):
    """A site folder as the pipeline leaves it (plus `orient`'s output if oriented)."""
    import numpy as np
    from PIL import Image

    from terminus.export import write_mask

    d = root / slug
    (d / "photos").mkdir(parents=True)
    _photo(d / "photos" / "a.jpg")
    if panorama:
        pano = np.zeros((36, 72, 3), np.uint8)
        pano[:18] = (90, 140, 200)  # sky above, ground below
        Image.fromarray(pano).save(d / "equirect.png")
        np.save(d / "equirect.coverage.npy", np.ones((36, 72), np.uint8))
    cols = {
        0: {"alt": 20.0, "type": "tree", "planning": 22.0, "fuzz": 2.0, "pockets": [[15.0, 10.0]]},
        90: {"alt": 5.0, "type": "structure"},
        180: {"alt": 12.5, "type": "tree", "planning": 13.0, "fuzz": 0.5},
        270: {"alt": 30.0, "type": "", "bound": True},
    }
    write_mask(d / "photo_mask.yaml", cols, [], {"oriented": False})
    if oriented:
        meta = {
            "yaw": 186.2, "pitch": -1.5, "tilt_mag": 7.1, "tilt_dir": 300.0, "fit_settled": True,
            "fit_fiducials": [
                {"az": 90.0, "alt": 5.4, "used": True, "residual": 0.4},
                {"az": 200.0, "alt": 40.0, "bound": True, "used": True},
                {"az": 250.0, "alt": 12.0, "used": False, "reason": "outlier"},
                {"az": 300.0, "alt": None, "used": False, "reason": "no edge in the search window"},
            ],
        }  # fmt: skip
        write_mask(d / "oriented.yaml", cols, [], meta)
    return d


def child(script):
    """A Jobs command that runs `script` in place of the pipeline child."""
    return lambda: [sys.executable, "-c", script]


# A child that "builds" the site: announces both steps and writes the outputs.
OK_CHILD = (
    "import sys, pathlib; d = pathlib.Path(sys.argv[2]); print('@step mosaic'); "
    "print('registered 18 frames'); print('@step skymask'); print('args', sys.argv[1:]); "
    "(d / 'equirect.png').write_bytes(b'x'); (d / 'photo_mask.yaml').write_text('x'); "
    "(d / 'horizon.yaml').write_text('x')"
)


class FakeJobs(server_module.sites.Jobs):
    """The real Jobs, with a scripted child in place of the pipeline (no Hugin)."""

    def __init__(self, script=OK_CHILD, **kw):
        super().__init__(command=child(script), **kw)

    def wait(self):
        for _ in range(500):
            if self.state and self.state["status"] != "running":
                return self.state
            time.sleep(0.01)
        raise AssertionError("job did not finish")


def test_create_site_copies_photos_into_a_new_dated_folder(tmp_path):
    from datetime import date

    from terminus.server import sites

    root = tmp_path / "sites"
    a, b = _photo(tmp_path / "a.jpg"), _photo(tmp_path / "b.JPG")
    slug = sites.create_site(str(root), [a, b], today=date(2026, 10, 5))
    assert slug == "site-2026-10-05"
    assert sorted(os.listdir(root / slug / "photos")) == ["a.jpg", "b.JPG"]
    again = sites.create_site(str(root), [a, a], today=date(2026, 10, 5))
    assert again == "site-2026-10-05-2"
    assert sorted(os.listdir(root / again / "photos")) == ["a-2.jpg", "a.jpg"]


@pytest.mark.parametrize(
    "photos",
    [[], "a.jpg", None, ["missing.jpg"], ["notes.txt"], [3], ["a.jpg", "notes.txt"]],
)
def test_create_site_refuses_a_bad_drop_and_makes_nothing(tmp_path, photos):
    from terminus.server import sites

    _photo(tmp_path / "a.jpg")
    (tmp_path / "notes.txt").write_text("not a photo")
    os.chdir(tmp_path)
    root = tmp_path / "sites"
    with pytest.raises(sites.SiteError):
        sites.create_site(str(root), photos)
    assert not root.exists() or not os.listdir(root)


@pytest.mark.parametrize("slug", ["../etc", "site/../../x", "", "A", ".hidden", None, 3, "nope"])
def test_site_dir_refuses_anything_but_an_existing_slug(tmp_path, slug):
    from terminus.server import sites

    (tmp_path / "site-a").mkdir()
    with pytest.raises(sites.SiteError):
        sites.site_dir(str(tmp_path), slug)
    assert sites.site_dir(str(tmp_path), "site-a") == str(tmp_path / "site-a")


def test_the_pipeline_child_stitches_reblends_then_reads_the_voted_sky(tmp_path, capsys):
    from terminus.server import sites

    d = _site(tmp_path, panorama=False)
    calls = []
    sites.run_pipeline(str(d), run=calls.append)
    assert [argv[0] for argv in calls] == ["mosaic", "reblend", "skymask", "horizon"]
    mosaic, reblend, skymask, horizon = calls
    assert mosaic[1] == str(d / "photos") and mosaic[mosaic.index("--out") + 1] == str(
        d / "equirect"
    )
    assert reblend[1:] == [str(d / "work"), "--out", str(d / "equirect"), "--events"]
    assert "--events" in mosaic, "the app watches each photo's progress"
    assert "--layers-only" in mosaic, "reblend does the blending; the stitch must not too"
    assert skymask[1] == str(d / "equirect.png") and skymask[-1] == str(d / "photo_mask.yaml")
    assert skymask[skymask.index("--sky") + 1] == str(d / "equirect.sky.npy")
    assert horizon[1:] == [str(d / "equirect"), "--out", str(d / "horizon.yaml")]
    assert capsys.readouterr().out == "@step mosaic\n@step reblend\n@step skymask\n@step horizon\n"


def test_photos_turned_off_are_left_out_of_both_a_stitch_and_a_reblend(tmp_path, monkeypatch):
    from terminus.server import sites

    monkeypatch.setattr(sites, "segmentation_installed", lambda: False)
    d = _site(tmp_path, panorama=False)
    sites.set_frames_off(str(d), ["b.jpg", "a.jpg"])
    build = dict(sites.pipeline(str(d), "build"))
    assert build["mosaic"][-4:] == ["--exclude", "a.jpg", "--exclude", "b.jpg"]
    assert build["reblend"][-4:] == ["--off", "a.jpg", "--off", "b.jpg"]
    reblend = sites.pipeline(str(d), "reblend")
    assert [s for s, _ in reblend] == [
        "reblend",
        "skymask",
        "horizon",
    ], "a reblend never re-stitches"


def test_a_build_follows_the_childs_steps_and_log(tmp_path):
    jobs = FakeJobs(hugin="/bundle/hugin/bin")
    jobs.start("site-a", str(tmp_path)).join(10)
    st = jobs.state
    assert st["status"] == "done" and st["step"] == "skymask" and st["error"] is None
    assert st["log"][0] == "registered 18 frames"
    assert st["log"][1] == (
        # The sites folder is scrubbed from what the page sees.
        f"args {['--pipeline', '<sites>/' + tmp_path.name, '--kind', 'build', '--hugin', '/bundle/hugin/bin']}"
    )
    conforms("Job", st)


def test_a_failed_step_is_reported_with_the_engines_own_message(tmp_path):
    jobs = FakeJobs(
        "import sys; print('@step mosaic'); "
        "print('error: no frame could be constrained', file=sys.stderr); sys.exit(1)"
    )
    jobs.start("s", str(tmp_path)).join(10)
    assert jobs.state["status"] == "failed" and jobs.state["step"] == "mosaic"
    assert jobs.state["error"] == "error: no frame could be constrained"


def test_a_crashing_child_fails_the_build_not_the_sidecar(tmp_path):
    jobs = FakeJobs("print('@step skymask', flush=True); raise RuntimeError('bug')")
    jobs.start("s", str(tmp_path)).join(10)
    assert jobs.state["status"] == "failed"
    assert jobs.state["error"] == "skymask stopped (exit 1): RuntimeError: bug"
    assert any("RuntimeError: bug" in line for line in jobs.state["log"]), "the traceback is kept"


def test_a_build_that_overruns_is_killed_and_says_why(tmp_path):
    jobs = FakeJobs("import time; print('@step mosaic', flush=True); time.sleep(30)", timeout=0.5)
    t0 = time.monotonic()
    jobs.start("s", str(tmp_path)).join(10)
    assert time.monotonic() - t0 < 10
    assert jobs.state["status"] == "failed" and "took longer than" in jobs.state["error"]


def test_only_one_site_builds_at_a_time_and_stop_kills_it(tmp_path):
    from terminus.server import sites

    jobs = FakeJobs("import time; time.sleep(30)")
    t = jobs.start("a", str(tmp_path))
    with pytest.raises(sites.SiteError, match="already"):
        jobs.start("b", str(tmp_path))
    jobs.stop()
    t.join(10)
    assert jobs.state["status"] == "failed"


def test_the_real_child_runs_the_engine_and_reports_its_error(tmp_path):
    """`python -m terminus.server --pipeline` is the child the sidecar starts.
    With one 8-pixel photo the engine fails (no Hugin, or nothing to register),
    and its own "error: ..." line must come back as the build's error."""
    from terminus.server import sites

    d = _site(tmp_path, panorama=False)
    env_hugin = str(tmp_path / "no-hugin")  # an empty bundle: mosaic refuses at once
    jobs = sites.Jobs(hugin=env_hugin)
    jobs.start("s", str(d)).join(60)
    assert jobs.state["status"] == "failed" and jobs.state["step"] == "mosaic"
    assert jobs.state["error"].startswith("error: hugin tools missing from")


def test_new_site_route_copies_photos_and_starts_the_build(serve, tmp_path):
    jobs = FakeJobs()
    s = serve(sites_root=str(tmp_path / "sites"), jobs=jobs)
    (tmp_path / "sites").mkdir()
    code, st = call(s, "POST", "/sites", {"photos": [_photo(tmp_path / "a.jpg")]})
    assert code == 200, st
    conforms("AppState", st)
    slug = st["site"]["slug"]
    assert st["site"]["photos"] == 1 and st["job"]["site"] == slug
    conforms("Job", jobs.wait())
    code, listing = call(s, "GET", "/sites")
    conforms("SiteList", listing)
    assert [x["slug"] for x in listing["sites"]] == [slug]


@pytest.mark.parametrize("body", [{"photos": "x.jpg"}, {"photos": ["nope.jpg"]}, {}])
def test_new_site_route_refuses_bad_photos(serve, tmp_path, body):
    s = serve(sites_root=str(tmp_path), jobs=FakeJobs())
    code, err = call(s, "POST", "/sites", body)
    assert code == 400 and "photo" in err["error"]
    assert os.listdir(tmp_path) == []


def test_open_site_and_read_an_unoriented_horizon(serve, tmp_path):
    _site(tmp_path)
    s = serve(sites_root=str(tmp_path))
    assert call(s, "GET", "/site/horizon")[0] == 400, "no site open yet"
    code, st = call(s, "POST", "/site/open", {"slug": "site-2026-10-05"})
    assert code == 200
    site = {k: st["site"][k] for k in ("slug", "name", "photos", "panorama", "mask")}
    assert site == {"slug": "site-2026-10-05", "name": "site-2026-10-05", "photos": 1,
                    "panorama": True, "mask": True}  # fmt: skip
    code, h = call(s, "GET", "/site/horizon")
    assert code == 200
    conforms("Horizon", h)
    assert h["mask"] == "photo_mask.yaml" and h["oriented"] is False
    assert h["solution"] == {"yaw": 0.0, "pitch": 0.0, "tilt_mag": 0.0, "tilt_dir": 0.0}
    assert [c["az"] for c in h["columns"]] == [0.0, 90.0, 180.0, 270.0]
    assert h["columns"][0]["pockets"] == [[15.0, 10.0]] and h["columns"][0]["planning"] == 22.0
    assert h["columns"][3]["bound"] is True and h["fit"] == [] and h["settled"] is None


def test_an_oriented_site_shows_its_solution_and_fit(serve, tmp_path):
    _site(tmp_path, oriented=True)
    s = serve(sites_root=str(tmp_path))
    call(s, "POST", "/site/open", {"slug": "site-2026-10-05"})
    code, h = call(s, "GET", "/site/horizon")
    conforms("Horizon", h)
    assert h["mask"] == "oriented.yaml" and h["oriented"] is True and h["settled"] is True
    assert h["solution"] == {"yaw": 186.2, "pitch": -1.5, "tilt_mag": 7.1, "tilt_dir": 300.0}
    assert [f["az"] for f in h["fit"]] == [90.0, 200.0, 250.0, 300.0]
    assert h["fit"][3] == {"az": 300.0, "used": False, "reason": "no edge in the search window"}


def test_disc_overlays_are_polar_disc_xy(serve, tmp_path):
    """The JS draws these points as given: they must be the engine's projection,
    so the app and `terminus polar` cannot disagree about where a column is."""
    from terminus import polar

    _site(tmp_path, oriented=True)
    s = serve(sites_root=str(tmp_path))
    call(s, "POST", "/site/open", {"slug": "site-2026-10-05"})
    code, disc = call(s, "GET", "/site/disc")
    assert code == 200
    conforms("Disc", disc)

    def xy(az, alt):
        return [round(v, 1) for v in polar.disc_xy(az, alt, polar.SIZE, polar.FLOOR_DEG)]

    assert disc["planning"] == [xy(0, 22.0), xy(180, 13.0)]
    # The actual horizon is the outline raster (outline.png), and pockets are
    # just the band between it and planning: neither is a list of points.
    assert "actual" not in disc and "pockets" not in disc
    # Only the columns the fit used, with the ceiling-bound one marked.
    assert [(f["az"], f["bound"]) for f in disc["fiducials"]] == [(90.0, False), (200.0, True)]
    north, east = disc["cardinals"][0], disc["cardinals"][1]
    assert north["label"] == "N" and north["xy"][1] < disc["centre"], "north is up"
    assert east["label"] == "E" and east["xy"][0] > disc["centre"], "east is right"


def test_site_images_are_jpegs_and_absent_ones_are_204(serve, tmp_path):
    _site(tmp_path, slug="site-a")
    _site(tmp_path, slug="site-b", panorama=False)
    s = serve(sites_root=str(tmp_path))
    call(s, "POST", "/site/open", {"slug": "site-a"})
    for path in ("/site/panorama.jpg", "/site/disc.jpg"):
        code, data = call(s, "GET", path)
        assert code == 200 and data[:3] == b"\xff\xd8\xff", path
    call(s, "POST", "/site/open", {"slug": "site-b"})
    # 204, not 404: a 404 is a wrong route, and the app must be able to tell.
    assert call(s, "GET", "/site/panorama.jpg")[0] == 204
    assert call(s, "GET", "/site/disc.jpg")[0] == 204
    assert call(s, "GET", "/site/nope.jpg")[0] == 404


def test_open_refuses_unknown_and_traversing_names(serve, tmp_path):
    s = serve(sites_root=str(tmp_path))
    for slug in ("../../etc", "nope", 3):
        assert call(s, "POST", "/site/open", {"slug": slug})[0] == 400
    assert call(s, "GET", "/state")[1]["site"] is None


@pytest.mark.parametrize("deg,code,spin", [(370, 200, 10.0), (-90, 200, 270.0), (True, 400, 0.0),
                                           ("5", 400, 0.0), (1e9, 400, 0.0), (None, 400, 0.0)])  # fmt: skip
def test_spin_takes_degrees_and_wraps(serve, deg, code, spin):
    s = serve()
    got, st = call(s, "POST", "/site/spin", {"deg": deg})
    assert got == code
    assert call(s, "GET", "/state")[1]["spin"] == spin


def test_a_broken_mask_is_a_clean_422_without_the_sites_path(serve, tmp_path):
    d = _site(tmp_path)
    (d / "photo_mask.yaml").write_text("horizon:\n  10: {alt: 5, pockets: [[1, 9]]}\n")
    s = serve(sites_root=str(tmp_path))
    call(s, "POST", "/site/open", {"slug": "site-2026-10-05"})
    code, err = call(s, "GET", "/site/horizon")
    assert code == 422 and "alt_hi below alt_lo" in err["error"]
    assert str(tmp_path) not in err["error"]


def test_every_state_response_matches_the_schema(serve, tmp_path):
    _site(tmp_path)
    s = serve(dev=True, sites_root=str(tmp_path))
    conforms("Health", call(s, "GET", "/health")[1])
    conforms("AppState", call(s, "GET", "/state")[1])
    conforms("AppState", call(s, "POST", "/site/open", {"slug": "site-2026-10-05"})[1])
    conforms("AppState", call(s, "POST", "/state/tab", {"tab": "horizon"})[1])
    conforms("AppState", call(s, "GET", "/dev/state")[1])
    conforms("AppState", call(s, "POST", "/site/spin", {"deg": 359.5})[1])
    conforms("Disc", call(s, "GET", "/site/disc")[1])  # the unoriented site's disc


# ---- lifecycle, refusals, and the gaps found by mutation ----


def test_a_drop_during_a_build_is_refused_before_anything_changes(serve, tmp_path):
    jobs = FakeJobs("import time; time.sleep(30)")
    root = tmp_path / "sites"
    _site(root, slug="site-a")
    s = serve(sites_root=str(root), jobs=jobs)
    call(s, "POST", "/site/open", {"slug": "site-a"})
    call(s, "POST", "/site/spin", {"deg": 45})
    assert call(s, "POST", "/sites", {"photos": [_photo(tmp_path / "a.jpg")]})[0] == 200
    building = call(s, "GET", "/state")[1]["site"]["slug"]
    code, err = call(s, "POST", "/sites", {"photos": [_photo(tmp_path / "b.jpg")]})
    assert code == 400 and "already" in err["error"]
    assert sorted(os.listdir(root)) == sorted(["site-a", building]), "no orphan site"
    assert call(s, "GET", "/state")[1]["site"]["slug"] == building, "the open site did not move"
    jobs.stop()


def test_a_build_that_cannot_start_fails_and_frees_the_next(tmp_path):
    from terminus.server import sites

    jobs = sites.Jobs(command=lambda: [str(tmp_path / "no-such-program")])
    assert jobs.start("a", str(tmp_path)) is None
    assert jobs.state["status"] == "failed" and "could not start" in jobs.state["error"]
    jobs._command = child(OK_CHILD)
    jobs.start("b", str(tmp_path)).join(10)
    assert jobs.state["status"] == "done"


def test_a_build_that_writes_nothing_is_not_done(tmp_path):
    jobs = FakeJobs("print('@step skymask')")
    jobs.start("s", str(tmp_path)).join(10)
    assert jobs.state["status"] == "failed"
    assert jobs.state["error"] == (
        "the build finished but wrote no equirect.png, photo_mask.yaml, horizon.yaml"
    )


def test_the_build_log_is_capped_but_complete_in_sidecar_log(tmp_path, caplog):
    from terminus.server import sites

    jobs = FakeJobs("[print(i) for i in range(100)]")
    jobs.start("s", str(tmp_path)).join(10)
    assert jobs.state["log"] == [str(i) for i in range(100 - sites.MAX_LOG, 100)]
    assert "build: 0" in caplog.text and "build: 99" in caplog.text


@pytest.mark.skipif(os.name == "nt", reason="POSIX process groups; Windows uses taskkill /T")
def test_stop_kills_the_build_and_its_grandchildren_and_closes_jobs(tmp_path):
    from terminus.server import sites

    pidfile = tmp_path / "grandchild.pid"
    script = (
        "import subprocess, sys, time, pathlib; "
        f"g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
        f"pathlib.Path({str(pidfile)!r}).write_text(str(g.pid)); print('@step mosaic', flush=True); time.sleep(60)"
    )
    jobs = FakeJobs(script)
    t = jobs.start("s", str(tmp_path))
    for _ in range(200):
        if pidfile.exists() and pidfile.read_text():
            break
        time.sleep(0.05)
    grandchild = int(pidfile.read_text())
    jobs.stop()
    t.join(10)
    for _ in range(100):  # the grandchild was killed with its group
        try:
            os.kill(grandchild, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        raise AssertionError("the Hugin-like grandchild outlived stop()")
    with pytest.raises(sites.SiteError, match="shutting down"):
        jobs.start("again", str(tmp_path))


def test_the_build_child_exits_when_the_sidecar_goes_away(tmp_path):
    """The child holds a pipe from the sidecar; when it closes, the child exits
    at once instead of finishing a build nobody will see."""
    p = subprocess.Popen(
        [sys.executable, "-c", (
            "import threading, time; from terminus.server import sites; "
            "threading.Thread(target=sites.exit_on_eof, daemon=True).start(); time.sleep(30)"
        )],  # fmt: skip
        stdin=subprocess.PIPE,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
    )
    time.sleep(1.5)
    p.stdin.close()
    assert p.wait(timeout=10) == 3


def test_create_site_rolls_back_a_failed_copy(tmp_path, monkeypatch):
    import shutil

    from terminus.server import sites

    a, b = _photo(tmp_path / "a.jpg"), _photo(tmp_path / "b.jpg")
    real = shutil.copy2

    def copy2(src, dst):
        if src == b:
            raise OSError(28, "No space left on device")
        return real(src, dst)

    monkeypatch.setattr(sites.shutil, "copy2", copy2)
    root = tmp_path / "sites"
    with pytest.raises(sites.SiteError, match="could not copy b.jpg: No space left"):
        sites.create_site(str(root), [a, b])
    assert os.listdir(root) == []


def test_create_site_refuses_relative_paths_and_links(tmp_path, monkeypatch):
    from terminus.server import sites

    real = _photo(tmp_path / "a.jpg")
    (tmp_path / "link.jpg").symlink_to(real)
    monkeypatch.chdir(tmp_path)
    for bad in (["a.jpg"], [str(tmp_path / "link.jpg")]):
        with pytest.raises(sites.SiteError):
            sites.create_site(str(tmp_path / "sites"), bad)
    with pytest.raises(sites.SiteError):
        sites.create_site(str(tmp_path / "sites"), [real] * 501)


def test_a_slug_with_a_trailing_newline_is_not_a_slug(tmp_path):
    from terminus.server import sites

    (tmp_path / "site-a\n").mkdir()  # exists, so only the slug check can refuse it
    with pytest.raises(sites.SiteError, match="not a site name"):
        sites.site_dir(str(tmp_path), "site-a\n")


def test_list_sites_shows_only_site_folders(tmp_path):
    from terminus.server import sites

    (tmp_path / "site-a").mkdir()
    (tmp_path / "Not A Site").mkdir()
    (tmp_path / "site-file").write_text("x")
    assert sites.list_sites(str(tmp_path)) == ["site-a"]


def test_opening_or_creating_a_site_resets_the_spin(serve, tmp_path):
    root = tmp_path / "sites"
    _site(root, slug="site-a")
    s = serve(sites_root=str(root), jobs=FakeJobs())
    call(s, "POST", "/site/open", {"slug": "site-a"})
    call(s, "POST", "/site/spin", {"deg": 90})
    assert call(s, "POST", "/site/open", {"slug": "site-a"})[1]["spin"] == 0.0
    call(s, "POST", "/site/spin", {"deg": 90})
    assert call(s, "POST", "/sites", {"photos": [_photo(tmp_path / "a.jpg")]})[1]["spin"] == 0.0


def test_a_site_deleted_from_disk_closes_with_a_log_line(serve, tmp_path, caplog):
    import shutil

    d = _site(tmp_path)
    s = serve(sites_root=str(tmp_path))
    call(s, "POST", "/site/open", {"slug": "site-2026-10-05"})
    shutil.rmtree(d)
    assert call(s, "GET", "/state")[1]["site"] is None
    assert "gone from disk" in caplog.text


def test_none_strings_in_the_meta_never_reach_the_ui(tmp_path):
    """The meta block is a Python literal, so absent values come back as "None"."""
    from terminus.server import views

    d = _site(tmp_path, oriented=True)
    text = (d / "oriented.yaml").read_text().replace("'fit_settled': True", "'fit_settled': 'None'")
    text = text.replace("'residual': 0.4", "'residual': 'None', 'reason': 'None'")
    text = text.replace("{'az': 300.0,", "{'az': 'None', 'x': 300.0,")
    (d / "oriented.yaml").write_text(text)
    h = views.horizon(str(d))
    conforms("Horizon", h)
    assert h["settled"] is None
    assert h["fit"][0] == {"az": 90.0, "alt": 5.4, "used": True}
    assert [f["az"] for f in h["fit"]] == [90.0, 200.0, 250.0], "an az of 'None' is no column"


def test_a_scope_only_sweep_has_no_photo_to_place(serve, tmp_path):
    from terminus.export import write_mask

    d = _site(tmp_path)
    (d / "photo_mask.yaml").unlink()
    write_mask(d / "oriented.yaml", {10: {"alt": 5.0, "type": "tree"}}, [], {"backend": "segment"})
    s = serve(sites_root=str(tmp_path))
    call(s, "POST", "/site/open", {"slug": "site-2026-10-05"})
    h = call(s, "GET", "/site/horizon")[1]
    assert h["oriented"] is True and h["solution"] is None and h["backend"] == "segment"
    assert call(s, "GET", "/site/disc.jpg")[0] == 204, "no rotation: nothing places the photo"


def test_the_disc_photo_renders_without_a_coverage_array(serve, tmp_path):
    d = _site(tmp_path)
    (d / "equirect.coverage.npy").unlink()
    s = serve(sites_root=str(tmp_path))
    call(s, "POST", "/site/open", {"slug": "site-2026-10-05"})
    code, data = call(s, "GET", "/site/disc.jpg")
    assert code == 200 and data[:3] == b"\xff\xd8\xff"


def test_disc_rings_sit_at_their_altitudes(serve, tmp_path):
    from terminus import polar

    _site(tmp_path)
    s = serve(sites_root=str(tmp_path))
    call(s, "POST", "/site/open", {"slug": "site-2026-10-05"})
    disc = call(s, "GET", "/site/disc")[1]
    conforms("Disc", disc)
    for ring in disc["rings"]:  # a ring's radius is where disc_xy puts that altitude
        x, y = polar.disc_xy(0.0, ring["alt"], polar.SIZE, polar.FLOOR_DEG)
        assert ring["r"] == pytest.approx(disc["centre"] - y, abs=0.1)


@pytest.mark.parametrize(
    "name,content,path",
    [
        ("photo_mask.yaml", b"horizon: [unclosed", "/site/horizon"),
        ("equirect.png", b"not a png", "/site/panorama.jpg"),
    ],
)
def test_unreadable_site_files_are_a_clean_422_not_a_dropped_connection(
    serve, tmp_path, name, content, path
):
    d = _site(tmp_path)
    (d / name).write_bytes(content)
    s = serve(sites_root=str(tmp_path))
    call(s, "POST", "/site/open", {"slug": "site-2026-10-05"})
    code, err = call(s, "GET", path)
    assert code == 422 and "could not be read" in err["error"]
    assert str(tmp_path) not in err["error"]


def test_an_unexpected_error_is_a_500_and_the_server_lives(serve, tmp_path, monkeypatch):
    from terminus.server import views

    _site(tmp_path)
    s = serve(sites_root=str(tmp_path))
    call(s, "POST", "/site/open", {"slug": "site-2026-10-05"})
    monkeypatch.setattr(views, "disc", lambda d: 1 / 0)
    code, err = call(s, "GET", "/site/disc")
    assert code == 500 and err == {"error": "internal error; see sidecar.log"}
    assert call(s, "GET", "/health")[0] == 200


@pytest.mark.skipif(os.name == "nt", reason="fake Hugin tools are shell scripts")
def test_the_real_sidecar_builds_with_its_bundled_hugin_and_kills_it_on_exit(tmp_path):
    """End to end in real processes: --hugin reaches the build child (the fake
    pto_gen it runs is the bundled one), and closing the sidecar's stdin kills
    the build's whole tree, Hugin tool included."""
    import stat

    from terminus import mosaic

    hugin, pid = tmp_path / "hugin", tmp_path / "pto_gen.pid"
    hugin.mkdir()
    for name in mosaic.TOOLS:
        body = f"echo $$ > {pid}\nsleep 60\n" if name == "pto_gen" else "exit 0\n"
        tool = hugin / name
        tool.write_text("#!/bin/sh\n" + body)
        tool.chmod(tool.stat().st_mode | stat.S_IEXEC)
    sites_root = tmp_path / "sites"
    p = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "terminus.server",
            "--sites",
            str(sites_root),
            "--hugin",
            str(hugin),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
    )
    watchdog = threading.Timer(60, p.kill)
    watchdog.daemon = True
    watchdog.start()
    try:
        s = types.SimpleNamespace(**json.loads(p.stdout.readline()))
        photos = [_photo(tmp_path / f"{i}.jpg") for i in range(2)]
        assert call(s, "POST", "/sites", {"photos": photos})[0] == 200
        for _ in range(400):
            if pid.exists() and pid.read_text().strip():
                break
            time.sleep(0.05)
        tool_pid = int(pid.read_text())  # the bundled pto_gen is running
        p.stdin.close()
        assert p.wait(timeout=20) == 0
        for _ in range(100):
            try:
                os.kill(tool_pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:
            raise AssertionError("the Hugin tool outlived the sidecar")
    finally:
        watchdog.cancel()
        p.kill()
        p.wait()


# ---- shutdown order, kills and reaping ----


def test_a_tiny_negative_spin_wraps_to_zero_not_360(serve):
    s = serve()
    st = call(s, "POST", "/site/spin", {"deg": -1e-20})[1]
    assert st["spin"] == 0.0
    conforms("AppState", st)


@pytest.mark.parametrize("failing", [None, "park", "stop"])
def test_shutdown_parks_first_and_every_step_survives_a_failure(failing):
    calls = []

    def step(name):
        def run():
            calls.append(name)
            if name == failing:
                raise RuntimeError(name)

        return run

    server = types.SimpleNamespace(
        scope=types.SimpleNamespace(park=step("park"), close=step("unlink")),
        jobs=types.SimpleNamespace(stop=step("stop")),
        server_close=step("close"),
    )
    if failing:
        with pytest.raises(RuntimeError):
            server_module._shutdown(server)
    else:
        server_module._shutdown(server)
    assert calls == ["park", "unlink", "stop", "close"]


@pytest.mark.skipif(os.name == "nt", reason="POSIX process groups")
def test_kill_tree_takes_an_orphaned_grandchild_and_tolerates_a_gone_group(tmp_path):
    from terminus.server import sites

    pidfile = tmp_path / "g.pid"
    child = subprocess.Popen(
        [sys.executable, "-c", (
            "import subprocess, sys, pathlib; "
            "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
            f"pathlib.Path({str(pidfile)!r}).write_text(str(g.pid))"
        )],  # fmt: skip
        start_new_session=True,
    )
    child.wait(timeout=20)  # the child has exited; its grandchild lives on in the group
    grandchild = int(pidfile.read_text())
    sites.kill_tree(child)
    for _ in range(100):
        try:
            os.kill(grandchild, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        raise AssertionError("the grandchild survived kill_tree")
    sites.kill_tree(child)  # the group is gone now: still no error


def test_a_finished_build_takes_its_leftover_tool_down_and_stop_leaves_its_pid_alone(
    tmp_path, monkeypatch
):
    """The watcher sweeps the group before reaping the child; after that the pid
    may belong to anyone, so stop() must not signal it."""
    from terminus.server import sites

    pidfile = tmp_path / "g.pid"
    jobs = FakeJobs(
        "import subprocess, sys, pathlib; "
        "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], "
        "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); "
        f"pathlib.Path({str(pidfile)!r}).write_text(str(g.pid))"
    )  # off the pipe, so the build ends with the tool still running
    real_killpg, signalled = os.killpg, []

    def killpg(pgid, sig):  # the child must still be an unreaped zombie
        signalled.append(os.path.exists(f"/proc/{pgid}"))
        real_killpg(pgid, sig)

    monkeypatch.setattr(sites.os, "killpg", killpg)
    jobs.start("s", str(tmp_path)).join(20)
    assert signalled == [True], "the group must be signalled before the child is reaped"
    grandchild = int(pidfile.read_text())
    for _ in range(100):
        try:
            os.kill(grandchild, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        raise AssertionError("the build's leftover tool outlived it")
    monkeypatch.setattr(sites.os, "killpg", lambda *a: pytest.fail("signalled a reaped pid"))
    jobs.stop()


def test_a_build_reaped_by_its_timeout_kill_still_reports_the_timeout(tmp_path, monkeypatch):
    """The timer's kill_tree reaps the child, racing the watcher's waitid."""
    from terminus.server import sites

    def reaped(*a):
        raise ChildProcessError(10, "No child processes")

    monkeypatch.setattr(sites.os, "waitid", reaped)
    jobs = FakeJobs("import time; time.sleep(30)", timeout=0.3)
    jobs.start("s", str(tmp_path)).join(20)
    assert "took longer than" in jobs.state["error"], jobs.state["error"]


def test_a_child_that_dies_before_any_step_is_named_the_build(tmp_path):
    jobs = FakeJobs("import sys; sys.exit(2)")
    jobs.start("s", str(tmp_path)).join(10)
    assert jobs.state["error"] == "the build stopped (exit 2)"


def test_the_watcher_fails_the_job_even_if_the_kill_fails(tmp_path, monkeypatch):
    from terminus.server import sites

    killed = []

    def broken_kill(proc):
        killed.append(proc.pid)
        proc.kill()
        raise ProcessLookupError("gone")

    monkeypatch.setattr(sites, "kill_tree", broken_kill)
    jobs = FakeJobs(
        "print('@step mosaic'); print('hello', flush=True); import time; time.sleep(30)"
    )
    monkeypatch.setattr(jobs, "_log", lambda line: 1 / 0)  # a bug while watching
    jobs.start("s", str(tmp_path)).join(10)
    assert jobs.state["status"] == "failed"
    assert jobs.state["error"].startswith("lost track of the build: ZeroDivisionError")
    assert killed == [jobs._proc.pid], "the watcher must still try to kill the build"


def test_a_finished_build_leaves_no_pipe_open(tmp_path):
    jobs = FakeJobs()
    jobs.start("s", str(tmp_path)).join(10)
    assert jobs._proc.stdin.closed and jobs._proc.stdout.closed


def test_a_failure_creating_the_site_folder_is_named_as_such(tmp_path, monkeypatch):
    from terminus.server import sites

    real = os.mkdir

    def mkdir(path, *a, **k):
        if str(path).endswith("photos"):
            raise OSError(13, "Permission denied")
        return real(path, *a, **k)

    monkeypatch.setattr(sites.os, "mkdir", mkdir)
    with pytest.raises(
        sites.SiteError, match="could not create the photos folder: Permission denied"
    ):
        sites.create_site(str(tmp_path / "sites"), [_photo(tmp_path / "a.jpg")])
    assert os.listdir(tmp_path / "sites") == []


@pytest.mark.skipif(os.name == "nt", reason="POSIX process groups")
def test_a_build_child_that_loses_the_sidecar_takes_its_tool_down(tmp_path):
    """Leader of its own group (as Jobs starts it): on EOF it kills the group,
    so a Hugin tool it started cannot outlive a sidecar that died hard."""
    pidfile = tmp_path / "tool.pid"
    p = subprocess.Popen(
        [sys.executable, "-c", (
            "import subprocess, sys, threading, time, pathlib; from terminus.server import sites; "
            "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
            f"pathlib.Path({str(pidfile)!r}).write_text(str(g.pid)); "
            "threading.Thread(target=sites.exit_on_eof, daemon=True).start(); time.sleep(30)"
        )],  # fmt: skip
        stdin=subprocess.PIPE,
        start_new_session=True,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
    )
    for _ in range(200):
        if pidfile.exists() and pidfile.read_text():
            break
        time.sleep(0.05)
    tool = int(pidfile.read_text())
    p.stdin.close()
    p.wait(timeout=10)
    for _ in range(100):
        try:
            os.kill(tool, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        raise AssertionError("the tool outlived the build child")


@pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0, reason="POSIX permissions, not as root")
def test_an_unwritable_sites_folder_says_so(serve, tmp_path):
    root = tmp_path / "sites"
    root.mkdir()
    root.chmod(0o555)
    try:
        s = serve(sites_root=str(root), jobs=FakeJobs())
        code, err = call(s, "POST", "/sites", {"photos": [_photo(tmp_path / "a.jpg")]})
        assert code == 400 and err["error"].startswith("could not create a site folder")
    finally:
        root.chmod(0o755)


def test_stop_after_a_finished_build_is_quiet(tmp_path):
    """A normal quit after a build: the group is long gone, and stop() must not
    raise (it would skip the rest of shutdown)."""
    jobs = FakeJobs()
    jobs.start("s", str(tmp_path)).join(10)
    jobs.stop()
    jobs.stop()


@pytest.mark.skipif(os.name == "nt", reason="fake Hugin tools are shell scripts")
def test_the_pipeline_entry_point_exits_and_takes_its_tool_down_on_eof(tmp_path):
    """`python -m terminus.server --pipeline` itself wires the EOF exit (not just
    a test calling exit_on_eof)."""
    import stat

    from terminus import mosaic

    hugin, pid = tmp_path / "hugin", tmp_path / "pto_gen.pid"
    hugin.mkdir()
    for name in mosaic.TOOLS:
        tool = hugin / name
        tool.write_text(
            "#!/bin/sh\n" + (f"echo $$ > {pid}\nsleep 60\n" if name == "pto_gen" else "exit 0\n")
        )
        tool.chmod(tool.stat().st_mode | stat.S_IEXEC)
    d = _site(tmp_path, panorama=False)
    _photo(d / "photos" / "b.jpg")
    p = subprocess.Popen(
        [sys.executable, "-m", "terminus.server", "--pipeline", str(d), "--hugin", str(hugin)],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
    )
    for _ in range(400):
        if pid.exists() and pid.read_text().strip():
            break
        time.sleep(0.05)
    tool = int(pid.read_text())
    p.stdin.close()
    p.wait(timeout=20)
    for _ in range(100):
        try:
            os.kill(tool, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        raise AssertionError("the pipeline's Hugin tool outlived it")


def test_a_busy_build_refuses_before_copying_anything(serve, tmp_path, monkeypatch):
    from terminus.server import sites

    jobs = FakeJobs()
    monkeypatch.setattr(jobs, "busy", lambda: True)
    monkeypatch.setattr(sites, "create_site", lambda *a: pytest.fail("copied photos while busy"))
    s = serve(sites_root=str(tmp_path / "sites"), jobs=jobs)
    assert call(s, "POST", "/sites", {"photos": [_photo(tmp_path / "a.jpg")]})[0] == 400


def test_a_drop_that_loses_the_race_to_build_leaves_no_folder(serve, tmp_path, monkeypatch):
    from terminus.server import sites

    jobs = FakeJobs()
    monkeypatch.setattr(jobs, "busy", lambda: False)  # passes the pre-check...

    def lose(slug, d):  # ...but another drop started building first
        raise sites.SiteError("a site is already being built")

    monkeypatch.setattr(jobs, "start", lose)
    root = tmp_path / "sites"
    s = serve(sites_root=str(root), jobs=jobs)
    assert call(s, "POST", "/sites", {"photos": [_photo(tmp_path / "a.jpg")]})[0] == 400
    assert os.listdir(root) == []


def test_identity_is_handed_out_as_a_copy_and_backend_must_be_text(tmp_path):
    from terminus.export import write_mask
    from terminus.server import views

    sol = views.solution({"oriented": False})
    sol["yaw"] = 99.0
    assert views.IDENTITY["yaw"] == 0.0
    d = _site(tmp_path)
    write_mask(
        d / "photo_mask.yaml", {0: {"alt": 5.0, "type": ""}}, [], {"oriented": False, "backend": 3}
    )
    assert views.horizon(str(d))["backend"] is None


def _label(path, classes):
    """A warped LABEL layer: the class id in every channel, covering the canvas."""
    import numpy as np
    from PIL import Image

    rgba = np.zeros(classes.shape + (4,), np.uint8)
    rgba[..., :3] = classes[..., None]
    rgba[..., 3] = 255
    Image.fromarray(rgba, "RGBA").save(
        path, tiffinfo={286: ((0, 1),), 287: ((0, 1),), 282: ((1, 1),), 283: ((1, 1),)}
    )


def _stitched(d, names=("a.jpg", "b.jpg"), dropped=("c.jpg",), labels=None, off_canvas=()):
    """A site whose photos `mosaic` has stitched: final.pto, one layer per placed
    photo (left half sky, right half ground), and the manifest. No Hugin.
    `labels` (one class map per photo) adds segmentation label layers;
    `off_canvas` photos get no layer, as nona does for a frame off the canvas."""
    import numpy as np
    from PIL import Image

    work = d / "work"
    work.mkdir()
    lines = ['p f2 w8 h4 v360 n"TIFF_m"'] + [f'i w8 h8 n"{n}"' for n in names]
    (work / "final.pto").write_text("\n".join(lines) + "\n")
    for i, n in enumerate(names):
        _photo(d / "photos" / n)
        if n in off_canvas:
            continue
        rgba = np.zeros((4, 8, 4), np.uint8)
        rgba[:, :4, :3], rgba[:, 4:, :3], rgba[..., 3] = (80, 120, 220), (120, 80, 40), 255
        Image.fromarray(rgba, "RGBA").save(
            work / f"layer{i:04d}.tif",
            tiffinfo={286: ((0, 1),), 287: ((0, 1),), 282: ((1, 1),), 283: ((1, 1),)},
        )
    if labels is not None:  # remapped after the photos, as a stitch does
        for i, classes in enumerate(labels):
            _label(work / f"label{i:04d}.tif", classes)
    for n in dropped:
        _photo(d / "photos" / n)
    (d / "equirect.manifest.json").write_text(
        json.dumps({"image_dir": str(d / "photos"), "dropped": list(dropped)})
    )


def _reblend(d, *off):
    from terminus import cli, skymask

    real = skymask.heuristic_sky
    skymask.heuristic_sky = lambda rgb, valid, px_per_deg: (rgb[..., 2] > rgb[..., 0]) & valid
    try:
        args = ["reblend", str(d / "work"), "--out", str(d / "equirect")]
        cli.main(args + [a for n in off for a in ("--off", n)])
    finally:
        skymask.heuristic_sky = real


def test_reblend_leaves_frames_out_and_lists_every_photo(tmp_path, capsys):
    import numpy as np

    d = _site(tmp_path, panorama=False)
    _stitched(d)
    _reblend(d, "b.jpg", "a.jpg-typo")
    assert "a.jpg-typo is not in this panorama" in capsys.readouterr().out
    listed = json.loads((d / "equirect.frames.json").read_text())
    conforms("Frames", listed)
    by = {f["name"]: f for f in listed["frames"]}
    assert set(by) == {"a.jpg", "b.jpg", "c.jpg"}
    assert by["a.jpg"] == {"name": "a.jpg", "layer": "layer0000.tif", "box": [0, 0, 8, 4],
                           "off": False, "dropped": False}  # fmt: skip
    assert by["b.jpg"]["off"] and by["b.jpg"]["layer"] == "layer0001.tif"
    assert by["c.jpg"] == {"name": "c.jpg", "layer": None, "box": None, "off": False,
                           "dropped": True}  # fmt: skip
    coverage = np.load(d / "equirect.coverage.npy")
    assert (coverage == 1).all(), "b is off, so only a was blended"
    sky = np.load(d / "equirect.sky.npy")
    assert sky[:, :4].all() and not sky[:, 4:].any()


def test_reblend_refuses_to_turn_every_frame_off(tmp_path, capsys):
    d = _site(tmp_path, panorama=False)
    _stitched(d)
    with pytest.raises(SystemExit):
        _reblend(d, "a.jpg", "b.jpg")
    assert "every frame is turned off" in capsys.readouterr().err


def test_skymask_reads_the_voted_sky_and_says_so(tmp_path, capsys):
    import numpy as np
    from PIL import Image

    from terminus import cli
    from terminus.export import load_columns

    d = tmp_path
    img = np.zeros((36, 72, 3), np.uint8)
    img[:] = (120, 80, 40)  # the panorama looks like ground everywhere...
    Image.fromarray(img).save(d / "p.png")
    sky = np.zeros((36, 72), bool)
    sky[:12] = True  # ...but the frames voted the top third sky
    np.save(d / "p.sky.npy", sky)
    cli.main(
        ["skymask", str(d / "p.png"), "--sky", str(d / "p.sky.npy"), "--out", str(d / "m.yaml")]
    )
    meta, cols = load_columns(str(d / "m.yaml"))
    assert meta["backend"] == "per-frame vote (unrecorded)", "no record: it does not guess"
    assert abs(cols[0]["alt"] - 30.0) < 3.0
    (d / "p.vote.json").write_text('{"backend": "segment (per-frame vote)"}')
    out = str(d / "m.yaml")
    cli.main(["skymask", str(d / "p.png"), "--sky", str(d / "p.sky.npy"), "--out", out])
    assert load_columns(out)[0]["backend"] == "segment (per-frame vote)"
    np.save(d / "bad.npy", sky[:, :10])
    with pytest.raises(SystemExit):
        cli.main(["skymask", str(d / "p.png"), "--sky", str(d / "bad.npy")])
    assert "does not match the image" in capsys.readouterr().err


def _curatable(tmp_path):
    root = tmp_path / "sites"
    d = _site(root, panorama=True)
    _stitched(d)
    _reblend(d)
    return root, d


def test_frames_routes_serve_a_curated_site(serve, tmp_path):
    root, d = _curatable(tmp_path)
    s = serve(sites_root=str(root), jobs=FakeJobs())
    call(s, "POST", "/site/open", {"slug": d.name})
    code, listed = call(s, "GET", "/site/frames")
    assert code == 200 and [f["name"] for f in listed["frames"]] == ["a.jpg", "b.jpg", "c.jpg"]
    conforms("Frames", listed)
    code, png = call(s, "GET", "/site/frame/footprint.png?name=a.jpg")
    assert code == 200 and png[:4] == b"\x89PNG"
    assert call(s, "GET", "/site/frame/footprint.png?name=c.jpg")[0] == 204, "dropped: no layer"
    code, jpg = call(s, "GET", "/site/frame/thumb.jpg?name=c.jpg")
    assert code == 200 and jpg[:2] == b"\xff\xd8"
    code, png = call(s, "GET", "/site/disagree.png")
    assert code == 200 and png[:4] == b"\x89PNG"


def test_frame_routes_refuse_a_name_the_site_does_not_list(serve, tmp_path):
    root, d = _curatable(tmp_path)
    s = serve(sites_root=str(root), jobs=FakeJobs())
    call(s, "POST", "/site/open", {"slug": d.name})
    for q in ("?name=../../etc/passwd", "?name=zz.jpg", "", "?name=a.jpg&name=b.jpg"):
        code, err = call(s, "GET", "/site/frame/thumb.jpg" + q)
        assert code == 400, q


def test_an_uncurated_site_has_no_frames_yet(serve, tmp_path):
    root = tmp_path / "sites"
    d = _site(root)
    s = serve(sites_root=str(root), jobs=FakeJobs())
    call(s, "POST", "/site/open", {"slug": d.name})
    assert call(s, "GET", "/site/frames")[0] == 204
    assert call(s, "GET", "/site/disagree.png")[0] == 204


def test_turning_photos_off_records_them_and_reblends(serve, tmp_path):
    from terminus.server import sites

    root, d = _curatable(tmp_path)
    jobs = FakeJobs("import sys; print('args', sys.argv[1:])")
    s = serve(sites_root=str(root), jobs=jobs)
    call(s, "POST", "/site/open", {"slug": d.name})
    assert call(s, "POST", "/site/frames", {"off": ["zz.jpg"]})[0] == 400
    assert call(s, "POST", "/site/frames", {"off": "a.jpg"})[0] == 400
    assert sites.frames_off(str(d)) == [], "a refused request changes nothing"
    code, st = call(s, "POST", "/site/frames", {"off": ["b.jpg"]})
    assert code == 200 and st["job"]["site"] == d.name
    jobs.wait()
    assert sites.frames_off(str(d)) == ["b.jpg"]
    assert "'--kind', 'reblend'" in jobs.state["log"][0]


def test_restitch_runs_a_full_build(serve, tmp_path):
    root, d = _curatable(tmp_path)
    jobs = FakeJobs("import sys; print('args', sys.argv[1:])")
    s = serve(sites_root=str(root), jobs=jobs)
    call(s, "POST", "/site/open", {"slug": d.name})
    assert call(s, "POST", "/site/frames", {"off": [], "restitch": "yes"})[0] == 400
    assert call(s, "POST", "/site/frames", {"off": [], "restitch": True})[0] == 200
    jobs.wait()
    assert "'--kind', 'build'" in jobs.state["log"][0]


def test_curation_is_refused_while_a_build_runs(serve, tmp_path, monkeypatch):
    from terminus.server import sites

    root, d = _curatable(tmp_path)
    jobs = FakeJobs()
    monkeypatch.setattr(jobs, "busy", lambda: True)
    s = serve(sites_root=str(root), jobs=jobs)
    call(s, "POST", "/site/open", {"slug": d.name})
    assert call(s, "POST", "/site/frames", {"off": ["b.jpg"]})[0] == 400
    assert sites.frames_off(str(d)) == []


def test_a_build_folds_progress_events_into_its_state(tmp_path):
    script = (
        "import json\n"
        "e = lambda **k: print('@event ' + json.dumps(k), flush=True)\n"
        "e(phase='matching'); e(detail='comparing neighbouring photos')\n"
        "e(name='a.jpg', state='listed'); e(name='b.jpg', state='listed')\n"
        "e(pair=['a.jpg', 'b.jpg'], matches=40, working=['a.jpg', 'b.jpg'])\n"
        "e(pair=['a.jpg', 'c.jpg'], matches=0, working=['a.jpg', 'c.jpg'])\n"
        "e(name='a.jpg', state='matched', points=40)\n"
        "e(outline=1); e(outline=2)\n"
        "e(phase='placing', canvas=[8, 4])\n"
        "e(name='a.jpg', state='placed', layer='layer0000.tif', box=[0, 0, 8, 4])\n"
        "e(working=['a.jpg'], detail='feathering the seams')\n"
        "print('@step skymask', flush=True)\n"
        "print('@event not json')\n"
        "import sys; sys.exit(1)\n"
    )
    jobs = FakeJobs(script)
    jobs.start("s", str(tmp_path)).join(10)
    st = jobs.state
    conforms("Job", st)
    assert st["phase"] == "placing" and st["canvas"] == [8, 4]
    assert st["detail"] is None and st["active"] == [], "a new step clears both"
    assert st["compared"] == 2 and st["pairs"] == [{"a": "a.jpg", "b": "b.jpg", "matches": 40}]
    assert st["outline"] == 2, "the horizon so far was redrawn twice"
    a = next(f for f in st["frames"] if f["name"] == "a.jpg")
    assert a == {"name": "a.jpg", "state": "placed", "points": 40, "layer": "layer0000.tif",
                 "box": [0, 0, 8, 4]}, "fields merge: placing keeps the matched points"  # fmt: skip


def test_the_panorama_and_disc_show_the_feathered_blend_when_there_is_one(serve, tmp_path):
    import io

    import numpy as np
    from PIL import Image

    root = tmp_path / "sites"
    d = _site(root)  # equirect.png: blue sky over black ground
    shown = np.zeros((36, 72, 3), np.uint8)
    shown[:] = (220, 30, 30)  # the display blend, unmistakably red
    Image.fromarray(shown).save(d / "equirect.display.jpg")
    s = serve(sites_root=str(root), jobs=FakeJobs())
    call(s, "POST", "/site/open", {"slug": d.name})
    code, pano = call(s, "GET", "/site/panorama.jpg")
    assert code == 200 and pano == (d / "equirect.display.jpg").read_bytes()
    code, disc = call(s, "GET", "/site/disc.jpg")
    rgb = np.asarray(Image.open(io.BytesIO(disc)).convert("RGB")).reshape(-1, 3)
    lit = rgb[rgb.sum(axis=1) > 60]
    assert code == 200 and lit[:, 0].mean() > 2 * lit[:, 2].mean(), "the disc is drawn from it too"


def test_reblend_writes_the_class_maps_the_horizon_is_read_from(tmp_path):
    import numpy as np

    from terminus.skymask import SKY_CLASS_ADE20K as SKY

    d = _site(tmp_path, panorama=False)
    _stitched(d)  # two frames, each sky on the left half
    _reblend(d)
    terrain = np.load(d / "equirect.terrain.classes.npy")
    strict = np.load(d / "equirect.strict.classes.npy")
    cover = np.load(d / "equirect.cover.npy")
    assert (cover == 2).all()
    assert (terrain[:, :4] == SKY).all() and (terrain[:, 4:] != SKY).all()
    assert (strict == terrain).all(), "both frames agree everywhere, so strict == voted"


def test_the_horizon_map_has_actual_and_planning_and_planning_is_never_lower(tmp_path):
    import numpy as np

    from terminus import cli
    from terminus.export import load_columns
    from terminus.skymask import SKY_CLASS_ADE20K as SKY

    H, W = 180, 360
    terrain = np.full((H, W), 1)
    terrain[:80] = SKY  # sky above alt 10 everywhere
    strict = terrain.copy()
    strict[70:80, :90] = 1  # one frame saw terrain up to alt 20 in the first quarter (wind)
    base = tmp_path / "equirect"
    np.save(f"{base}.terrain.classes.npy", terrain)
    np.save(f"{base}.strict.classes.npy", strict)
    np.save(f"{base}.cover.npy", np.full((H, W), 2, np.int16))
    np.save(f"{base}.coverage.npy", np.ones((H, W)))
    cli.main(["horizon", str(base), "--out", str(tmp_path / "h.yaml")])
    meta, cols = load_columns(str(tmp_path / "h.yaml"))
    assert meta["oriented"] is False and len(cols) == 360
    assert abs(cols[200]["alt"] - 10.0) < 1.01 and cols[200]["planning"] == cols[200]["alt"]
    assert abs(cols[45]["planning"] - 20.0) < 1.01, "planning rises to the terrain any frame saw"
    assert all(c["planning"] >= c["alt"] for c in cols.values())


def test_the_outline_is_a_disc_overlay_once_the_class_maps_exist(serve, tmp_path):
    root = tmp_path / "sites"
    d = _site(root)
    s = serve(sites_root=str(root), jobs=FakeJobs())
    call(s, "POST", "/site/open", {"slug": d.name})
    assert call(s, "GET", "/site/outline.png")[0] == 204, "built before the class maps"
    _stitched(d)
    _reblend(d)
    code, png = call(s, "GET", "/site/outline.png")
    assert code == 200 and png[:4] == b"\x89PNG"


def test_a_stitch_segments_each_photo_when_segmentation_is_installed(tmp_path, monkeypatch):
    from terminus.server import sites

    d = _site(tmp_path, panorama=False)
    monkeypatch.setattr(sites, "segmentation_installed", lambda: True)
    assert "--segment" in dict(sites.pipeline(str(d), "build"))["mosaic"]
    monkeypatch.setattr(sites, "segmentation_installed", lambda: False)
    assert "--segment" not in dict(sites.pipeline(str(d), "build"))["mosaic"]


def test_a_reblend_draws_the_horizon_so_far_after_each_frame(serve, tmp_path, capsys):
    from terminus import mosaic

    root = tmp_path / "sites"
    d = _site(root)
    _stitched(d)
    seen = []
    mosaic.EVENTS = seen.append
    try:
        _reblend(d)
    finally:
        mosaic.EVENTS = None
    assert [e["outline"] for e in seen if "outline" in e] == [1, 2], "one redraw per frame"
    s = serve(sites_root=str(root), jobs=FakeJobs())
    call(s, "POST", "/site/open", {"slug": d.name})
    code, png = call(s, "GET", "/site/progress.png")
    assert code == 200 and png[:4] == b"\x89PNG"


def test_a_site_can_be_renamed_without_moving_its_folder(serve, tmp_path):
    root = tmp_path / "sites"
    d = _site(root)
    s = serve(sites_root=str(root), jobs=FakeJobs())
    sites = call(s, "GET", "/sites")[1]["sites"]
    assert sites[0]["name"] == d.name, "a site is named after its folder until renamed"
    conforms("SiteList", {"sites": sites})
    for bad in ("", "   ", "x" * 81, 3):
        assert call(s, "POST", "/site/rename", {"slug": d.name, "name": bad})[0] == 400, bad
    assert call(s, "POST", "/site/rename", {"slug": "nope", "name": "Back yard"})[0] == 400
    assert call(s, "POST", "/site/rename", {"slug": d.name, "name": "  Back yard  "})[0] == 200
    assert call(s, "GET", "/sites")[1]["sites"][0]["name"] == "Back yard"
    assert d.is_dir(), "the folder keeps its slug"


def test_deleting_a_site_removes_it_and_closes_it(serve, tmp_path):
    root = tmp_path / "sites"
    a, b = _site(root, slug="site-a"), _site(root, slug="site-b")
    s = serve(sites_root=str(root), jobs=FakeJobs())
    call(s, "POST", "/site/open", {"slug": "site-a"})
    code, st = call(s, "POST", "/site/delete", {"slug": "site-a"})
    assert code == 200 and st["site"] is None and not a.exists() and b.exists()
    assert call(s, "POST", "/site/delete", {"slug": "site-a"})[0] == 400, "already gone"
    assert call(s, "POST", "/site/delete", {"slug": "../sites"})[0] == 400


def test_a_site_being_built_cannot_be_deleted(serve, tmp_path, monkeypatch):
    root = tmp_path / "sites"
    d = _site(root, slug="site-a")
    jobs = FakeJobs()
    jobs.state = {"site": "site-a", "status": "running"}
    monkeypatch.setattr(jobs, "busy", lambda: True)
    s = serve(sites_root=str(root), jobs=jobs)
    code, err = call(s, "POST", "/site/delete", {"slug": "site-a"})
    assert code == 400 and "being built" in err["error"] and d.exists()


SKY_, TREE_, WALL_ = 2, 4, 1  # ADE20K: sky, tree, building


def test_the_terrain_biased_vote_keeps_ties_and_the_obstructions_class(tmp_path):
    import numpy as np

    from terminus.mosaic import combine_terrain_biased

    a = np.array([[SKY_, SKY_, TREE_, SKY_]])
    b = np.array([[SKY_, TREE_, TREE_, WALL_]])
    _label(tmp_path / "a.tif", a)
    _label(tmp_path / "b.tif", b)
    seen = []
    classes, votes, cover = combine_terrain_biased(
        [str(tmp_path / "a.tif"), str(tmp_path / "b.tif")], 4, 1,
        on_start=seen.append, on_vote=lambda v, c: seen.append(int(c.sum())),
        on_frame=lambda p, sky: seen.append(sky[0].tolist()),
    )  # fmt: skip
    assert classes[0].tolist() == [SKY_, TREE_, TREE_, WALL_], "a 1-1 tie is terrain, of its kind"
    assert votes[0].tolist() == [2, 1, 0, 1] and cover[0].tolist() == [2, 2, 2, 2]
    assert seen == [
        str(tmp_path / "a.tif"), [True, True, False, True], 4,
        str(tmp_path / "b.tif"), [True, False, False, False], 8,
    ], "each frame's own verdict, then the vote so far"  # fmt: skip


def test_a_segmented_reblend_votes_on_the_labels_and_says_so(tmp_path):
    import numpy as np

    sky_left = np.full((4, 8), TREE_)
    sky_left[:, :4] = SKY_
    one_more = sky_left.copy()
    one_more[:, 4] = SKY_  # this frame alone calls column 4 sky
    d = _site(tmp_path, panorama=False)
    _stitched(
        d, names=("a.jpg", "b.jpg", "c2.jpg"), dropped=(), labels=[sky_left, one_more, sky_left]
    )
    _reblend(d)
    classes = np.load(d / "equirect.terrain.classes.npy")
    strict = np.load(d / "equirect.strict.classes.npy")
    assert (classes[:, :4] == SKY_).all() and (classes[:, 4:] == TREE_).all(), "a tree stays a tree"
    assert (strict[:, :4] == SKY_).all(), "sky every frame agreed on"
    assert (
        json.loads((d / "equirect.vote.json").read_text())["backend"] == "segment (per-frame vote)"
    )


def test_a_disputed_sky_pixel_is_not_strict_sky(tmp_path):
    import numpy as np

    sky_left = np.full((4, 8), TREE_)
    sky_left[:, :4] = SKY_
    less = sky_left.copy()
    less[:, 3] = TREE_  # one of three frames calls column 3 terrain
    d = _site(tmp_path, panorama=False)
    _stitched(d, names=("a.jpg", "b.jpg", "c2.jpg"), dropped=(), labels=[sky_left, sky_left, less])
    _reblend(d)
    classes = np.load(d / "equirect.terrain.classes.npy")
    strict = np.load(d / "equirect.strict.classes.npy")
    assert (classes[:, 3] == SKY_).all(), "two of three is a majority"
    assert (strict[:, 3] != SKY_).all(), "but not unanimous: the planning line sees terrain there"


def _maps(terrain, strict=None, cover=2):
    import numpy as np

    strict = terrain if strict is None else strict
    return terrain, strict, np.full(terrain.shape, cover, np.int16), np.ones(terrain.shape)


def test_the_planning_line_buffers_a_tree_only_one_frame_saw():
    import numpy as np

    from terminus.export import TREE_BUFFER_DEG
    from terminus.reproject import true_horizon

    t = np.full((180, 360), TREE_)
    t[:80] = SKY_  # sky down to alt 10
    two = true_horizon(*_maps(t, cover=2))
    one = true_horizon(*_maps(t, cover=1))
    assert two[200]["planning"] == two[200]["alt"], "two frames saw the edge hold still"
    assert one[200]["planning"] == pytest.approx(one[200]["alt"] + TREE_BUFFER_DEG)
    assert one[200]["type"] == "tree"


def test_the_actual_line_dips_into_a_pocket_the_planning_line_does_not():
    import numpy as np

    from terminus.reproject import true_horizon

    t = np.full((180, 360), WALL_)
    t[:80] = SKY_  # sky down to alt 10 ...
    t[80:100, 100:112] = SKY_  # ... lower, to alt -10, at az 100-111 ...
    t[70:80, 100:110] = WALL_  # ... under an overhang from alt 10 to 20 over az 100-109
    cols = true_horizon(*_maps(t))
    assert cols[105]["alt"] == pytest.approx(-10.0, abs=1.01), "actual reaches round into it"
    assert cols[105]["planning"] == pytest.approx(20.0, abs=1.01), "planning: the overhang"
    assert cols[200]["alt"] == pytest.approx(10.0, abs=1.01)


def test_a_column_whose_obstruction_runs_off_the_photos_is_marked_clipped():
    import numpy as np

    from terminus.reproject import true_horizon

    t = np.full((180, 360), SKY_)
    t[100:] = WALL_
    t[:, 50:60] = WALL_  # a wall that fills its columns to the top
    cols = true_horizon(*_maps(t))
    assert cols[55]["clipped"] is True and cols[200]["clipped"] is False


def test_the_reprojection_applies_the_solution_the_way_orient_rotates():
    from terminus.orient import rotate
    from terminus.reproject import index_map

    H, W = 180, 360
    sol = {"yaw": 30.0, "pitch": 4.0, "tilt_mag": 6.0, "tilt_dir": 120.0}
    sy, sx = index_map(H, W, sol)
    for taz, talt in ((10.5, 20.5), (200.5, -5.5), (300.5, 45.5)):
        # The panorama pixel the photo shows at a true direction is where the
        # solution's forward rotation sends that panorama pixel.
        x, y = int(taz), int(90.0 - talt)
        paz, palt = (sx[y, x] + 0.5) / W * 360.0, 90.0 - (sy[y, x] + 0.5) / H * 180.0
        raz, ralt = rotate(
            (paz + sol["yaw"]) % 360.0, palt, sol["pitch"], sol["tilt_mag"], sol["tilt_dir"]
        )
        assert abs((raz - taz + 180) % 360 - 180) < 1.5 and abs(ralt - talt) < 1.5, (taz, talt)


def test_yaw_moves_the_horizon_to_its_true_azimuth():
    import numpy as np

    from terminus.reproject import true_horizon

    t = np.full((180, 360), WALL_)
    t[:80] = SKY_
    t[:, 0:10] = WALL_  # a tall notch at panorama azimuth 0-10
    t[:40, 0:10] = SKY_  # (sky above alt 50 there)
    cols = true_horizon(*_maps(t), {"yaw": 90.0, "pitch": 0.0, "tilt_mag": 0.0, "tilt_dir": 0.0})
    assert cols[95]["alt"] == pytest.approx(50.0, abs=1.01), "panorama az 5 is true az 95"
    assert cols[5]["alt"] == pytest.approx(10.0, abs=1.01)


def test_layer_and_verdict_images_serve_only_layer_names(serve, tmp_path):
    root, d = _curatable(tmp_path)
    s = serve(sites_root=str(root), jobs=FakeJobs())
    call(s, "POST", "/site/open", {"slug": d.name})
    code, webp = call(s, "GET", "/site/frame/layer.webp?layer=layer0000.tif")
    assert code == 200 and webp[8:12] == b"WEBP"
    code, png = call(s, "GET", "/site/frame/verdict.png?layer=layer0000.tif")
    assert code == 200 and png[:4] == b"\x89PNG"
    assert call(s, "GET", "/site/frame/layer.webp?layer=layer0009.tif")[0] == 204
    for bad in ("../site.json", "layer0000.png", "x/layer0000.tif", "layer00000.tif"):
        assert call(s, "GET", f"/site/frame/layer.webp?layer={bad}")[0] == 400, bad
        assert call(s, "GET", f"/site/frame/verdict.png?layer={bad}")[0] == 400, bad


def test_reblend_reuses_cached_gains_and_verdicts_until_a_layer_changes(tmp_path, monkeypatch):
    import os

    from terminus import mosaic, skymask

    d = _site(tmp_path, panorama=False)
    _stitched(d)
    solves, judged = [], []
    real_solve = mosaic.solve_gains
    monkeypatch.setattr(
        mosaic, "solve_gains", lambda *a, **k: solves.append(1) or real_solve(*a, **k)
    )

    def colour(rgb, valid, px_per_deg):
        judged.append(1)
        return (rgb[..., 2] > rgb[..., 0]) & valid

    monkeypatch.setattr(skymask, "heuristic_sky", colour)
    from terminus import cli

    def run(*off):
        args = ["reblend", str(d / "work"), "--out", str(d / "equirect")]
        cli.main(args + [a for n in off for a in ("--off", n)])

    import numpy as np

    run()
    first = (len(solves), len(judged))
    sky = np.load(d / "equirect.sky.npy")
    run()  # everything from cache: the same verdicts, not merely no new ones
    assert (np.load(d / "equirect.sky.npy") == sky).all()
    assert (len(solves), len(judged)) == first
    run("b.jpg")  # turning a frame off re-solves nothing and judges nothing again
    assert (len(solves), len(judged)) == first
    layer = d / "work" / "layer0000.tif"
    st = os.stat(layer)
    os.utime(layer, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))  # a re-stitch rewrote it
    run()
    assert len(solves) > first[0] and len(judged) == first[1] + 1, "only the changed frame again"


def test_vote_sky_crops_each_frame_to_its_own_columns(tmp_path):
    import numpy as np
    from PIL import Image

    from terminus.mosaic import vote_sky

    rgba = np.zeros((4, 6, 4), np.uint8)
    rgba[:2, :, :3], rgba[2:, :, :3], rgba[..., 3] = (80, 120, 220), (120, 80, 40), 255
    path = tmp_path / "l.tif"
    Image.fromarray(rgba, "RGBA").save(
        path, tiffinfo={286: ((10, 1),), 287: ((0, 1),), 282: ((1, 1),), 283: ((1, 1),)}
    )  # a frame at canvas columns 10-15
    seen = []

    def blue(rgb, valid):
        seen.append(rgb.shape)
        return (rgb[..., 2] > rgb[..., 0]) & valid

    sky, _ = vote_sky([str(path)], 30, 4, blue, margin=3)
    assert seen == [(4, 12, 3)], "its 6 columns and 3 either side, full height"
    assert (
        sky[:2, 10:16].all()
        and not sky[2:].any()
        and not sky[:, :10].any()
        and not sky[:, 16:].any()
    )


def test_a_frame_warped_off_the_canvas_is_left_out_not_fatal(tmp_path, capsys):
    d = _site(tmp_path, panorama=False)
    _stitched(d, names=("a.jpg", "b.jpg", "e.jpg"), dropped=(), off_canvas=("b.jpg",))
    _reblend(d)
    assert "b.jpg was warped off the canvas" in capsys.readouterr().out
    by = {f["name"]: f for f in json.loads((d / "equirect.frames.json").read_text())["frames"]}
    assert by["b.jpg"]["layer"] is None and by["b.jpg"]["dropped"] is True
    assert by["e.jpg"]["layer"] == "layer0002.tif", "layers are matched by index, not by count"


def test_an_unreadable_site_name_keeps_the_folder_name(serve, tmp_path):
    root = tmp_path / "sites"
    d = _site(root, slug="site-a")
    _site(root, slug="site-b")
    (d / "site.json").write_text("{not json")
    s = serve(sites_root=str(root), jobs=FakeJobs())
    code, listed = call(s, "GET", "/sites")
    assert code == 200 and [x["name"] for x in listed["sites"]] == ["site-a", "site-b"]


def test_a_hugin_tool_that_cannot_start_is_a_mosaic_error(tmp_path, monkeypatch):
    from terminus import mosaic

    tool = tmp_path / "pto_gen"
    tool.write_text("not a program")  # present but not executable
    monkeypatch.setattr(mosaic, "HUGIN_BIN", str(tmp_path))
    monkeypatch.setattr(mosaic, "_tool", lambda name: str(tool))
    for on_line in (None, print):
        with pytest.raises(mosaic.MosaicError, match="pto_gen could not run"):
            mosaic._run(["pto_gen"], on_line=on_line)


def test_the_stitch_takes_every_photo_type_and_refreshes_a_changed_photo(tmp_path, monkeypatch):
    import os

    from terminus import mosaic

    photos = tmp_path / "photos"
    photos.mkdir()
    for n in ("a.JPG", "b.png", "c.TIFF", "notes.txt"):
        (photos / n).write_bytes(b"one")
    calls = []
    monkeypatch.setattr(mosaic, "require_hugin", lambda: None)
    monkeypatch.setattr(mosaic, "_run", lambda cmd, **kw: calls.append(cmd) or "")
    monkeypatch.setattr(
        mosaic, "control_point_counts", lambda pto: {"a.JPG": 50, "b.png": 50, "c.TIFF": 50}
    )
    monkeypatch.setattr(
        mosaic, "control_point_links", lambda pto: {"a.JPG": [], "b.png": [], "c.TIFF": []}
    )
    work = tmp_path / "work"
    mosaic.solve(str(photos), str(work))
    staged = [
        os.path.basename(a) for a in calls[0] if a.endswith((".JPG", ".png", ".TIFF", ".txt"))
    ]
    assert staged == ["a.JPG", "b.png", "c.TIFF"]
    (work / "stage" / "a.key").write_text("keypoints of the first a.JPG")
    (photos / "a.JPG").write_bytes(b"a different photo, same name")
    mosaic.solve(str(photos), str(work))
    assert (work / "stage" / "a.JPG").read_bytes() == b"a different photo, same name"
    assert not (work / "stage" / "a.key").exists(), "its stale keypoints are dropped"


def test_a_canvas_event_must_be_two_integers(tmp_path):
    jobs = FakeJobs()
    jobs.state = {"canvas": None, "frames": [], "active": [], "pairs": [], "compared": 0}
    for bad in ('{"canvas": [1]}', '{"canvas": [1.5, 2]}', '{"canvas": ["a", "b"]}'):
        jobs._event(bad)
        assert jobs.state["canvas"] is None, bad
    jobs._event('{"canvas": [2880, 1440]}')
    assert jobs.state["canvas"] == [2880, 1440]


def test_the_build_child_says_why_its_stdin_watch_failed():
    script = (
        "import sys\n"
        "class Bad:\n"
        "    def read(self): raise OSError('stdin is broken')\n"
        "sys.stdin = Bad()\n"
        "from terminus.server import sites\n"
        "sites.exit_on_eof()\n"
    )
    p = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=30,
                       env={**os.environ, "PYTHONPATH": str(ROOT / "src")})  # fmt: skip
    assert p.returncode == 3 and "stdin is broken" in p.stderr


def test_an_unreadable_rotation_is_logged_not_silent(caplog):
    from terminus.server import views

    assert views.solution({"oriented": True, "yaw": "12.5deg"}) is None
    assert "rotation is unreadable" in caplog.text
    caplog.clear()
    assert views.solution({"oriented": True}) is None, "a scope-only sweep: nothing to say"
    assert "unreadable" not in caplog.text


def test_terrain_of_unknown_kind_claims_no_type():
    import numpy as np

    from terminus.reproject import true_horizon
    from terminus.skymask import TERRAIN_CLASS, type_name

    t = np.full((180, 360), TERRAIN_CLASS)
    t[:80] = SKY_
    assert {c["type"] for c in true_horizon(*_maps(t)).values()} == {""}
    assert type_name(TERRAIN_CLASS) == "" and type_name(TREE_) == "tree"


def test_terminus_horizon_records_the_vote_and_applies_the_solution(tmp_path):
    import numpy as np
    import yaml

    from terminus import cli
    from terminus.export import load_columns

    t = np.full((180, 360), WALL_)
    t[:80] = SKY_
    t[40:80, 0:10] = WALL_  # a taller wall at panorama azimuth 0-10
    base = tmp_path / "equirect"
    for key, arr in zip(("terrain.classes", "strict.classes", "cover", "coverage"), _maps(t),
                        strict=True):  # fmt: skip
        np.save(f"{base}.{key}.npy", arr)
    (tmp_path / "equirect.vote.json").write_text('{"backend": "segment (per-frame vote)"}')
    sol = tmp_path / "oriented.yaml"
    sol.write_text(yaml.safe_dump({"meta": {"oriented": True, "yaw": 90.0, "pitch": 0.0,
                                            "tilt_mag": 0.0, "tilt_dir": 0.0}}))  # fmt: skip
    out = str(tmp_path / "h.yaml")
    cli.main(["horizon", str(base), "--out", out, "--solution", str(sol)])
    meta, cols = load_columns(out)
    assert meta["backend"] == "segment (per-frame vote)" and meta["yaw"] == 90.0
    assert cols[95]["alt"] == pytest.approx(50.0, abs=1.01), "the solution's yaw is applied"


def test_a_layer_running_off_the_canvas_is_clipped():
    import numpy as np

    from terminus.mosaic import _placed

    m = np.ones((2, 4), bool)
    ys, xs, Y, X = _placed(m, -1, 0, 5, 1)  # starts left of the canvas; canvas is 1 row
    assert sorted(zip(Y.tolist(), X.tolist(), strict=True)) == [(0, 0), (0, 1), (0, 2)]
    assert sorted(zip(ys.tolist(), xs.tolist(), strict=True)) == [(0, 1), (0, 2), (0, 3)]
    _, _, Y, X = _placed(m, 3, 0, 5, 2)  # runs off the right
    assert X.max() == 4 and len(X) == 4


def test_reblend_with_no_layers_says_to_stitch(tmp_path, capsys):
    import os

    d = _site(tmp_path, panorama=False)
    _stitched(d)
    for f in (d / "work").glob("layer*.tif"):
        os.remove(f)
    with pytest.raises(SystemExit):
        _reblend(d)
    assert "holds no remapped layers; re-run mosaic" in capsys.readouterr().err


def test_labels_older_than_their_layers_are_not_used(tmp_path, capsys):
    import os

    import numpy as np

    sky_left = np.full((4, 8), TREE_)
    sky_left[:, :4] = SKY_
    d = _site(tmp_path, panorama=False)
    _stitched(d, dropped=(), labels=[sky_left, sky_left])
    for f in (d / "work").glob("label*.tif"):  # from an earlier, segmented stitch
        st = os.stat(f)
        os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns - 10**10))
    _reblend(d)
    assert "voting by colour instead" in capsys.readouterr().out
    assert json.loads((d / "equirect.vote.json").read_text())["backend"].startswith("heuristic")


def test_a_colour_vote_does_not_reuse_a_segmented_runs_verdicts(tmp_path, monkeypatch):
    import numpy as np
    from PIL import Image

    from terminus import cli, skymask

    d = _site(tmp_path, panorama=False)
    _stitched(d, dropped=())
    (d / "equirect.vote.json").write_text('{"backend": "segment (per-frame vote)"}')
    for i in range(2):  # verdicts a segmented vote left behind: all sky
        Image.fromarray(np.full((4, 8), 255, np.uint8)).save(d / "work" / f"sky_layer{i:04d}.png")
    judged = []

    def colour(rgb, valid, px_per_deg):
        judged.append(1)
        return (rgb[..., 2] > rgb[..., 0]) & valid

    monkeypatch.setattr(skymask, "heuristic_sky", colour)
    cli.main(["reblend", str(d / "work"), "--out", str(d / "equirect")])
    assert len(judged) == 2, "every frame judged afresh by colour"
    sky = np.load(d / "equirect.sky.npy")
    assert sky[:, :4].all() and not sky[:, 4:].any()


def test_a_corrupt_vote_record_says_so(tmp_path, capsys):
    from terminus import cli

    (tmp_path / "x.vote.json").write_text("{not json")
    assert cli._vote_backend(str(tmp_path / "x")) == "per-frame vote (unrecorded)"
    assert "x.vote.json is unreadable" in capsys.readouterr().out
    assert cli._vote_backend(str(tmp_path / "missing")) == "per-frame vote (unrecorded)"
    assert capsys.readouterr().out == "", "a missing record is not a warning"
