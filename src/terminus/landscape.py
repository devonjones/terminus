"""Render the horizon as a picture: Sky Safari panoramas and Stellarium landscapes.

Both targets want the same thing — an equirectangular image, azimuth across the
width and altitude down the height — so one renderer serves both and the two
exporters differ only in packaging.

    x: azimuth 0 (north) at the LEFT edge, increasing east, wrapping to 360 at
       the right edge.
    y: altitude +90 at the TOP, 0 at the middle, -90 at the BOTTOM.

That is Sky Safari's documented panorama convention (Simulation Curriculum:
32-bit RGBA PNG, 2048x1024, north at the left edge, zenith at the top). It is
also Stellarium's `type=spherical` maptex layout, and it is the projection our
own mosaic canvas already uses — `cmd_skymask` reads it back as
``alt = 90 - (y/h) * 180``.

ALPHA IS THE HORIZON. Neither program reads a drawn line: what makes a pixel
ground is that it is opaque. So nothing here paints a curve — it fills
everything below the horizon and leaves the sky transparent. A drawn line on an
opaque background would render as a completely blocked sky.

TWO MODES, and the difference is about honesty rather than looks:

  * SYNTHETIC — a flat silhouette from the mask alone. Works from a scope-only
    sweep, and shows exactly what was measured and nothing else.
  * PHOTO — the registered panorama as the texture, with the mosaic's coverage
    as the alpha. Where the phone never pointed the pixel is transparent, so the
    program draws sky instead of fabricated black ground.

The scope stays the source of geometric truth in both. The panorama is prettier
pixels over the same numbers, and Stellarium can carry both at once: the picture
as `maptex`, the measured horizon as `polygonal_horizon_list`.
"""

import os

import numpy as np

from .export import (
    TREE_BUFFER_DEG,
    _ascending_pairs,
    require_oriented,
    to_stellarium_txt,
)

# Sky Safari's documented panorama size. Other sizes render correctly, but this
# is what the vendor asks for.
SKYSAFARI_SIZE = (2048, 1024)


def horizon_altitudes(rows, width, tree_buffer=TREE_BUFFER_DEG):
    """Horizon altitude at each of `width` evenly spaced azimuths.

    Interpolated across the wrap, so the column at 359.9 degrees is continuous
    with the one at 0.1 rather than travelling back through the whole circle.

    Routes through `_ascending_pairs`, which is where the vegetation buffer is
    applied for every exporter. A picture that disagreed with the .hrz beside it
    would be worse than no picture: they are meant to be one horizon.
    """
    pairs = _ascending_pairs(rows, tree_buffer)
    az = np.array([p[0] for p in pairs], dtype=float)
    alt = np.array([p[1] for p in pairs], dtype=float)
    x = np.arange(width, dtype=float) * 360.0 / width
    return np.interp(x, az, alt, period=360.0)


def ground_alpha(rows, width, height, tree_buffer=TREE_BUFFER_DEG):
    """Boolean array, True where the pixel is ground.

    A pixel in row y has altitude ``90 - (y + 0.5) / height * 180``, and is
    ground when that sits at or below its column's horizon. The half-pixel is
    not fussiness: without it the horizon lands on a pixel EDGE and the rendered
    line sits half a pixel high, which at 1024 rows is 0.09 degrees. Small, but
    it is a bias in one direction rather than a rounding error in both.
    """
    horizon = horizon_altitudes(rows, width, tree_buffer)
    y = np.arange(height, dtype=float)
    alt = 90.0 - (y + 0.5) / height * 180.0
    return alt[:, None] <= horizon[None, :]


def _resample(img, width, height):
    """Nearest-neighbour resample of an (h, w, ...) array to (height, width, ...).

    Nearest rather than interpolated on purpose: this also resamples the
    coverage mask, and a blended coverage edge would invent fractional data
    where the phone saw none.
    """
    ys = (np.arange(height) * (img.shape[0] / height)).astype(int).clip(0, img.shape[0] - 1)
    xs = (np.arange(width) * (img.shape[1] / width)).astype(int).clip(0, img.shape[1] - 1)
    return img[ys[:, None], xs[None, :]]


def _rotate_east(img, yaw_deg):
    """Roll an equirectangular image so its azimuth 0 lands at true north.

    A mosaic is in the PANORAMA's own azimuth until the orientation is solved,
    while the mask is in true azimuth once it is. Compositing the two without
    this would put the measured horizon over the wrong part of the picture — the
    exact error the package exists to make visible, silently introduced by the
    thing meant to reveal it.

    Rotating here rather than declaring it in landscape.ini is deliberate. See
    LANDSCAPE_INI: Stellarium has two rotation keys that turn two different
    things, and the surest way not to set the wrong one is to ship an image that
    needs neither.

    SIGN, derived rather than guessed, because it is exactly the kind of thing
    that looks right either way in a thumbnail. `orient` defines yaw by
    ``phi = target_az - yaw``, so a native panorama column `phi` sits at true
    azimuth ``phi + yaw``. The output pixel for true azimuth A must therefore
    come from the texture at ``A - yaw``:

        out[x] = tex[x - shift],  shift = yaw / 360 * width

    and ``np.roll(a, k)[x] == a[x - k]``, so the roll is by +shift. Rolling the
    other way moves the imagery by twice the yaw in the wrong direction, which
    for a small yaw still looks like a horizon — just not this one.
    """
    if not yaw_deg:
        return img
    shift = int(round(yaw_deg / 360.0 * img.shape[1]))
    return np.roll(img, shift, axis=1)


def render(
    rows,
    size=SKYSAFARI_SIZE,
    texture=None,
    coverage=None,
    tree_buffer=TREE_BUFFER_DEG,
    texture_yaw=0.0,
):
    """RGBA array for the horizon. Ground opaque, sky transparent.

    `texture` is an equirectangular RGB panorama (the mosaic) to draw as the
    picture; without one the ground is a flat fill. `coverage` is the mosaic's
    per-pixel frame count, and where it is zero the pixel is TRANSPARENT even
    below the horizon — the phone saw nothing there, and black would be
    inventing terrain rather than admitting a gap.

    `texture_yaw` rotates the texture into the mask's frame; see `_rotate_east`.

    The measured horizon bounds the opaque region even with a texture. The
    panorama is pixels, the mask is the measurement, and where they disagree the
    measurement wins.
    """
    width, height = size
    ground = ground_alpha(rows, width, height, tree_buffer)
    rgba = np.zeros((height, width, 4), dtype=np.uint8)
    if texture is None:
        # Flat neutral, not black. Both programs dim the ground at night, and
        # something already black gives the eye nothing to place the horizon
        # against.
        rgba[..., :3] = 60
    else:
        tex = _rotate_east(np.asarray(texture), texture_yaw)
        if tex.shape[:2] != (height, width):
            tex = _resample(tex, width, height)
        rgba[..., :3] = tex[..., :3]
        if coverage is not None:
            cov = _rotate_east(np.asarray(coverage), texture_yaw)
            if cov.shape != (height, width):
                cov = _resample(cov, width, height)
            ground = ground & (cov > 0)
    rgba[..., 3] = np.where(ground, 255, 0)
    return rgba


def to_skysafari_png(
    rows,
    path,
    meta=None,
    size=SKYSAFARI_SIZE,
    texture=None,
    coverage=None,
    tree_buffer=TREE_BUFFER_DEG,
    allow_unoriented=False,
):
    """Write a Sky Safari panorama horizon.

    Gated on orientation for the same reason as the other exporters, and more
    sharply: a PNG carries no comment line, so an unoriented panorama cannot
    warn about itself. It would simply show the wrong horizon.

    Load it under Settings -> Horizon & Sky, choose "Show Horizon & Sky as
    Panoramic Image", and pick the file from the Panorama Horizon list.
    """
    from PIL import Image

    require_oriented(meta, allow_unoriented)
    rgba = render(rows, size, texture, coverage, tree_buffer, _yaw(meta))
    Image.fromarray(rgba, "RGBA").save(path)
    return path


def _yaw(meta):
    """Rotation from the panorama's own azimuth to true north, degrees.

    Absent means zero, which is correct for the two cases that exist today: a
    scope mask is measured in true azimuth already, and a photo mask is refused
    by `require_oriented` before it gets here.
    """
    return float((meta or {}).get("yaw") or 0.0)


# Stellarium reads the picture and the numbers from different keys, and rotates
# them with two more. `angle_rotatez` turns the IMAGE; `polygonal_angle_rotatez`
# turns the POLYGON. Stellarium's own "zero" landscape comments on why they are
# separate: a landscape may mix photo and polygon.
#
# Both are written as 0 here, and that is the point. The horizon list is already
# in true azimuth, and `render` rotates the texture into the same frame rather
# than asking Stellarium to do it. Setting either key would turn one of the two
# away from the other, and a package whose numbers and picture disagree destroys
# the one property that makes it worth shipping: that a wrong yaw shows up as
# the observer's own house in the wrong place.
LANDSCAPE_INI = """[landscape]
name = {name}
author = {author}
description = {description}
type = {type}
{texture_keys}polygonal_horizon_list = horizon.txt
polygonal_horizon_list_mode = azDeg_altDeg
polygonal_angle_rotatez = 0
ground_color = {ground_color}
minimal_brightness = 0.05
{location}"""

# Written only when the mask actually recorded a position. A landscape.ini with
# no [location] leaves the observer where they already are, which is the honest
# outcome for a horizon whose site we do not know. Writing `latitude = 0,
# longitude = 0` instead would move a Stellarium user to the Gulf of Guinea and
# tell them nothing was wrong — a fabricated value dressed as a default, which
# is the habit this repo refuses everywhere else.
#
# `altitude` is omitted rather than defaulted for the same reason: no mask
# records elevation today, and 0 m is a claim, not an absence.
LOCATION_INI = """
[location]
planet = Earth
latitude = {lat}
longitude = {lon}
"""

_SPHERICAL_KEYS = "maptex = maptex.png\nmaptex_top = 90\nmaptex_bottom = -90\nangle_rotatez = 0\n"


def write_landscape(
    directory,
    rows,
    meta=None,
    name="terminus",
    author="terminus",
    texture=None,
    coverage=None,
    tree_buffer=TREE_BUFFER_DEG,
    allow_unoriented=False,
):
    """Write a complete Stellarium landscape directory.

    Produces `landscape.ini`, `horizon.txt` (the measured polygon) and, with a
    texture, `maptex.png`. Install by copying the directory into Stellarium's
    `landscapes/` folder, or by pointing its Load Landscape button at a zip.

    Without a texture this is `type=polygonal`: the measured horizon and a flat
    ground colour, which is all a scope-only sweep can honestly support. With
    one it is `type=spherical`, and the polygon ships alongside the picture so
    the numbers and the imagery agree.

    The `[location]` section appears only when the mask recorded a position;
    see `LOCATION_INI`.

    `maptex_top` and `maptex_bottom` are declared +90/-90 because the mosaic
    canvas IS full-sphere equirectangular. The phone does not FILL that canvas,
    and the honest treatment of the empty part is transparency (see `render`),
    not a narrower declaration — narrowing would stretch the imagery we do have
    across sky nobody photographed.
    """
    require_oriented(meta, allow_unoriented)
    os.makedirs(directory, exist_ok=True)
    meta = meta or {}

    with open(os.path.join(directory, "horizon.txt"), "w") as f:
        # allow_unoriented=True: already checked above, and re-checking here
        # would refuse a package the caller was explicitly permitted to build.
        f.write(to_stellarium_txt(rows, meta, tree_buffer, allow_unoriented=True))

    texture_keys, kind = "", "polygonal"
    if texture is not None:
        from PIL import Image

        tex = np.asarray(texture)
        size = (tex.shape[1], tex.shape[0])
        rgba = render(rows, size, texture, coverage, tree_buffer, _yaw(meta))
        Image.fromarray(rgba, "RGBA").save(os.path.join(directory, "maptex.png"))
        texture_keys, kind = _SPHERICAL_KEYS, "spherical"

    ini = LANDSCAPE_INI.format(
        name=name,
        author=author,
        description=(
            f"Horizon measured with terminus from {meta.get('lat', '?')},{meta.get('lon', '?')} "
            f"on {meta.get('measured', '?')}. POSITION-SPECIFIC: a near obstruction shifts by "
            "degrees for a few metres of observer displacement, so this describes one spot."
        ),
        type=kind,
        texture_keys=texture_keys,
        # Stellarium's own dark green. Only visible in the polygonal case.
        ground_color=".15,.45,.05",
        location=(
            LOCATION_INI.format(lat=meta["lat"], lon=meta["lon"])
            if meta.get("lat") is not None and meta.get("lon") is not None
            else ""
        ),
    )
    with open(os.path.join(directory, "landscape.ini"), "w") as f:
        f.write(ini)
    return directory
