"""Decide which pixels of a photograph are open sky.

Two backends, same interface:

  * `segment` — semantic segmentation (SegFormer / ADE20K). Labels each pixel by
    what it *is*, so cloud is sky because it is sky and a pale wall is building
    because it is a building. Needs torch; optional.
  * `heuristic` — colour and brightness only. No dependencies beyond numpy, which
    matters on a Raspberry Pi.

Why the heuristic is what it is, and why it is not the default:

Brightness alone fails. A shadowed cloud base is genuinely dark, so any
brightness threshold either rejects it as terrain or is loose enough to accept
roofs. Requiring *not warm and not green* rescues cloud (cloud is neutral or
blue; fences are warm, foliage is green) but then admits off-white siding, which
is also neutral — the horizon walks down through the roof and into the wall.

Local variance, the standard textbook cue ("sky is smoother than buildings"),
does not hold under overcast: measured on real data here, sky reached a 90th
percentile local deviation of 55.8 against structure's median of 23.5. Heavy
cloud is *more* textured than siding. Do not reinstate it without re-measuring.

The heuristic therefore accepts a known compromise, and the segmentation backend
exists because no threshold resolves it.
"""

import numpy as np

SKY_CLASS_ADE20K = 2
_MODEL = "nvidia/segformer-b0-finetuned-ade-512-512"


def available(backend="segment"):
    """True if `backend` can run in this environment."""
    if backend == "heuristic":
        return True
    try:
        import torch  # noqa: F401
        import transformers  # noqa: F401
    except ImportError:
        return False
    return True


# ---- heuristic ------------------------------------------------------------
def _local_sky_reference(lum, valid, half_deg, px_per_deg):
    """Bright-end reference per column, over a neighbourhood in azimuth.

    A single global threshold cannot work outdoors under sun: sky near the Sun is
    far brighter than sky away from it, so one number is simultaneously too high
    on one side of the panorama and too low on the other.
    """
    h, w = lum.shape
    k = max(1, int(half_deg * px_per_deg))
    upper = lum[: max(1, int(h * 0.45))]
    upper_valid = valid[: max(1, int(h * 0.45))]
    ref = np.full(w, 200.0)
    for x in range(w):
        sl = np.arange(x - k, x + k + 1) % w
        vals = upper[:, sl][upper_valid[:, sl]]
        if vals.size > 200:
            ref[x] = np.percentile(vals, 92)
    return ref


def heuristic_sky(rgb, valid=None, px_per_deg=8.0, half_deg=15.0, lum_frac=0.30):
    """Sky mask from colour, with a locally adaptive brightness floor."""
    rgb = rgb.astype(np.float32)
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    lum = rgb @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    if valid is None:
        valid = np.ones(lum.shape, bool)
    ref = _local_sky_reference(lum, valid, half_deg, px_per_deg)
    not_warm = r < b + 12  # fences, brick, sunlit wood are warm
    not_green = g < b + 10  # foliage is green
    return not_warm & not_green & (lum > lum_frac * ref[None, :]) & valid


# ---- segmentation ---------------------------------------------------------
class _Segmenter:
    """Lazily loaded model, kept alive across images."""

    def __init__(self, model=_MODEL):
        from transformers import SegformerForSemanticSegmentation, SegformerImageProcessor

        self.proc = SegformerImageProcessor.from_pretrained(model)
        self.model = SegformerForSemanticSegmentation.from_pretrained(model)

    def __call__(self, image):
        import torch

        inputs = self.proc(images=image, return_tensors="pt")
        with torch.no_grad():
            out = self.model(**inputs)
        seg = self.proc.post_process_semantic_segmentation(out, target_sizes=[image.size[::-1]])
        return seg[0].cpu().numpy()


_SEG = None


def _tiles(w, h):
    """Left edges of overlapping square tiles covering a wide image.

    The model expects something like a normal photograph; squashing a 6:1
    panorama into its square input distorts it past recognition.
    """
    step = int(h * 0.75)
    xs = list(range(0, max(1, w - h + 1), step))
    if xs[-1] != w - h:
        xs.append(w - h)
    return xs


def segment_classes(image, tile=True):
    """Raw ADE20K class per pixel, as an int array.

    `segment_sky` reduces to sky/not-sky, which throws away the one thing the
    telescope cannot supply: WHICH obstruction. `obstruction_classes` needs the
    classes themselves, and without this there is no way to get them through the
    package — the type would have to come from colour again, which is the
    failure the segmentation replaced.

    Where tiles overlap, the class is decided by majority vote. Averaging is not
    available here as it is for the boolean mask: class indices are labels, not
    magnitudes, and the mean of "tree" and "building" is neither.
    """
    global _SEG
    if _SEG is None:
        _SEG = _Segmenter()
    w, h = image.size
    if not tile or w <= h * 1.6:
        return np.asarray(_SEG(image), dtype=int)
    votes = {}
    for x0 in _tiles(w, h):
        seg = np.asarray(_SEG(image.crop((x0, 0, x0 + h, h))), dtype=int)
        for cls in np.unique(seg):
            box = votes.setdefault(int(cls), np.zeros((h, w), np.int32))
            box[:, x0 : x0 + h] += seg == cls
    if not votes:
        return np.zeros((h, w), dtype=int)
    labels = sorted(votes)
    stack = np.stack([votes[c] for c in labels], axis=0)
    return np.asarray(labels, dtype=int)[np.argmax(stack, axis=0)]


def segment_sky(image, tile=True):
    """Sky mask for a PIL image, via semantic segmentation.

    Kept separate from `segment_classes` rather than derived from it: the
    boolean mask votes on the sky FRACTION across overlapping tiles, which is a
    softer and better-behaved decision at a tile seam than taking whichever
    single class won.
    """
    global _SEG
    if _SEG is None:
        _SEG = _Segmenter()
    w, h = image.size
    if not tile or w <= h * 1.6:
        return _SEG(image) == SKY_CLASS_ADE20K
    acc = np.zeros((h, w), np.float32)
    cnt = np.zeros((h, w), np.float32)
    for x0 in _tiles(w, h):
        seg = _SEG(image.crop((x0, 0, x0 + h, h)))
        acc[:, x0 : x0 + h] += (seg == SKY_CLASS_ADE20K).astype(np.float32)
        cnt[:, x0 : x0 + h] += 1
    return (acc / np.maximum(cnt, 1)) > 0.5


def sky_mask(image, backend="auto", **kw):
    """Sky mask for a PIL image. backend: 'segment', 'heuristic', or 'auto'."""
    if backend == "auto":
        backend = "segment" if available("segment") else "heuristic"
    if backend == "segment":
        return segment_sky(image, **{k: v for k, v in kw.items() if k == "tile"})
    return heuristic_sky(np.asarray(image.convert("RGB")), **kw)


# ---- horizon from a sky mask ---------------------------------------------
def horizon_rows(sky, valid=None, run=6):
    """First row per column where terrain begins, walking down from the data top.

    `run` consecutive non-sky rows are required. Both a lattice fence and a tree
    canopy show sky through their gaps, so any rule that keeps descending past
    the first obstruction traces the fence bottom instead of the treeline; the
    run length is what tolerates a single branch crossing an open column.

    Returns (rows, clipped). `clipped` marks columns whose obstruction reaches the
    top of the data — those record where the frame was cropped, not where the sky
    is, and must never be used as measurements.
    """
    h, w = sky.shape
    if valid is None:
        valid = np.ones((h, w), bool)
    rows = np.full(w, np.nan)
    clipped = np.zeros(w, bool)
    kern = np.ones(run)
    for x in range(w):
        idx = np.flatnonzero(valid[:, x])
        if len(idx) < max(run + 2, 20):
            clipped[x] = True
            continue
        blocked = ~sky[idx, x]
        runs = np.convolve(blocked.astype(float), kern, "valid")
        hit = np.flatnonzero(runs >= run)
        if not len(hit):
            continue  # column is open all the way down
        rows[x] = idx[hit[0]]
        clipped[x] = hit[0] <= run
    return rows, clipped


def upper_envelope(rows, half_deg, px_per_deg):
    """Take the highest horizon within +-half_deg of each column.

    A gap narrower than the mask's own resolution is not usable sky: a branch
    gap or a dip between two roof peaks still blocks the telescope. Without this
    the horizon dives into every hole in a tree canopy.
    """
    k = max(1, int(half_deg * px_per_deg))
    n = len(rows)
    idx = (np.arange(n)[:, None] + np.arange(-k, k + 1)[None, :]) % n
    with np.errstate(all="ignore"):
        return np.nanmin(rows[idx], axis=1)  # smallest row = highest altitude


# ---- obstruction structure ------------------------------------------------
# ADE20K classes that behave like vegetation: gappy, seasonal, partly
# transmissive. Everything else that blocks is treated as opaque structure.
VEG_CLASSES_ADE20K = (4, 9, 17, 66)  # tree, grass, plant, flower


def horizon_band(sky, valid=None, run=6):
    """Per column: where obstruction starts, where it ends, and how solid it is.

    A wall is a line; a tree is a band. Foliage has gaps with more foliage above
    them, so a single altitude misrepresents it in both directions — reporting
    the first obstruction understates the true ceiling, and reporting the top
    hides that there is usable sky in between.

    Returns a dict of arrays:
      first     row where obstruction first appears (lowest altitude)
      top       row above which everything is sky (highest altitude)
      porosity  fraction of rows between top and first that ARE sky
      clipped   obstruction reaches the top of the data

    porosity near 0 is a solid edge; a high value means a gappy canopy where the
    horizon is genuinely ambiguous and the mask should carry that as uncertainty
    rather than pretending to a single number.
    """
    h, w = sky.shape
    if valid is None:
        valid = np.ones((h, w), bool)
    first = np.full(w, np.nan)
    top = np.full(w, np.nan)
    porosity = np.zeros(w)
    clipped = np.zeros(w, bool)
    kern = np.ones(run)
    for x in range(w):
        idx = np.flatnonzero(valid[:, x])
        if len(idx) < max(run + 2, 20):
            clipped[x] = True
            continue
        blocked = ~sky[idx, x]
        runs = np.convolve(blocked.astype(float), kern, "valid")
        hit = np.flatnonzero(runs >= run)
        if not len(hit):
            continue
        f = hit[0]
        first[x] = idx[f]
        clipped[x] = f <= run
        # highest obstruction anywhere in this column, gaps notwithstanding
        anyblock = np.flatnonzero(blocked)
        t = anyblock[0] if len(anyblock) else f
        top[x] = idx[t]
        # Of everything below the canopy top, how much is still sky? A wall
        # gives zero; a gappy canopy gives the fraction you could in principle
        # see through, which is the honest measure of how ill-defined the
        # boundary is.
        span = blocked[t:]
        porosity[x] = float((~span).mean()) if len(span) else 0.0
    return {"first": first, "top": top, "porosity": porosity, "clipped": clipped}


def obstruction_classes(seg, rows, valid=None, window=12):
    """Dominant ADE20K class just below the horizon, per column.

    Taken from the segmentation of an in-focus photograph, because a telescope
    focused at infinity cannot supply it: at 250mm every terrestrial target is
    far inside the hyperfocal distance, so its frames are a blur and any type it
    reports from there is inferred from colour alone.

    That is a limit of the focus position rather than of the instrument — the
    scope can autofocus in scenery mode and resolve terrestrial detail, which
    would let it measure type directly (terminus-32). This function is the
    photograph's answer either way.
    """
    h, w = seg.shape
    out = np.full(w, -1, dtype=int)
    for x in range(w):
        r = rows[x]
        if not np.isfinite(r):
            continue
        lo = int(min(h - 1, r + 1))
        hi = int(min(h, r + 1 + window))
        band = seg[lo:hi, x]
        if valid is not None:
            band = band[valid[lo:hi, x]]
        if band.size:
            vals, counts = np.unique(band, return_counts=True)
            out[x] = int(vals[np.argmax(counts)])
    return out


def type_uncertainty(classes, porosity, veg_deg=3.0, solid_deg=1.0):
    """Per-column altitude uncertainty, widened for vegetation.

    Vegetation earns extra fuzz for two independent reasons: its boundary is
    genuinely a band rather than a line, and it is seasonal — a deciduous canopy
    measured in August is not the horizon you get in January. A roofline is
    neither, so it keeps the base uncertainty.
    """
    veg = np.isin(classes, VEG_CLASSES_ADE20K)
    base = np.where(veg, veg_deg, solid_deg)
    return base * (1.0 + np.clip(porosity, 0.0, 1.0))
