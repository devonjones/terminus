"""Sites: the app's own project folders, one per observing position.

A site is a folder under the sites root that the app owns:

    <root>/<slug>/photos/            the user's photographs, copied in
    <root>/<slug>/equirect.png       `terminus mosaic` output (+ .coverage.npy, ...)
    <root>/<slug>/photo_mask.yaml    `terminus skymask` output
    <root>/<slug>/oriented.yaml      `terminus orient` output, when there is one

The same files the CLI writes, so the CLI can still open a site. The pipeline
runs the engine in-process: the app ships its own frozen Python and Hugin and
must not depend on a `terminus` command or anything else on the machine.
"""

import contextlib
import datetime
import io
import os
import re
import shutil
import threading

PHOTO_EXTS = (".jpg", ".jpeg", ".png", ".tif", ".tiff")
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
MAX_LOG = 40


class SiteError(ValueError):
    """A request about sites that cannot be honoured; the message is for the user."""


def site_dir(root, slug):
    """The folder of one existing site. Refuses anything that is not a plain slug."""
    if not isinstance(slug, str) or not SLUG.match(slug):
        raise SiteError("not a site name")
    path = os.path.join(root, slug)
    if not os.path.isdir(path):
        raise SiteError("no such site")
    return path


def list_sites(root):
    if not os.path.isdir(root):
        return []
    return sorted(
        s for s in os.listdir(root) if SLUG.match(s) and os.path.isdir(os.path.join(root, s))
    )


def create_site(root, photos, today=None):
    """Copy the photographs into a new site folder and return its slug.

    Every path must be an existing image file; nothing is copied unless all of
    them are, so a bad drop leaves no half-made site behind.
    """
    if not isinstance(photos, list) or not photos or len(photos) > 500:
        raise SiteError("expected a list of 1 to 500 photo paths")
    for p in photos:
        if not isinstance(p, str) or not p.lower().endswith(PHOTO_EXTS) or not os.path.isfile(p):
            raise SiteError("every photo must be an existing .jpg, .png or .tif file")
    base = f"site-{(today or datetime.date.today()).isoformat()}"
    slug, n = base, 1
    while os.path.exists(os.path.join(root, slug)):
        n += 1
        slug = f"{base}-{n}"
    dest = os.path.join(root, slug, "photos")
    os.makedirs(dest)
    for p in photos:
        name = os.path.basename(p)
        stem, ext = os.path.splitext(name)
        target, k = os.path.join(dest, name), 1
        while os.path.exists(target):  # two drops with the same file name
            k += 1
            target = os.path.join(dest, f"{stem}-{k}{ext}")
        shutil.copy2(p, target)
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


class _Lines(io.TextIOBase):
    """A write target that keeps the last lines, for showing progress."""

    def __init__(self, sink):
        self.sink, self.buf = sink, ""

    def write(self, s):
        self.buf += s
        *done, self.buf = self.buf.split("\n")
        for line in done:
            if line.strip():
                self.sink(line)
        return len(s)


class Jobs:
    """One pipeline at a time, run on a worker thread; its state is read by /state."""

    def __init__(self, run=None):
        from .. import cli

        self._run = run or cli.main
        self._lock = threading.Lock()
        self.state = None

    def _log(self, line):
        log = self.state["log"]
        log.append(line)
        del log[:-MAX_LOG]

    def start(self, slug, d):
        with self._lock:
            if self.state and self.state["status"] == "running":
                raise SiteError("a site is already being built")
            self.state = {"site": slug, "step": None, "status": "running", "log": [], "error": None}
        t = threading.Thread(target=self._work, args=(d,), daemon=True)
        t.start()
        return t

    def _work(self, d):
        out = _Lines(self._log)
        try:
            for step, argv in pipeline(d):
                self.state["step"] = step
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                    self._run(argv)
        except SystemExit as e:
            # cli.main reports its errors as "error: ..." on stderr and exits 1;
            # that line is already in the log, so name it as the failure.
            errors = [x for x in self.state["log"] if x.startswith("error:")]
            self.state["error"] = errors[-1] if errors else f"{self.state['step']} exited {e.code}"
            self.state["status"] = "failed"
            return
        except Exception as e:  # anything else is a bug; keep the sidecar up and say so
            self.state["error"] = f"{self.state['step']} crashed: {type(e).__name__}: {e}"
            self.state["status"] = "failed"
            return
        self.state["status"] = "done"
