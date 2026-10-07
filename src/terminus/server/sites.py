"""Sites: the app's own project folders, one per observing position.

A site is a folder under the sites root that the app owns:

    <root>/<slug>/photos/            the user's photographs, copied in
    <root>/<slug>/site.json          the user's name for the site
    <root>/<slug>/curation.json      the photos the user turned off
    <root>/<slug>/work/              `terminus mosaic`: the stitch, one layer per photo
    <root>/<slug>/equirect.*         `terminus reblend`: the panoramas and class maps
    <root>/<slug>/photo_mask.yaml    `terminus skymask`: the native mask, for orienting
    <root>/<slug>/horizon.yaml       `terminus horizon`: the map (actual + planning)
    <root>/<slug>/oriented.yaml      `terminus orient` output, when there is one

The same files the CLI writes, so the CLI can still open a site. The pipeline
runs the engine's own code in a child process of this program (see `Jobs`).
The packaged app will ship its own frozen Python and Hugin (terminus-71.6), so
nothing here may depend on a `terminus` command or anything else on the machine.
"""

import datetime
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import threading

PHOTO_EXTS = (".jpg", ".jpeg", ".png", ".tif", ".tiff")
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
MAX_LOG = 40

log = logging.getLogger("terminus.server")


class SiteError(ValueError):
    """A request about sites that cannot be honoured; the message is for the user."""


def site_dir(root, slug):
    """The folder of one existing site. Refuses anything that is not a plain slug."""
    if not isinstance(slug, str) or not SLUG.fullmatch(slug):
        raise SiteError("not a site name")
    path = os.path.join(root, slug)
    if not os.path.isdir(path):
        raise SiteError("no such site")
    return path


def list_sites(root):
    if not os.path.isdir(root):
        return []
    return sorted(
        s for s in os.listdir(root) if SLUG.fullmatch(s) and os.path.isdir(os.path.join(root, s))
    )


def _claim(root, base):
    """Create and return a new, unused site folder under `root`. Atomic: two
    drops on the same day cannot get the same folder."""
    try:
        os.makedirs(root, exist_ok=True)
        for n in range(1, 1000):
            slug = base if n == 1 else f"{base}-{n}"
            try:
                os.mkdir(os.path.join(root, slug))
                return slug
            except FileExistsError:
                continue
    except OSError as e:
        raise SiteError(f"could not create a site folder: {e.strerror}") from e
    raise SiteError("too many sites for one day")


def create_site(root, photos, today=None):
    """Copy the photographs into a new site folder and return its slug.

    Every path must be an existing image file (absolute, not a link); nothing is
    left behind unless all of them copy, so a bad drop leaves no half-made site.
    """
    if not isinstance(photos, list) or not photos or len(photos) > 500:
        raise SiteError("expected a list of 1 to 500 photo paths")
    for p in photos:
        if not (
            isinstance(p, str)
            and os.path.isabs(p)
            and p.lower().endswith(PHOTO_EXTS)
            and os.path.isfile(p)
            and not os.path.islink(p)
        ):
            raise SiteError("every photo must be an existing .jpg, .png or .tif file")
    slug = _claim(root, f"site-{(today or datetime.date.today()).isoformat()}")
    dest = os.path.join(root, slug, "photos")
    what = "create the photos folder"
    try:
        os.mkdir(dest)
        for p in photos:
            name = os.path.basename(p)
            what = f"copy {name}"
            stem, ext = os.path.splitext(name)
            target, k = os.path.join(dest, name), 1
            while os.path.exists(target):  # two drops with the same file name
                k += 1
                target = os.path.join(dest, f"{stem}-{k}{ext}")
            shutil.copy2(p, target)
    except OSError as e:
        shutil.rmtree(os.path.join(root, slug), ignore_errors=True)
        raise SiteError(f"could not {what}: {e.strerror}") from e
    return slug


SITE_FILE = "site.json"  # the user's own name for the site: {"name": ...}
MAX_NAME = 80


def site_name(d):
    path = os.path.join(d, SITE_FILE)
    try:
        with open(path) as fh:
            name = json.load(fh).get("name")
    except FileNotFoundError:
        name = None
    except (OSError, ValueError, AttributeError):
        log.warning("%s is unreadable; the site keeps its folder name", path)
        name = None
    return name if isinstance(name, str) and name.strip() else os.path.basename(d)


def rename_site(root, slug, name):
    """Give a site a name of the user's choosing. The folder keeps its slug, so
    nothing that refers to the site (an open view, a running build) breaks."""
    d = site_dir(root, slug)
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > MAX_NAME:
        raise SiteError(f"a site name is 1 to {MAX_NAME} characters")
    tmp = os.path.join(d, SITE_FILE + ".tmp")
    with open(tmp, "w") as fh:
        json.dump({"name": name.strip()}, fh)
    os.replace(tmp, os.path.join(d, SITE_FILE))


def delete_site(root, slug):
    """Remove a site and everything in it (its copies of the photos included)."""
    shutil.rmtree(site_dir(root, slug))


def summary(root, slug):
    """What the site holds, for the UI to decide what it can show."""
    d = site_dir(root, slug)
    photos = os.path.join(d, "photos")
    changed = os.stat(d).st_mtime
    for f in os.listdir(d):
        try:
            changed = max(changed, os.stat(os.path.join(d, f)).st_mtime)
        except FileNotFoundError:  # removed while we looked (a build tidying up)
            pass
    return {
        "slug": slug,
        "name": site_name(d),
        "updated": datetime.datetime.fromtimestamp(changed).isoformat(timespec="minutes"),
        "photos": len(os.listdir(photos)) if os.path.isdir(photos) else 0,
        "panorama": os.path.isfile(os.path.join(d, "equirect.png")),
        "mask": mask_path(d) is not None,
    }


HORIZON = "horizon.yaml"  # actual + planning, from one reprojection


def mask_path(d):
    """The mask to show: the oriented one if `orient` has run, else the horizon
    map, else (a site built before the map existed) the native photo mask."""
    for name in ("oriented.yaml", HORIZON, "photo_mask.yaml"):
        p = os.path.join(d, name)
        if os.path.isfile(p):
            return p
    return None


CURATION = "curation.json"  # the frames the user turned off: {"off": [name, ...]}
KINDS = ("build", "reblend")  # stitch from the photos, or re-blend the stitched frames


def frames_off(d):
    """The photos the user has turned off in site `d`."""
    path = os.path.join(d, CURATION)
    if not os.path.isfile(path):
        return []
    with open(path) as fh:
        off = json.load(fh).get("off", [])
    if not isinstance(off, list) or not all(isinstance(n, str) for n in off):
        raise ValueError(f"{CURATION} is malformed")
    return off


def set_frames_off(d, off):
    tmp = os.path.join(d, CURATION + ".tmp")
    with open(tmp, "w") as fh:
        json.dump({"off": sorted(off)}, fh)
    os.replace(tmp, os.path.join(d, CURATION))


def segmentation_installed():
    """Can the build child segment photos (the `segment` extra)? Checked without
    importing torch, which takes seconds."""
    import importlib.util

    return all(importlib.util.find_spec(m) for m in ("torch", "transformers"))


def pipeline(d, kind="build"):
    """The CLI invocations that turn a site's photos into a panorama and a mask.

    A build stitches from the photos, leaving out the ones turned off; a reblend
    reuses the stitched frames. Both then vote on the sky frame by frame.
    """
    off = frames_off(d)
    eq, work = os.path.join(d, "equirect"), os.path.join(d, "work")
    steps = []
    if kind == "build":
        exclude = [a for n in off for a in ("--exclude", n)]
        if segmentation_installed():
            exclude.append("--segment")  # label each photo; the vote reads the labels
        steps.append(("mosaic", ["mosaic", os.path.join(d, "photos"), "--work", work,
                                 "--out", eq, "--events", "--layers-only", *exclude]))  # fmt: skip
    steps.append(
        (
            "reblend",
            ["reblend", work, "--out", eq, "--events", *(a for n in off for a in ("--off", n))],
        )
    )
    steps.append(("skymask", ["skymask", eq + ".png", "--coverage", eq + ".coverage.npy",
                              "--sky", eq + ".sky.npy", "--out",
                              os.path.join(d, "photo_mask.yaml")]))  # fmt: skip
    # The map the app shows and exports; photo_mask.yaml is the native mask the
    # orientation fit reads.
    steps.append(("horizon", ["horizon", eq, "--out", os.path.join(d, HORIZON)]))
    return steps


STEP = "@step "  # how the pipeline child announces each step on stdout
EVENT = "@event "  # and each photo's progress (mosaic's EVENTS, as JSON)
FRAME_FIELDS = ("state", "points", "links", "reason", "layer", "box")
JOB_TIMEOUT_S = 2 * 60 * 60  # a stitch of hundreds of photos is slow, but not this slow


def run_pipeline(d, kind="build", run=None):
    """The child process's whole job: run each step in its main thread.

    Raises SystemExit on the first step that fails, after cli.main has printed
    its "error: ..." line.
    """
    from .. import cli

    run = run or cli.main
    for step, argv in pipeline(d, kind):
        print(STEP + step, flush=True)
        run(argv)


def _poll_until_closed(every_s=1.0):
    """Return once the writer of our stdin pipe has gone, without ever blocking
    in a read. On Windows a thread parked in ReadFile on stdin hung the main
    thread's import of scipy's BLAS (py-spy, 2026-10-06), so this thread only
    peeks at the pipe."""
    import ctypes
    import msvcrt
    import time

    handle = msvcrt.get_osfhandle(sys.stdin.fileno())
    peek = ctypes.WinDLL("kernel32", use_last_error=True).PeekNamedPipe
    avail = ctypes.c_ulong()
    while peek(ctypes.c_void_p(handle), None, 0, None, ctypes.byref(avail), None):
        time.sleep(every_s)  # the pipe is still open (and nothing is ever written)
    error = ctypes.get_last_error()
    if error != 109:  # ERROR_BROKEN_PIPE: the writer has gone, which is the point
        log.warning("stdin is not a pipe we can watch (Windows error %d): stopping", error)


def exit_on_eof():
    """Exit the build child at once when the sidecar goes away (its end of our
    stdin closes), so a build never outlives the app."""
    try:
        if os.name == "nt":
            _poll_until_closed()
        else:
            sys.stdin.read()
    except BaseException:
        import traceback

        traceback.print_exc()  # into the build log: why the build is stopping
        raise
    finally:
        # The sidecar may have died without stopping us (killed, crashed), so
        # take the Hugin tool we are running down with us: on POSIX we lead our
        # own process group. On Windows the tool is left to finish.
        if os.name != "nt" and os.getpgrp() == os.getpid():
            os.killpg(0, signal.SIGKILL)
        os._exit(3)


def own_command():
    """How this program starts itself: the frozen sidecar, or `python -m` in dev."""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, "-m", "terminus.server"]


def _reap(proc):
    """Wait for the child, then kill anything it left in its group (a Hugin tool
    can outlive it). The group is signalled while the child is still an unreaped
    zombie, so its pid cannot have been reused by an unrelated process."""
    if hasattr(os, "waitid"):  # not on Windows, nor macOS before Python 3.13
        try:
            os.waitid(os.P_PID, proc.pid, os.WEXITED | os.WNOWAIT)
            os.killpg(proc.pid, signal.SIGKILL)
        except (ChildProcessError, ProcessLookupError):
            pass  # kill_tree (the timeout, or stop) already took the group, or it is gone
    return proc.wait()


def kill_tree(proc):
    """Kill a running child and everything it started (the Hugin tool it is
    running). A group that has already gone counts as done."""
    if os.name == "nt":
        if proc.poll() is None:
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, timeout=30
            )
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)  # the child leads its own process group
        except ProcessLookupError:
            pass  # the whole group has already exited
    proc.wait(timeout=30)


class Jobs:
    """One pipeline at a time, each in a child process the job thread watches.

    The child, not this process, loads the native code (Hugin, scipy, OpenBLAS):
    the sidecar will hold the telescope, and a hang or crash in stitching must
    not take down the process that parks it. It also sidesteps a Windows
    loader-lock deadlock seen when a worker thread first loaded scipy's BLAS
    while the HTTP server was starting threads.
    """

    def __init__(self, command=None, hugin=None, timeout=JOB_TIMEOUT_S):
        self._command = command or own_command
        self._hugin = hugin
        self._root = None  # the running site's parent folder, scrubbed from what the page sees
        self._timeout = timeout
        self._lock = threading.Lock()
        self._proc = None
        self._closed = False
        self.state = None

    def busy(self):
        return bool(self.state and self.state["status"] == "running")

    def _scrub(self, text):
        """The sites folder is nobody's business in what the page sees."""
        return text.replace(self._root, "<sites>") if self._root else text

    def _log(self, line):
        log.info("build: %s", line)  # whole, in sidecar.log
        lines = self.state["log"]
        lines.append(self._scrub(line))
        del lines[:-MAX_LOG]

    def _event(self, text):
        """Fold one progress event into the job: a phase, the canvas size, or a
        photo's latest state (fields merge, so `placed` keeps `matched`'s points)."""
        try:
            event = json.loads(text)
        except ValueError:
            log.warning("build: unreadable event %r", text[:200])
            return
        if not isinstance(event, dict):
            return
        if isinstance(event.get("phase"), str):
            self.state["phase"] = event["phase"]
            self.state["active"] = []  # a new phase starts on nothing in particular
            self.state["detail"] = None
        if isinstance(event.get("detail"), str):
            self.state["detail"] = event["detail"]
        if isinstance(event.get("working"), list):
            self.state["active"] = [n for n in event["working"] if isinstance(n, str)]
        pair = event.get("pair")
        if isinstance(pair, list) and len(pair) == 2 and isinstance(event.get("matches"), int):
            self.state["compared"] += 1
            if event["matches"] > 0:  # most pairs share nothing; keep the ones that do
                self.state["pairs"].append(
                    {"a": pair[0], "b": pair[1], "matches": event["matches"]}
                )
        canvas = event.get("canvas")
        if isinstance(canvas, list) and len(canvas) == 2 and all(type(v) is int for v in canvas):
            self.state["canvas"] = canvas
        if isinstance(event.get("outline"), int):
            self.state["outline"] = event["outline"]
        name = event.get("name")
        if isinstance(name, str):
            frames = self.state["frames"]
            frame = next((f for f in frames if f["name"] == name), None)
            if frame is None:
                frame = {"name": name}
                frames.append(frame)
            frame.update({k: event[k] for k in FRAME_FIELDS if k in event})

    def _fail(self, error):
        self.state["error"] = self._scrub(error)
        self.state["status"] = "failed"

    def start(self, slug, d, kind="build"):
        """Start building site `d` (see `pipeline` for the kinds). Returns the watching thread, or None if the
        child could not even start (the job is then already marked failed)."""
        with self._lock:
            if self._closed:
                raise SiteError("the engine is shutting down")
            self._root = os.path.dirname(os.path.abspath(d))
            if self.busy():
                raise SiteError("a site is already being built")
            self.state = {
                "site": slug,
                "step": None,
                "status": "running",
                "log": [],
                "error": None,
                "phase": None,
                "detail": None,  # what the phase is doing, in words, when it says
                "canvas": None,
                "frames": [],
                "active": [],  # the photos being worked on right now
                "pairs": [],  # photo pairs cpfind found matches between
                "compared": 0,  # pairs cpfind has compared so far
                "outline": 0,  # times the horizon so far has been redrawn
            }
            cmd = [*self._command(), "--pipeline", d, "--kind", kind]
            if self._hugin:
                cmd += ["--hugin", self._hugin]
            try:
                self._proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,  # held open; the child exits when it closes
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    errors="replace",
                    start_new_session=os.name != "nt",
                )
            except OSError as e:
                log.exception("build of %s could not start", slug)
                self._fail(f"the build could not start: {e.strerror or e}")
                return None
        t = threading.Thread(target=self._watch, args=(self._proc, d), daemon=True)
        t.start()
        return t

    def _watch(self, proc, d):
        expired = threading.Event()

        def expire():
            expired.set()
            try:
                kill_tree(proc)
            except Exception:  # the timeout is still reported once the build ends
                log.exception("could not kill the overrunning build")

        timer = threading.Timer(self._timeout, expire)
        timer.start()
        try:
            for line in proc.stdout:
                line = line.rstrip()
                if line.startswith(STEP):
                    self.state["step"] = line[len(STEP) :]
                    self.state["active"], self.state["detail"] = [], None
                elif line.startswith(EVENT):
                    self._event(line[len(EVENT) :])
                elif line.strip():
                    self._log(line)
            code = _reap(proc)
        except Exception as e:  # a bug in watching must not leave the job "running"
            log.exception("watching the build failed")
            self._fail(f"lost track of the build: {type(e).__name__}: {e}")
            try:
                kill_tree(proc)
            except Exception:
                log.exception("could not kill the build")
            return
        finally:
            timer.cancel()
            proc.stdout.close()
            proc.stdin.close()
        self._finish(code, expired.is_set(), d)

    def _finish(self, code, expired, d):
        step = self.state["step"] or "the build"
        if expired:
            return self._fail(f"{step} took longer than {self._timeout // 60} minutes")
        if code == 0:
            missing = [
                f
                for f in ("equirect.png", "photo_mask.yaml", HORIZON)
                if not os.path.isfile(os.path.join(d, f))
            ]
            if missing:
                return self._fail(f"the build finished but wrote no {', '.join(missing)}")
            self.state["status"] = "done"
            return
        # cli.main prints "error: ..." and exits 1; a crash ends with its exception.
        errors = [x for x in self.state["log"] if x.startswith("error:")]
        if errors:
            self._fail(errors[-1])
        elif self.state["log"]:
            self._fail(f"{step} stopped (exit {code}): {self.state['log'][-1]}")
        else:
            self._fail(f"{step} stopped (exit {code})")

    def stop(self):
        """Refuse new builds and kill a running one (the sidecar is exiting)."""
        with self._lock:
            self._closed = True
            proc = self._proc
        if proc is not None and proc.returncode is None:  # a reaped pid may be reused
            kill_tree(proc)
