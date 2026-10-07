"""`terminus reblend`: blend a stitched panorama's frames again (some turned off)
and vote on the sky frame by frame, writing the maps the horizon is read from.

No Hugin: the frames are already remapped, one layer each, and dropping a frame
barely moves the others, so their layers are reused as they are. What a frame
contributes is cached until a re-stitch rewrites its layer: its sky verdict
beside the layer (work/sky_layerNNNN.png), the gains in work/cache.json.
"""

import json
import os

import numpy as np

from . import mosaic, skymask

SKY = skymask.SKY_CLASS_ADE20K
COLOUR_REACH_DEG = 15.0  # heuristic_sky's half_deg: how far a frame's classifier looks


def frame_files(work, names, prefix):
    """{photo name: its remapped file, or None}. nona numbers each file by the
    frame's index in the project and writes none for a frame that lands off the
    canvas, so the files are found by index, never by counting."""
    out = {}
    for i, name in enumerate(names):
        path = os.path.join(work, f"{prefix}{i:04d}.tif")
        out[os.path.basename(name)] = path if os.path.isfile(path) else None
    return out


def fresh_labels(work, names, layers, kept):
    """{kept layer: its label layer} when every kept frame has a label remapped
    with or after its photo (a stitch remaps labels right after the photos), or
    None: a label older than its layer is from an earlier stitch."""
    labels = frame_files(work, names, "label")
    out = {}
    for name, layer in layers.items():
        if layer not in kept:
            continue
        label = labels.get(name)
        if label is None or os.stat(label).st_mtime_ns < os.stat(layer).st_mtime_ns:
            if any(labels.values()):
                print("some frames have no current segmentation labels: voting by colour instead")
            return None
        out[layer] = label
    return out


def frame_gains(work, kept, names_of):
    """Per-frame gains (one, and one per colour channel) for the kept layers.

    Cached in work/cache.json by each layer's mtime, for the set last solved.
    Turning frames off reuses it; a set that includes a frame not in it
    re-solves the gains for all of them (they are relative to each other)."""
    path = os.path.join(work, "cache.json")
    cache = {}
    if os.path.isfile(path):
        with open(path) as fh:
            cache = json.load(fh)
    stamp = {os.path.basename(p): os.stat(p).st_mtime_ns for p in kept}
    if all(cache.get(k, {}).get("stamp") == s for k, s in stamp.items()):
        return [cache[k]["gain"] for k in stamp], [cache[k]["rgb"] for k in stamp]
    mosaic._emit(
        detail="evening out brightness across the photos", working=[names_of[p] for p in kept]
    )
    loaded = [mosaic._layer(p) for p in kept]
    gains, rgb = mosaic.solve_gains(loaded), mosaic.channel_gains(loaded).tolist()
    cache = {
        k: {"stamp": s, "gain": float(g), "rgb": rg}
        for (k, s), g, rg in zip(stamp.items(), gains, rgb, strict=True)
    }
    with open(path, "w") as fh:
        json.dump(cache, fh)
    return gains, rgb


class _Progress:
    """Each frame's verdict saved for the app (work/sky_layerNNNN.png) and the
    horizon so far redrawn after it (work/progress.png), with the events."""

    def __init__(self, work, width, height, names_of):
        self.work, self.size, self.names_of, self.drawn = work, (width, height), names_of, 0

    def verdict_png(self, path):
        stem = os.path.splitext(os.path.basename(path))[0]
        return os.path.join(self.work, "sky_" + stem + ".png")

    def start(self, path):
        mosaic._emit(working=[self.names_of[path]])

    def judged(self, path, sky):
        from PIL import Image

        Image.fromarray(np.where(sky, 255, 0).astype(np.uint8)).save(self.verdict_png(path))
        mosaic._emit(name=self.names_of[path], state="judged")

    def known(self, path):
        """A verdict cached from an earlier run, if the layer has not changed."""
        from PIL import Image

        verdict = self.verdict_png(path)
        if os.path.isfile(verdict) and os.stat(verdict).st_mtime_ns >= os.stat(path).st_mtime_ns:
            mosaic._emit(name=self.names_of[path], state="judged")
            return np.asarray(Image.open(verdict)) > 0
        return None

    def vote(self, votes, cover):
        from PIL import Image

        edge = skymask.sky_outline(votes, cover)
        rgba = np.zeros(edge.shape + (4,), np.uint8)
        rgba[edge] = (255, 214, 64, 255)
        w, h = self.size
        tmp = os.path.join(self.work, "progress.tmp.png")
        Image.fromarray(rgba, "RGBA").resize((w // 2, h // 2)).save(tmp)
        os.replace(tmp, os.path.join(self.work, "progress.png"))
        self.drawn += 1
        mosaic._emit(outline=self.drawn)


def vote_labels(labels, width, height, progress):
    """The vote over segmented frames: (classes, strict, votes, cover). Each obstruction
    keeps its own class, so a tree stays a tree (and earns the tree buffer)."""
    by_label = {lab: layer for layer, lab in labels.items()}
    classes, votes, cover = mosaic.combine_terrain_biased(
        list(labels.values()),
        width,
        height,
        on_start=lambda p: progress.start(by_label[p]),
        on_frame=lambda p, sky: progress.judged(by_label[p], sky),
        on_vote=progress.vote,
    )
    unanimous = (cover > 0) & (votes == cover)
    strict = np.where(classes == SKY, np.where(unanimous, SKY, 4), classes)  # disputed: tree
    return classes, strict, votes, cover


def vote_colours(kept, width, height, progress, reuse=True):
    """The vote by colour, where nothing was segmented: (classes, strict, votes, cover).
    It knows sky from not-sky but not what an obstruction is."""
    px_per_deg = width / 360.0
    sky, _disagree, votes, cover = mosaic.vote_sky(
        kept,
        width,
        height,
        lambda rgb, valid: skymask.heuristic_sky(rgb, valid=valid, px_per_deg=px_per_deg),
        on_frame=lambda p, sky, _origin: progress.judged(p, sky),
        margin=int(np.ceil(COLOUR_REACH_DEG * px_per_deg)),
        known=progress.known if reuse else None,
        on_start=progress.start,
        counts=True,
        on_vote=progress.vote,
    )
    terrain = np.where(cover > 0, skymask.TERRAIN_CLASS, -1)
    classes = np.where(sky, SKY, terrain)
    strict = np.where((cover > 0) & (votes == cover), SKY, terrain)
    return classes, strict, votes, cover


def frame_list(photos, layers, off, dropped):
    """The site's photos for the app: where each landed, which are off, which the
    stitch left out (too few control points, or warped off the canvas)."""
    frames = []
    for name in sorted(set(photos) | set(layers)):
        layer = layers.get(name)
        frames.append({
            "name": name,
            "layer": os.path.basename(layer) if layer else None,
            "box": list(mosaic.footprint(layer)[0]) if layer else None,
            "off": name in off,
            "dropped": name in dropped or (name in layers and layer is None),
        })  # fmt: skip
    return frames
