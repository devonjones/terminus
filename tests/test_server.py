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
    [("GET", "/health"), ("GET", "/state"), ("GET", "/dev/state"), ("POST", "/state/tab")],
)
def test_refuses_without_token_host_or_with_origin(serve, method, path, headers, token, code):
    s = serve()
    status, _ = call(s, method, path, {"tab": "fit"}, headers=headers, token=token)
    assert status == code
    assert s.state["tab"] == "connect"


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
        "scope": {"link": "none"},
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


# ---- sites, the pipeline job, the views, and the schema contract (stage 1) ----

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
                {"az": 300.0, "alt": None, "used": False, "reason": "no edge in the search window"},
            ],
        }  # fmt: skip
        write_mask(d / "oriented.yaml", cols, [], meta)
    return d


class FakeJobs(server_module.sites.Jobs):
    """The real Jobs, with the engine replaced by a recorder (no Hugin in tests)."""

    def __init__(self, fail=None):
        self.calls = []

        def run(argv):
            self.calls.append(argv)
            if fail == argv[0]:
                print(f"error: {argv[0]} could not register the photos", file=sys.stderr)
                raise SystemExit(1)

        super().__init__(run=run)

    def wait(self):
        for _ in range(200):
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


def test_the_pipeline_runs_mosaic_then_skymask_on_the_site(tmp_path):
    jobs = FakeJobs()
    d = _site(tmp_path, panorama=False)
    jobs.start("site-2026-10-05", str(d)).join(5)
    assert jobs.state["status"] == "done" and jobs.state["error"] is None
    assert [argv[0] for argv in jobs.calls] == ["mosaic", "skymask"]
    mosaic, skymask = jobs.calls
    assert mosaic[1] == str(d / "photos") and mosaic[-1] == str(d / "equirect")
    assert skymask[1] == str(d / "equirect.png") and skymask[-1] == str(d / "photo_mask.yaml")
    conforms("Job", jobs.state)


def test_a_failed_step_is_reported_with_the_engines_own_message(tmp_path):
    jobs = FakeJobs(fail="mosaic")
    jobs.start("s", str(_site(tmp_path))).join(5)
    assert jobs.state["status"] == "failed"
    assert jobs.state["error"] == "error: mosaic could not register the photos"
    assert [argv[0] for argv in jobs.calls] == ["mosaic"], "skymask must not run on no panorama"


def test_a_crashing_step_fails_the_job_not_the_sidecar(tmp_path):
    from terminus.server import sites

    def boom(argv):
        raise RuntimeError("bug")

    jobs = sites.Jobs(run=boom)
    jobs.start("s", str(_site(tmp_path))).join(5)
    assert jobs.state["status"] == "failed"
    assert jobs.state["error"] == "mosaic crashed: RuntimeError: bug"


def test_only_one_site_builds_at_a_time(tmp_path):
    from terminus.server import sites

    gate = threading.Event()
    jobs = sites.Jobs(run=lambda argv: gate.wait(5))
    t = jobs.start("a", str(_site(tmp_path)))
    with pytest.raises(sites.SiteError, match="already"):
        jobs.start("b", str(tmp_path))
    gate.set()
    t.join(5)


def test_progress_lines_reach_the_job_log(tmp_path):
    from terminus.server import sites

    jobs = sites.Jobs(run=lambda argv: print(f"working on {argv[0]}"))
    jobs.start("s", str(_site(tmp_path))).join(5)
    assert jobs.state["log"] == ["working on mosaic", "working on skymask"]


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
    assert st["site"] == {"slug": "site-2026-10-05", "photos": 1, "panorama": True, "mask": True}
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
    assert [f["az"] for f in h["fit"]] == [90.0, 200.0, 300.0]
    assert h["fit"][2] == {"az": 300.0, "used": False, "reason": "no edge in the search window"}


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

    assert disc["actual"] == [xy(0, 20.0), xy(90, 5.0), xy(180, 12.5), xy(270, 30.0)]
    assert disc["planning"] == [xy(0, 22.0), xy(180, 13.0)]
    assert disc["pockets"] == [[xy(0, 15.0), xy(0, 10.0)]]
    # Only the columns the fit used, with the ceiling-bound one marked.
    assert [(f["az"], f["bound"]) for f in disc["fiducials"]] == [(90.0, False), (200.0, True)]
    north, east = disc["cardinals"][0], disc["cardinals"][1]
    assert north["label"] == "N" and north["xy"][1] < disc["centre"], "north is up"
    assert east["label"] == "E" and east["xy"][0] > disc["centre"], "east is right"


def test_site_images_are_jpegs_and_absent_ones_are_404(serve, tmp_path):
    _site(tmp_path, slug="site-a")
    _site(tmp_path, slug="site-b", panorama=False)
    s = serve(sites_root=str(tmp_path))
    call(s, "POST", "/site/open", {"slug": "site-a"})
    for path in ("/site/panorama.jpg", "/site/disc.jpg"):
        code, data = call(s, "GET", path)
        assert code == 200 and data[:3] == b"\xff\xd8\xff", path
    call(s, "POST", "/site/open", {"slug": "site-b"})
    assert call(s, "GET", "/site/panorama.jpg")[0] == 404
    assert call(s, "GET", "/site/disc.jpg")[0] == 404


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
