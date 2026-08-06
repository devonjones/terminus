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


def hugin_available():
    return all(shutil.which(t) for t in TOOLS)


def require_hugin():
    missing = [t for t in TOOLS if not shutil.which(t)]
    if missing:
        raise MosaicError(
            "hugin command line tools not found: "
            + ", ".join(missing)
            + ". Install hugin-tools (Debian/Ubuntu 24.04+, Raspberry Pi OS). "
            "On Ubuntu 22.04 hugin was dropped from the archive; use a PPA or AppImage."
        )


def _run(cmd, cwd=None):
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if p.returncode != 0:
        raise MosaicError(f"{cmd[0]} failed: {p.stderr.strip()[:400]}")
    return p.stdout


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


def control_point_counts(pto):
    counts = {}
    names = []
    for line in open(pto):
        if line.startswith("i "):
            names.append(os.path.basename(re.search(r'n"([^"]+)"', line).group(1)))
        elif line.startswith("c "):
            m = re.match(r"c n(\d+) N(\d+)", line)
            for i in (int(m.group(1)), int(m.group(2))):
                counts[i] = counts.get(i, 0) + 1
    return {names[i]: counts.get(i, 0) for i in range(len(names))}


def solve(
    image_dir,
    work_dir,
    lens=None,
    min_points=MIN_CONTROL_POINTS,
    celeste=True,
    log=print,
):
    """Register every image in image_dir. Returns (pto_path, dropped_names).

    Iterates: find control points, clean, drop under-constrained frames, repeat.
    Dropping is always logged — a silently discarded frame is silently missing sky.
    """
    require_hugin()
    os.makedirs(work_dir, exist_ok=True)
    images = sorted(glob.glob(os.path.join(image_dir, "*.jpg")))
    if len(images) < 2:
        raise MosaicError(f"need at least 2 images, found {len(images)}")
    dropped = []
    keep = [os.path.basename(p) for p in images]
    for _attempt in range(6):
        stage = os.path.join(work_dir, "stage")
        shutil.rmtree(stage, ignore_errors=True)
        os.makedirs(stage)
        for n in keep:
            shutil.copy2(os.path.join(image_dir, n), os.path.join(stage, n))
        pto = os.path.join(stage, "project.pto")
        _run(["pto_gen", "-o", pto] + [os.path.join(stage, n) for n in keep])
        if lens:
            _set_lens(pto, lens)
        cp = os.path.join(stage, "cp.pto")
        cmd = ["cpfind", "--multirow"]
        if celeste:
            cmd.append("--celeste")  # masks cloud before feature detection
        _run(cmd + ["-o", cp, pto])
        clean = os.path.join(stage, "clean.pto")
        _run(["cpclean", "-o", clean, cp])
        counts = control_point_counts(clean)
        weak = [n for n, c in counts.items() if c < min_points]
        if not weak:
            log(f"all {len(keep)} frames constrained (min {min(counts.values())} points)")
            break
        for n in weak:
            log(f"dropping {n}: {counts[n]} control points (< {min_points})")
        dropped += weak
        keep = [n for n in keep if n not in weak]
        if len(keep) < 3:
            raise MosaicError("too few constrained frames remain")
    else:
        raise MosaicError("frame rejection did not converge")
    solved = os.path.join(work_dir, "solved.pto")
    _run(["autooptimiser", "-a", "-l", "-s", "-o", solved, clean])
    return solved, dropped


def render(pto, work_dir, width=2880, height=1440, prefix="layer"):
    """Remap to equirectangular layers. Returns the list of TIFF paths."""
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
    out = os.path.join(work_dir, prefix)
    for old in glob.glob(out + "*.tif"):
        os.remove(old)
    _run(["nona", "-m", "TIFF_m", "-o", out, final])
    return sorted(glob.glob(out + "*.tif")), final


# Panotools interpolator 6 is nearest neighbour. It is not a quality setting
# here, it is a correctness one: a class index is a LABEL, not a magnitude, and
# poly3 (the default, `m i0`) will happily produce 8.4 halfway between tree (4)
# and building (1) — a class that does not exist. Everything downstream then
# reads it as whatever ADE20K happens to number 8.
NEAREST = 6


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

    `label_for` maps a source filename (as the project spells it) to a label
    image on disk. Returns the remapped layer paths.
    """
    require_hugin()
    out_pto = os.path.join(work_dir, prefix + ".pto")
    with open(pto) as fh, open(out_pto, "w") as out:
        for line in fh:
            if line.startswith("m "):
                # Interpolation is set on the m line and defaults to poly3.
                line = re.sub(r"\bi\d+", f"i{NEAREST}", line.rstrip("\n")) + "\n"
                if f"i{NEAREST}" not in line:
                    line = line.rstrip("\n") + f" i{NEAREST}\n"
            elif line.startswith("i "):
                m = re.search(r'n"([^"]*)"', line)
                if m and m.group(1) in label_for:
                    line = line.replace(m.group(0), f'n"{label_for[m.group(1)]}"')
            out.write(line)
    stem = os.path.join(work_dir, prefix)
    for old in glob.glob(stem + "*.tif"):
        os.remove(old)
    _run(["nona", "-m", "TIFF_m", "-o", stem, out_pto])
    return sorted(glob.glob(stem + "*.tif"))


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
    for _ in range(iterations):
        acc = np.zeros((h, w, 3))
        cnt = np.zeros((h, w))
        for g, (rgb, m, ox, oy) in zip(gains, layers, strict=False):
            ys, xs = np.mgrid[0 : m.shape[0], 0 : m.shape[1]]
            Y, X = ys[m] + oy, xs[m] + ox
            ok = (Y >= 0) & (Y < h) & (X >= 0) & (X < w)
            acc[Y[ok], X[ok]] += rgb[m][ok] * g
            cnt[Y[ok], X[ok]] += 1
        ref = np.where(cnt[..., None] > 0, acc / np.maximum(cnt, 1)[..., None], 0)
        new = []
        for g, (rgb, m, ox, oy) in zip(gains, layers, strict=False):
            ys, xs = np.mgrid[0 : m.shape[0], 0 : m.shape[1]]
            Y, X = ys[m] + oy, xs[m] + ox
            ok = (Y >= 0) & (Y < h) & (X >= 0) & (X < w)
            mine = rgb[m][ok].mean()
            theirs = ref[Y[ok], X[ok]].mean()
            new.append(g * (theirs / max(mine * g, 1e-6)) ** 0.5)
        new = np.array(new)
        new /= np.exp(np.log(new).mean())
        if np.allclose(new, gains, atol=1e-4):
            return new
        gains = new
    return gains


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
