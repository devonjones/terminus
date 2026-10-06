"""Sites: the app's own project folders, one per observing position.

A site is a folder under the sites root that the app owns:

    <root>/<slug>/photos/            the user's photographs, copied in
    <root>/<slug>/equirect.png       `terminus mosaic` output (+ .coverage.npy, ...)
    <root>/<slug>/photo_mask.yaml    `terminus skymask` output
    <root>/<slug>/oriented.yaml      `terminus orient` output, when there is one

The same files the CLI writes, so the CLI can still open a site. The pipeline
runs the engine's own code in a child process of this program (see `Jobs`).
The packaged app will ship its own frozen Python and Hugin (terminus-71.6), so
nothing here may depend on a `terminus` command or anything else on the machine.
"""

import datetime
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


def summary(root, slug):
    """What the site holds, for the UI to decide what it can show."""
    d = site_dir(root, slug)
    photos = os.path.join(d, "photos")
    return {
        "slug": slug,
        "photos": len(os.listdir(photos)) if os.path.isdir(photos) else 0,
        "panorama": os.path.isfile(os.path.join(d, "equirect.png")),
        "mask": mask_path(d) is not None,
    }


def mask_path(d):
    """The mask to show: the oriented one if `orient` has run, else the photo mask."""
    for name in ("oriented.yaml", "photo_mask.yaml"):
        p = os.path.join(d, name)
        if os.path.isfile(p):
            return p
    return None


def pipeline(d):
    """The CLI invocations that turn a site's photos into a panorama and a mask."""
    return [
        ("mosaic", ["mosaic", os.path.join(d, "photos"), "--work", os.path.join(d, "work"),
                    "--out", os.path.join(d, "equirect")]),  # fmt: skip
        ("skymask", ["skymask", os.path.join(d, "equirect.png"), "--coverage",
                     os.path.join(d, "equirect.coverage.npy"), "--out",
                     os.path.join(d, "photo_mask.yaml")]),  # fmt: skip
    ]


STEP = "@step "  # how the pipeline child announces each step on stdout
JOB_TIMEOUT_S = 2 * 60 * 60  # a stitch of hundreds of photos is slow, but not this slow


def run_pipeline(d, run=None):
    """The child process's whole job: run each step in its main thread.

    Raises SystemExit on the first step that fails, after cli.main has printed
    its "error: ..." line.
    """
    from .. import cli

    run = run or cli.main
    for step, argv in pipeline(d):
        print(STEP + step, flush=True)
        run(argv)


def exit_on_eof():
    """Exit the build child at once when the sidecar goes away (its end of our
    stdin closes), so a build never outlives the app."""
    try:
        sys.stdin.read()
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
        self._timeout = timeout
        self._lock = threading.Lock()
        self._proc = None
        self._closed = False
        self.state = None

    def busy(self):
        return bool(self.state and self.state["status"] == "running")

    def _log(self, line):
        log.info("build: %s", line)
        lines = self.state["log"]
        lines.append(line)
        del lines[:-MAX_LOG]

    def _fail(self, error):
        self.state["error"] = error
        self.state["status"] = "failed"

    def start(self, slug, d):
        """Start building site `d`. Returns the watching thread, or None if the
        child could not even start (the job is then already marked failed)."""
        with self._lock:
            if self._closed:
                raise SiteError("the engine is shutting down")
            if self.busy():
                raise SiteError("a site is already being built")
            self.state = {"site": slug, "step": None, "status": "running", "log": [], "error": None}
            cmd = [*self._command(), "--pipeline", d]
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
                for f in ("equirect.png", "photo_mask.yaml")
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
