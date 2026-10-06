"""Register a set of photographs into one equirectangular sky map, via Hugin.

Hugin does the photogrammetry; terminus does the astronomy. `autooptimiser`
solves each frame's yaw, pitch and roll — which are azimuth, altitude and camera
tilt — and `nona` remaps into equirectangular, where x is azimuth and y is
altitude. That is exactly the frame a horizon mask wants, produced natively.

Requires the hugin-tools command line programs (pto_gen, cpfind, cpclean,
autooptimiser, nona, pano_modify). They are an optional system dependency: this
is an offline desktop step, not something a Raspberry Pi does mid-sweep, so a
missing Hugin degrades to a clear error rather than blocking a sweep.

Hard-won details encoded here:

* `pto_gen` guesses a rectilinear 50 degree lens for everything. Photographs need
  their real field of view from EXIF; an already-stitched panorama needs
  projection f4 (equirectangular) and its angular span. Getting this wrong is
  silent — the solve simply converges somewhere wrong.
* **A frame that cannot be constrained must be dropped, never placed.**
  `autooptimiser` will happily leave an image with zero control points at its
  default orientation, dropping a photograph of the ground into the middle of the
  sky. Removing frames changes connectivity, so the check has to iterate.
* Exposure varies between frames — unavoidable when shooting toward and away
  from the Sun — so per-frame gains are solved before compositing. Without it,
  overlaps darken wherever a dim frame contributes.
* Seams are not blended away. A visible seam is how a person checks the fit.
"""

import glob
import json
import os
import re
import shutil
import subprocess

import numpy as np

TOOLS = ("pto_gen", "cpfind", "cpclean", "autooptimiser", "nona", "pano_modify")
MIN_CONTROL_POINTS = 12  # below this a frame's orientation is not determined


class MosaicError(RuntimeError):
    pass


# Where the Hugin tools live. None means PATH, which suits the CLI on a machine
# where someone installed Hugin. The desktop app will ship its own Hugin and set
# this, so it never depends on what the user's machine has.
HUGIN_BIN = None


def _tool(name):
    """The path of one Hugin tool, or None if it is not there."""
    if HUGIN_BIN is None:
        return shutil.which(name)
    path = os.path.join(HUGIN_BIN, name + (".exe" if os.name == "nt" else ""))
    return path if os.path.isfile(path) else None


def hugin_available():
    return all(_tool(t) for t in TOOLS)


def require_hugin():
    missing = [t for t in TOOLS if not _tool(t)]
    if missing and HUGIN_BIN is not None:
        raise MosaicError(f"hugin tools missing from {HUGIN_BIN}: " + ", ".join(missing))
    if missing:
        raise MosaicError(
            "hugin command line tools not found: "
            + ", ".join(missing)
            + ". Install hugin-tools (Debian/Ubuntu 24.04+, Raspberry Pi OS). "
            "On Ubuntu 22.04 hugin was dropped from the archive; use a PPA or AppImage."
        )


def _run(cmd, cwd=None, on_line=None):
    """Run a Hugin tool. `on_line`, if given, sees each line of its output as it
    is printed (stderr folded in), so a watcher can follow a long cpfind."""
    tool = _tool(cmd[0])
    if tool is None and HUGIN_BIN is not None:
        # Never fall back to PATH: the app must run the Hugin it ships.
        raise MosaicError(f"{cmd[0]} is missing from {HUGIN_BIN}")
    cmd = [tool or cmd[0], *cmd[1:]]
    if on_line is None:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
        if p.returncode != 0:
            raise MosaicError(f"{cmd[0]} failed: {p.stderr.strip()[:400]}")
        return p.stdout
    lines = []
    with subprocess.Popen(
        cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace"
    ) as p:
        for line in p.stdout:
            lines.append(line)
            on_line(line.rstrip())
    if p.returncode != 0:
        raise MosaicError(f"{cmd[0]} failed: {''.join(lines[-10:]).strip()[:400]}")
    return "".join(lines)


ANALYZING = re.compile(r"^i(\d+) : Analyzing image")
CACHED = re.compile(r"^i(\d+) : Caching keypoints")
PAIR = re.compile(r"^i(\d+) <> i(\d+) : Found (\d+) matches")
# cpfind's own stages, as it announces them ("--- Analyze Images ---"), in words.
CPFIND_STAGES = {
    "Analyze Images": "finding features in each photo",
    "Cache keyfiles to disc": "saving each photo's features",
    "Find matches": "comparing neighbouring photos",
    "Find matches in images groups": "comparing photos across rows",
    "Find pair-wise matches": "comparing the remaining pairs",
    "Write Project output": "writing the matches",
}
STAGE = re.compile(r"^--- (.+?) ---$")


def _follow_cpfind(names):
    """cpfind's progress as events: which photos it is analysing, then each pair
    it compares and how many matches it found (only reported once compared)."""
    busy = set()

    def on_line(line):
        if (m := STAGE.match(line)) and m.group(1) in CPFIND_STAGES:
            _emit(detail=CPFIND_STAGES[m.group(1)])
        elif m := ANALYZING.match(line):
            busy.add(names[int(m.group(1))])
            _emit(working=sorted(busy))
        elif m := CACHED.match(line):
            busy.discard(names[int(m.group(1))])
            _emit(working=sorted(busy))
        elif m := PAIR.match(line):
            a, b = names[int(m.group(1))], names[int(m.group(2))]
            _emit(pair=[a, b], matches=int(m.group(3)), working=[a, b])

    return on_line


def _set_lens(pto, entries):
    """Rewrite each i-line's projection and field of view.

    entries: {basename: (projection_code, hfov_degrees)}
    """
    out = []
    for line in open(pto):
        if line.startswith("i "):
            name = os.path.basename(re.search(r'n"([^"]+)"', line).group(1))
            if name in entries:
                f, v = entries[name]
                line = re.sub(r"\bf\d+\b", f"f{f}", line, count=1)
                line = re.sub(r"\bv[0-9.]+\b", f"v{v:.4f}", line, count=1)
        out.append(line)
    with open(pto, "w") as fh:
        fh.writelines(out)


# Progress for a watcher (the app): called with one dict per event when set.
EVENTS = None


def _emit(**event):
    if EVENTS:
        EVENTS(event)


def _control_points(pto):
    names, pairs = [], []
    for line in open(pto):
        if line.startswith("i "):
            names.append(os.path.basename(re.search(r'n"([^"]+)"', line).group(1)))
        elif line.startswith("c "):
            m = re.match(r"c n(\d+) N(\d+)", line)
            pairs.append((int(m.group(1)), int(m.group(2))))
    return names, pairs


def control_point_counts(pto):
    names, pairs = _control_points(pto)
    counts = {}
    for pair in pairs:
        for i in pair:
            counts[i] = counts.get(i, 0) + 1
    return {names[i]: counts.get(i, 0) for i in range(len(names))}


def control_point_links(pto):
    """Each image's neighbours: the images it shares control points with."""
    names, pairs = _control_points(pto)
    links = {n: set() for n in names}
    for a, b in pairs:
        if a != b:
            links[names[a]].add(names[b])
            links[names[b]].add(names[a])
    return {n: sorted(v) for n, v in links.items()}


def solve(
    image_dir,
    work_dir,
    lens=None,
    min_points=MIN_CONTROL_POINTS,
    celeste=True,
    log=print,
    exclude=(),
):
    """Register every image in image_dir. Returns (pto_path, dropped_names).

    `exclude` names images the user turned off: they are left out of the solve
    entirely, so they cannot pull the others' registration.

    Iterates: find control points, clean, drop under-constrained frames, repeat.
    Dropping is always logged — a silently discarded frame is silently missing sky.
    """
    require_hugin()
    os.makedirs(work_dir, exist_ok=True)
    images = sorted(
        p for p in glob.glob(os.path.join(image_dir, "*.jpg")) if os.path.basename(p) not in exclude
    )
    if len(images) < 2:
        raise MosaicError(f"need at least 2 images, found {len(images)}")
    dropped = []
    keep = [os.path.basename(p) for p in images]
    for n in keep:
        _emit(name=n, state="listed")
    _emit(phase="matching")
    for _attempt in range(6):
        # Kept between runs: cpfind caches each photo's keypoints beside it
        # (`--cache`), so a re-stitch does not find them all over again.
        stage = os.path.join(work_dir, "stage")
        os.makedirs(stage, exist_ok=True)
        for n in keep:
            if not os.path.isfile(os.path.join(stage, n)):
                shutil.copy2(os.path.join(image_dir, n), os.path.join(stage, n))
        pto = os.path.join(stage, "project.pto")
        _run(["pto_gen", "-o", pto] + [os.path.join(stage, n) for n in keep])
        if lens:
            _set_lens(pto, lens)
        cp = os.path.join(stage, "cp.pto")
        cmd = ["cpfind", "--multirow", "--cache"]
        if celeste:
            cmd.append("--celeste")  # masks cloud before feature detection
        _run(cmd + ["-o", cp, pto], on_line=_follow_cpfind(keep))
        clean = os.path.join(stage, "clean.pto")
        _emit(detail="throwing out matches that disagree", working=[])
        _run(["cpclean", "-o", clean, cp])
        counts = control_point_counts(clean)
        links = control_point_links(clean)
        weak = [n for n, c in counts.items() if c < min_points]
        for n, c in counts.items():
            if n in weak:
                _emit(name=n, state="dropped", points=c, links=links[n],
                      reason=f"{c} control points (needs {min_points})")  # fmt: skip
            else:
                _emit(name=n, state="matched", points=c, links=links[n])
        if not weak:
            log(f"all {len(keep)} frames constrained (min {min(counts.values())} points)")
            break
        for n in weak:
            log(f"dropping {n}: {counts[n]} control points (< {min_points})")
        dropped += weak
        keep = [n for n in keep if n not in weak]
        _emit(detail=f"matching again without the {len(weak)} that would not match")
        if len(keep) < 3:
            raise MosaicError("too few constrained frames remain")
    else:
        raise MosaicError("frame rejection did not converge")
    solved = os.path.join(work_dir, "solved.pto")
    _emit(phase="solving")
    _run(["autooptimiser", "-a", "-l", "-s", "-o", solved, clean])
    return solved, dropped


def _nona_render(pto, work_dir, prefix, one_by_one=False):
    # Clear the prefix's stale layers, remap, return the fresh set - the shared
    # tail of both renders. The stale-file sweep matters: a frame count that
    # shrank between runs would otherwise leave orphan layers that composite
    # happily averages in.
    #
    # `one_by_one` remaps one image per nona run, so a watcher sees each frame
    # land as it is placed.
    out = os.path.join(work_dir, prefix)
    for old in glob.glob(out + "*.tif"):
        os.remove(old)
    if not one_by_one:
        _run(["nona", "-m", "TIFF_m", "-o", out, pto])
        return sorted(glob.glob(out + "*.tif"))
    _emit(phase="placing", canvas=list(canvas_size(pto)))
    for i, name in enumerate(source_images(pto)):
        _emit(working=[os.path.basename(name)])
        _run(["nona", "-m", "TIFF_m", "-o", out, "-i", str(i), pto])
        path = f"{out}{i:04d}.tif"
        if os.path.isfile(path):  # nona writes nothing for a frame off the canvas
            box, _mask = footprint(path)
            _emit(name=os.path.basename(name), state="placed",
                  layer=os.path.basename(path), box=list(box))  # fmt: skip
    return sorted(glob.glob(out + "*.tif"))


def render(pto, work_dir, width=2880, height=1440, prefix="layer"):
    """Remap to equirectangular layers. Returns (tiff_paths, final_pto).

    THE LAYERS ARE RAW. Per-frame exposure is corrected by `composite()`, which
    solves and APPLIES a scalar gain per layer before averaging — a caller that
    averages these TIFFs itself reintroduces the very exposure steps the gains
    exist to remove, and the manifest's `gains` field is an OUTPUT of that
    composite, not an input to reapply (terminus-52). If a uniform-looking
    image is wanted for a figure, that is `render_photometric`, not a naive
    mean of these layers.
    """
    require_hugin()
    final = os.path.join(work_dir, "final.pto")
    _run(
        [
            "pano_modify",
            "--projection=2",  # equirectangular: x is azimuth, y is altitude
            "--fov=360x180",
            f"--canvas={width}x{height}",
            "-o",
            final,
            pto,
        ]
    )
    return _nona_render(final, work_dir, prefix, one_by_one=True), final


def render_photometric(final_pto, work_dir, prefix="figure"):
    """Figure-quality layers: exposure, vignetting and response solved by Hugin.

    `autooptimiser -m` fits a photometric model — per-frame exposure, the
    lens's vignetting falloff, and the camera response curve — which `nona`
    then applies while remapping. A scalar gain per frame (what `composite`
    solves) cannot remove a gradient WITHIN a frame; this can, which is why it
    is the escalation for a uniform-looking mosaic (terminus-52).

    Two boundaries, both deliberate:

      * OFF the numbers path. It changes pixel values, and every published
        residual and figure so far was produced without it — so it hangs off
        the SAME geometric solve (the final.pto the diagnostic render used)
        and writes to its own prefix, and nothing downstream of measurement
        reads it. Geometry identical, photometry solved: two renders, one
        solve.
      * NOT seam feathering. D-08 stands: seams stay hard, because a visible
        seam is how a person checks the registration. Only the exposure
        surface is solved, so a step that survives at a boundary means
        misregistration, not metering.
    """
    require_hugin()
    photo = os.path.join(work_dir, "photometric.pto")
    _run(["autooptimiser", "-m", "-o", photo, final_pto])
    return _nona_render(photo, work_dir, prefix), photo


def source_images(pto):
    """The image filenames a project references, in image-line order."""
    names = []
    with open(pto) as fh:
        for line in fh:
            if line.startswith("i "):
                m = re.search(r'n"([^"]*)"', line)
                if m:
                    names.append(m.group(1))
    return names


def remap_labels(pto, work_dir, label_for, prefix="label"):
    """Warp per-frame LABEL images through the same solve as the photographs.

    This is the point of the whole arrangement, and doing it the other way round
    is a mistake worth naming. Segmenting the finished panorama asks the model to
    read an equirectangular projection: downsampled from eighteen 12-megapixel
    frames to one 2880x1440 canvas, seamed where frames blend, and stretched
    without limit toward the poles. SegFormer was trained on photographs. A
    photograph is what each frame still is.

    So segment the frames — full resolution, native projection, exactly the
    input the model expects — and then push the LABELS through the identical
    warp, so they land wherever their pixels landed.

    NONA NEVER TOUCHES A CLASS ID. It is asked for COORDINATES (`-c`, which
    writes `_x` and `_y` images naming the source pixel behind every output
    pixel) and the ids are then looked up in the full-resolution label frame.
    Anything else corrupts them, and it took three attempts to accept that:

      * poly3 resampling averages two ids into a third, so class 2 beside class
        4 becomes class 3. Setting the project's interpolator to nearest does
        NOT prevent this — nona ignores the `m` line's `i` value entirely, and
        `i0`, `i5` and `i6` produce byte-identical output. The guard that was
        supposed to stop this had never once worked.
      * the frames are 12 megapixels and the canvas is 2880 wide, so nearly
        every output pixel straddles a class boundary. On the 2026-08-03 set
        that was 75% of them, not some thin edge case.
      * photometric correction applies exposure, white balance and a response
        curve to whatever the file holds. Neutralising every coefficient is not
        enough while the correction MODE is set, and even then the sRGB round
        trip perturbs small integers.

    A resampling kernel and an exposure curve are both arithmetic, and
    arithmetic on an identifier is meaningless. Looking the id up by coordinate
    is not a better approximation, it is an exact answer.

    `label_for` maps a source filename (as the project spells it) to a label
    image on disk. Returns the remapped layer paths.
    """
    import numpy as np
    from PIL import Image

    require_hugin()
    out_pto = os.path.join(work_dir, prefix + ".pto")
    names = source_images(pto)
    with open(pto) as fh, open(out_pto, "w") as out:
        for line in fh:
            if line.startswith("i "):
                m = re.search(r'n"([^"]*)"', line)
                if m and m.group(1) in label_for:
                    line = line.replace(m.group(0), f'n"{label_for[m.group(1)]}"')
            out.write(line)
    # EVERY LABEL MUST HAVE ACTUALLY REPLACED A PHOTOGRAPH. The substitution
    # matches on the project's own spelling of the filename, so a caller whose
    # keys differ by a directory prefix — "frame1.jpg" against
    # "stage/frame1.jpg" — silently rewrites nothing. nona then warps the
    # PHOTOGRAPHS, exits 0, and `combine_labels` reads RGB brightness as class
    # ids: a wrong-but-successful run, indistinguishable from a correct one, and
    # the same silent corruption this function was rewritten to eliminate
    # reached through a different door. A reviewer hit exactly this while
    # building a harness for it.
    missing = set(label_for) - set(names)
    if missing:
        raise MosaicError(
            "these label images name frames the project does not contain: "
            + ", ".join(sorted(missing))
            + f". The project spells its images {sorted(names)[:3]}... — the keys of "
            "`label_for` must match that spelling exactly, or the photographs get "
            "warped instead of the labels and nothing says so."
        )
    stem = os.path.join(work_dir, prefix)
    for old in glob.glob(stem + "*.tif"):
        os.remove(old)
    _run(["nona", "-c", "-m", "TIFF_m", "-o", stem, out_pto])

    written = []
    for index, name in enumerate(names):
        layer = f"{stem}{index:04d}.tif"
        xpath, ypath = f"{stem}{index:04d}_x.tif", f"{stem}{index:04d}_y.tif"
        if not (os.path.exists(layer) and os.path.exists(xpath)):
            continue  # nona placed no pixels for this frame
        label_path = os.path.join(os.path.dirname(pto), label_for[name])
        source = np.asarray(Image.open(label_path).convert("RGB"))[..., 0]
        height, width = source.shape
        xs = np.asarray(Image.open(xpath)).astype(np.int64)
        ys = np.asarray(Image.open(ypath)).astype(np.int64)
        with Image.open(layer) as im:
            tags = im.tag_v2
            alpha = np.asarray(im.convert("RGBA"))[..., 3] > 0
            # Uncovered pixels carry a sentinel rather than a coordinate, so the
            # bounds test is what separates "no source pixel" from "pixel 0,0".
            ok = alpha & (xs >= 0) & (xs < width) & (ys >= 0) & (ys < height)
            ids = np.zeros(xs.shape, np.uint8)
            ids[ok] = source[ys[ok], xs[ok]]
            rgba = np.dstack([ids, ids, ids, np.where(ok, 255, 0).astype(np.uint8)])
            keep = {t: tags[t] for t in (282, 283, 286, 287) if t in tags}
        Image.fromarray(rgba, "RGBA").save(layer, tiffinfo=keep)
        os.remove(xpath)
        os.remove(ypath)
        written.append(layer)
    return sorted(written)


def combine_labels(layer_paths, width, height):
    """Majority vote per pixel across the remapped label layers.

    Majority, not last-wins, for the same reason `segment_classes` votes across
    tiles: where two frames overlap they may disagree, and the answer that more
    of the evidence supports is better than the answer that happened to be
    stitched second.

    Returns an int array of ADE20K classes, -1 where no frame covered.
    """
    votes = {}
    covered = np.zeros((height, width), bool)
    for path in layer_paths:
        rgb, mask, ox, oy = _layer(path)
        lab = rgb[..., 0].astype(int)  # class id stored in every channel
        h, w = lab.shape
        ys, xs = np.mgrid[0:h, 0:w]
        Y, X = ys[mask] + oy, xs[mask] + ox
        ok = (Y >= 0) & (Y < height) & (X >= 0) & (X < width)
        Y, X, L = Y[ok], X[ok], lab[mask][ok]
        covered[Y, X] = True
        for cls in np.unique(L):
            box = votes.setdefault(int(cls), np.zeros((height, width), np.int32))
            sel = L == cls
            np.add.at(box, (Y[sel], X[sel]), 1)
    if not votes:
        return np.full((height, width), -1, dtype=int)
    labels = sorted(votes)
    stack = np.stack([votes[c] for c in labels], axis=0)
    out = np.asarray(labels, dtype=int)[np.argmax(stack, axis=0)]
    return np.where(covered, out, -1)


def combine_terrain_biased(label_paths, width, height):
    """Devon's rule over warped per-frame LABEL layers: a pixel is sky only if a
    strict majority of the frames covering it say sky; otherwise it is terrain,
    labelled with its most-voted non-sky class (so a tree stays a tree). Ported
    exactly from the 2026-10-05 session's combine_terrain_biased.py.

    `combine_labels` takes a plain argmax whose ties go to the lowest class id,
    and sky (2) is lower than most terrain classes, so a two-frame disagreement
    resolves to SKY: the unsafe direction. Returns (classes, sky_votes, cover).
    """
    from .skymask import SKY_CLASS_ADE20K as SKY

    cover = np.zeros((height, width), np.int32)
    skyv = np.zeros((height, width), np.int32)
    votes = {}  # non-sky class -> counts
    for path in label_paths:
        rgb, mask, ox, oy = _layer(path)
        lab = rgb[..., 0].astype(int)
        ys, xs = np.nonzero(mask)
        Y, X = ys + oy, xs + ox
        ok = (Y >= 0) & (Y < height) & (X >= 0) & (X < width)
        Y, X, L = Y[ok], X[ok], lab[mask][ok]
        np.add.at(cover, (Y, X), 1)
        s = L == SKY
        np.add.at(skyv, (Y[s], X[s]), 1)
        for cls in np.unique(L[~s]):
            sel = L == cls
            box = votes.setdefault(int(cls), np.zeros((height, width), np.int32))
            np.add.at(box, (Y[sel], X[sel]), 1)
    out = np.full((height, width), -1, int)
    if votes:
        labels = sorted(votes)
        best = np.asarray(labels)[np.argmax(np.stack([votes[c] for c in labels]), axis=0)]
        out = np.where(cover > 0, best, -1)
    out[(cover > 0) & (2 * skyv > cover)] = SKY  # strict majority of covering frames
    return out, skyv, cover


def _layer(path):
    from PIL import Image

    im = Image.open(path)

    def num(tag, default):
        v = im.tag_v2.get(tag, default)
        return float(v[0] if isinstance(v, (tuple, list)) else v)

    ox = int(round(num(286, 0) * num(282, 1)))
    oy = int(round(num(287, 0) * num(283, 1)))
    a = np.asarray(im.convert("RGBA")).astype(np.float32)
    return a[..., :3], a[..., 3] > 0, ox, oy


def canvas_size(pto):
    """The (width, height) a rendered project's layers are placed on."""
    with open(pto) as fh:
        for line in fh:
            if line.startswith("p "):
                w, h = re.search(r"\bw(\d+)", line), re.search(r"\bh(\d+)", line)
                if w and h:
                    return int(w.group(1)), int(h.group(1))
    raise MosaicError(f"{pto} has no canvas size")


def footprint(path):
    """A layer's (x, y, width, height) box on the canvas, and its coverage mask."""
    _rgb, mask, ox, oy = _layer(path)
    return (ox, oy, mask.shape[1], mask.shape[0]), mask


def vote_sky(
    layer_paths,
    width,
    height,
    classify,
    on_frame=None,
    margin=0,
    known=None,
    on_start=None,
    counts=False,
    on_vote=None,
):
    """Sky by majority of the frames, each classified on its own.

    A pixel is sky only if a STRICT majority of the frames covering it say so:
    a tie is terrain, because a horizon placed too high costs a planner a
    target, and one placed too low sends the scope into a roof. Each frame is
    judged whole, in its own exposure, rather than through a blend that mixes a
    bright frame's sky into a dim frame's tree.

    `classify(rgb, valid)` takes one frame placed on the full canvas and returns
    its sky mask. Returns (sky, disagree): `disagree` is the fraction of the
    covering frames outvoted at each pixel, 0 where they agree or none covers.
    `on_frame(path, sky, box)`, if given, sees each frame's own verdict, cropped
    to its box, as soon as it is made. `known(path)` may return a verdict made
    before (cropped the same way) to skip classifying that frame again.

    Each frame is classified on its own columns plus `margin` either side, at
    full height: the columns beyond carry none of its pixels, so cropping them
    away changes nothing as long as `margin` covers the classifier's reach.
    """
    votes = np.zeros((height, width), np.int32)
    cover = np.zeros((height, width), np.int32)
    for path in layer_paths:
        rgb, m, ox, oy = _layer(path)
        ys, xs = np.nonzero(m)
        Y, X = ys + oy, xs + ox
        ok = (Y >= 0) & (Y < height) & (X >= 0) & (X < width)
        valid = np.zeros((height, width), bool)
        valid[Y[ok], X[ok]] = True
        y0, y1 = max(0, oy), min(height, oy + m.shape[0])
        x0, x1 = max(0, ox), min(width, ox + m.shape[1])
        mine = np.zeros((height, width), bool)
        box = known(path) if known else None
        if box is None:
            if on_start:
                on_start(path)
            c0, c1 = max(0, ox - margin), min(width, ox + m.shape[1] + margin)
            canvas = np.zeros((height, c1 - c0, 3), np.float32)
            canvas[Y[ok], X[ok] - c0] = rgb[m][ok]
            mine[:, c0:c1] = classify(canvas, valid[:, c0:c1])
            mine &= valid
            box = mine[y0:y1, x0:x1]
            if on_frame:
                on_frame(path, box, (x0, y0))
        else:
            mine[y0:y1, x0:x1] = box & valid[y0:y1, x0:x1]
        votes += mine
        cover += valid
        if on_vote:
            on_vote(votes, cover)  # the vote so far, after this frame
    sky = votes * 2 > cover
    outvoted = np.where(sky, cover - votes, votes)
    disagree = np.where(cover > 0, outvoted / np.maximum(cover, 1), 0.0)
    # `counts`: also the sky votes and the covering-frame count per pixel, which
    # the planning horizon reads (sky every frame agreed on is votes == cover).
    return (sky, disagree, votes, cover) if counts else (sky, disagree)


def solve_gains(layers, iterations=12):
    """Per-layer brightness gains that make overlaps agree.

    Photographing a full circle under sun guarantees different exposures; the
    camera meters each frame separately. Averaging raw values darkens every
    overlap that includes a dim frame, which the sky classifier then reads as
    terrain.
    """
    gains = np.ones(len(layers))
    h = w = None
    for _rgb, m, ox, oy in layers:
        h = max(h or 0, oy + m.shape[0])
        w = max(w or 0, ox + m.shape[1])
    # Each layer's covered pixels as flat canvas indices, found once.
    prep = []
    cnt = np.zeros(h * w)
    for rgb, m, ox, oy in layers:
        ys, xs = np.nonzero(m)
        Y, X = ys + oy, xs + ox
        ok = (Y >= 0) & (Y < h) & (X >= 0) & (X < w)
        idx = Y[ok] * w + X[ok]
        prep.append((idx, rgb[m][ok]))
        cnt[idx] += 1
    for _ in range(iterations):
        acc = np.zeros((h * w, layers[0][0].shape[-1]))
        for g, (idx, vals) in zip(gains, prep, strict=True):
            acc[idx] += vals * g  # a layer covers each pixel once: no repeats in idx
        ref = acc / np.maximum(cnt, 1)[:, None]
        new = []
        for g, (idx, vals) in zip(gains, prep, strict=True):
            new.append(g * (ref[idx].mean() / max(vals.mean() * g, 1e-6)) ** 0.5)
        new = np.array(new)
        new /= np.exp(np.log(new).mean())
        if np.allclose(new, gains, atol=1e-4):
            return new
        gains = new
    return gains


def channel_gains(layers):
    """`solve_gains` for each colour channel: shape (n, 3)."""
    return np.stack(
        [
            solve_gains([(rgb[..., c : c + 1], m, ox, oy) for rgb, m, ox, oy in layers])
            for c in range(3)
        ],
        axis=1,
    )


def blend(tiffs, width, height, feather=48, gains=None, on_frame=None):
    """A panorama for the eye: each frame colour-matched to its neighbours channel
    by channel, and faded out over `feather` pixels toward its edges so seams do
    not show. Never measure from it: the sky vote reads the raw layers, and
    `composite` is the measurement blend (terminus-52)."""
    from scipy.ndimage import distance_transform_edt

    layers = [_layer(t) for t in tiffs]
    gains = channel_gains(layers) if gains is None else np.asarray(gains)
    acc = np.zeros((height, width, 3))
    wsum = np.zeros((height, width))
    for path, g, (rgb, m, ox, oy) in zip(tiffs, gains, layers, strict=True):
        if on_frame:
            on_frame(path)
        weight = np.minimum(distance_transform_edt(m), feather) / feather
        ys, xs = np.mgrid[0 : m.shape[0], 0 : m.shape[1]]
        Y, X = ys[m] + oy, xs[m] + ox
        ok = (Y >= 0) & (Y < height) & (X >= 0) & (X < width)
        w = weight[m][ok]
        acc[Y[ok], X[ok]] += np.clip(rgb[m][ok] * g, 0, 255) * w[:, None]
        wsum[Y[ok], X[ok]] += w
    img = np.where(wsum[..., None] > 0, acc / np.maximum(wsum, 1e-9)[..., None], 0)
    return img.clip(0, 255).astype(np.uint8)


def composite(tiffs, width, height, gains=None):
    """Average layers into one canvas. Returns (rgb, coverage_count)."""
    layers = [_layer(t) for t in tiffs]
    if gains is None:
        gains = solve_gains(layers)
    acc = np.zeros((height, width, 3))
    cnt = np.zeros((height, width))
    for g, (rgb, m, ox, oy) in zip(gains, layers, strict=False):
        ys, xs = np.mgrid[0 : m.shape[0], 0 : m.shape[1]]
        Y, X = ys[m] + oy, xs[m] + ox
        ok = (Y >= 0) & (Y < height) & (X >= 0) & (X < width)
        acc[Y[ok], X[ok]] += np.clip(rgb[m][ok] * g, 0, 255)
        cnt[Y[ok], X[ok]] += 1
    img = np.where(cnt[..., None] > 0, acc / np.maximum(cnt, 1)[..., None], 0)
    return img.clip(0, 255).astype(np.uint8), cnt, gains


def write_manifest(path, **fields):
    """Record everything needed to reprocess this run later.

    Frames on disk are only re-analysable if the conditions that produced them
    are recorded too. Keeping this next to the data, not in a log file, is the
    difference between a capture that can be re-judged by a future classifier and
    one that cannot.
    """
    with open(path, "w") as fh:
        json.dump(fields, fh, indent=1, sort_keys=True, default=str)
    return path
