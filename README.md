# terminus

*terminus* (Latin: boundary, limit) measures the **local horizon** at your
observing site — the real skyline of rooftops, fences, and trees that decides
what you can actually image — and exports it for planning tools like **N.I.N.A.**,
**Stellarium** and **Sky Safari**, or queries it directly as a library so a scheduler can ask
*"is this target clear of my horizon right now?"*

The horizon comes from **photographs**. A [Seestar S50](https://www.seestar.com/)
is used to **orient** them.

> Companion to [uranometria](https://github.com/devonjones/uranometria) (star
> charts and annotated astrophotos). terminus is the horizon; uranometria is the
> sky above it.

## How the work is split

A telescope focused at infinity cannot resolve your neighbour's roof. The Seestar
is 250 mm at f/5, so its hyperfocal distance is about 4.3 km and every
terrestrial target sits roughly a thousand times inside it. It measures altitude
well and cannot identify anything. A phone photograph is in focus by default and
samples the skyline about two orders of magnitude more finely.

So each instrument does what it is good at:

- **The photographs are the mask.** A registered panorama gives a dense skyline
  and, through semantic segmentation, what each obstruction *is*.
- **The telescope is the calibration.** A handful of accurately known altitudes
  fix three parameters — yaw, pitch, tilt — placing the photo sphere on the sky.

The scope's job is therefore three numbers, not a whole circle. Columns are
chosen by information gain and the run stops when the orientation stops moving;
at this site that took **seven columns, about twenty minutes**.

**How many you need depends on your horizon, and not in the direction you would
expect.** Yaw is only constrained where the horizon has *gradient* — across a
long flat roofline, rotating the sphere barely changes the predicted altitude, so
measuring there says almost nothing. Cliffs, tall trees and sharp rooflines carry
the information. **An even, open horizon is the hard case** and needs more
columns, not fewer.

**Two capture modes, never mixed in one solve:** individual photos (preferred —
exact pinhole geometry, registered with Hugin), or one already-stitched phone
panorama (fallback — the phone's projection is unknown and it cannot see above
its own frame).

## Status

| | State |
|---|---|
| Scope sweep, mask, exports, `Horizon` queries | **Working, on the CLI** |
| Photo registration and segmentation | **Working, on the CLI** (`mosaic`, `skymask`) |
| Orientation fit and adaptive column choice | **Working, on the CLI** (`orient`) |
| Night detection | **Implemented and tested, nothing calls it** |

Run end to end on real hardware for the first time on 2026-08-05: nineteen phone
frames to a solved orientation, with the yaw reproducing exactly across two
independent runs an hour apart. That run also found four bugs that 211 passing
tests had not — every mask a real sweep wrote was unparseable, the Sun-cone
check tested only a column's endpoints, the Sun guard could refuse to move the
mount *away* from the Sun, and a darkening sky manufactured false measurements.
All four are fixed. Read **What breaks**, below, before relying on a result.

`terminus mosaic` and `terminus skymask` build a horizon from photographs and
need no telescope, no network and no `config.toml`. What they produce is
**unoriented** — azimuth is the panorama's own until the orientation is solved
against telescope-measured columns, so it declares that in its header and
refuses to export.

`terminus orient` resolves that. It chooses a column, measures it, refits, and
stops when the solved yaw holds still — typically after seven or eight columns
rather than a blind circle of thirty-six:

```bash
terminus orient pano_mask.yaml                    # ~20 minutes of scope time
terminus orient pano_mask.yaml --replay run_profiles.json   # no telescope at all
```

`--replay` re-judges a night you already measured. Every sweep writes
`<mask>_profiles.json` beside its mask, so a planner change can be compared
against a real run without waiting for another clear night — and the loop is
therefore testable without hardware.

It stops on the **stability of the yaw, not on the residual**. With four columns
fitting four parameters the fit interpolates and the RMS reads near zero however
wrong the answer is; one fit reported 0.28° from four edges and was nearly
believed. RMS is also blind to uniform bias, because a constant offset is
absorbed into pitch. If the yaw has not settled by the column budget, the run
says so and marks the result provisional rather than letting it read as
converged.

`terminus export --allow-unoriented` still exists and is only correct if you
know the azimuths are already true — if you set north by hand, say. Otherwise
the exported horizon is rotated by an unknown amount, which a planner cannot
detect.

`night` is still library-only.

```python
from terminus import mosaic, skymask, orient, plan
```

Importing terminus never pulls in torch or transformers and never shells out to
Hugin; `skymask.available()` and `mosaic.hugin_available()` report what you have.

So `terminus sweep` today runs the **scope-only** fallback: a blind circle, with
obstruction type guessed from colour in a blurred frame. Treat that type column
as a hint, not a measurement.

## What you get

- A durable, hand-editable **mask** (`horizon_mask.yaml`) with per-azimuth
  altitude and obstruction type.
- **`horizon.hrz`** for N.I.N.A. (Options → General → Horizon) and
  **`horizon.stellarium.txt`** for a Stellarium polygonal landscape.
- A per-azimuth **review frame** so you can eyeball every measurement.
- A **`Horizon`** class for planners: `altitude_at(az)`, `is_above(az, alt)`,
  `is_visible(ra, dec, sky)`.

**The horizon is specific to where the tripod stood**, and what matters most is
moving *closer to* a near obstruction, not sideways past it:

| moving 2 m, against a 2 m fence 5 m away | change |
|---|---|
| **2 m closer** | **+11.9°** |
| 2 m further away | −5.9° |
| along the fence (perpendicular distance unchanged) | 0° |
| past a narrow obstruction — a post, a chimney | −1.4° |

Closer and further are not mirror images. `atan` is nonlinear, so approaching an
obstruction costs more than retreating from it gains — which is why "toward or
away" was the wrong way to say this.

A 100 m ridge 2 km out moves 0.003° for the same 2 m, so distance is the whole
story. The error is largest exactly where the horizon is highest, because the
tall obstructions are the near ones. Measure from where you observe, and
re-measure if you move — above all if you move NEARER, which is the case that
bites hardest.

This is geometry, not something measured here. Note also that the mask and the
`.hrz` carry this warning in their headers but the Stellarium `.txt` cannot:
that format forbids comments.

Anything classified as vegetation is exported with a **3° safety margin** added,
applied at a single choke point every exporter routes through. A tree has gaps
low down with more canopy above, it moves in wind, and a crown measured in August
is not the horizon you get in January.

## Requirements

**Core:**

- A Seestar S50 on your network, in **EQ mode on a polar-aligned mount**
  (terminus points by RA/Dec goto; in EQ mode the scope's alt/az readout is not
  trustworthy but its RA/Dec pointing is).
- Python ≥ 3.11 and **ffmpeg** on `PATH` (the scenery preview arrives over RTSP).
- The **interop key** (see below).

**Optional, for the photo pipeline:**

- **hugin-tools** — `pto_gen`, `cpfind`, `cpclean`, `autooptimiser`, `nona`,
  `pano_modify`. No blender (`enblend`) is needed: `nona` renders separate
  `TIFF_m` layers and terminus composites them itself, solving per-frame gains
  first, because a circle shot under sun guarantees frames metered differently.
- **torch**, **torchvision**, **transformers** for semantic segmentation
  (SegFormer, ADE20K). Without them `skymask` falls back to a colour heuristic,
  which is markedly worse. About 15 MB of model download, ~2 s per frame on CPU.

Runs headless — a Raspberry Pi is a fine host, same as
[seestar_alp](https://github.com/smart-underworld/seestar_alp). Core dependencies
(numpy, pillow, cryptography, astropy, pyyaml) ship ARM wheels; `apt install
ffmpeg`. The segmentation stack is heavier and may not be practical on a Pi.

## The interop key

Firmware 7.18+ requires a signed challenge/response before it will accept
commands. The signing key is an RSA private key ZWO ships **in the clear** inside
the Android app. terminus does **not** include it, and does not document how to
get it out — you extract it once, yourself, from the app you already own, using
[seestar-tool's **Extract PEM**](https://github.com/bguthro/seestar-tool#extract-pem).

Save the result (e.g. `~/.seestar/seestar_client_key.pem`, mode 600) and point
`config.toml` at it.

This is interoperation with a device you own (17 U.S.C. §1201(f)); the key is
never committed and `*.pem` is git-ignored. Legality varies by jurisdiction — you
are responsible for your use.

## Install

```bash
git clone https://github.com/devonjones/terminus
cd terminus
uv sync            # or: pip install -e .
cp config.example.toml config.toml   # then edit host + pem path
```

## Use

### Start to finish, the way it was actually run

This is the sequence run end to end on 2026-08-05, with the numbers that run
produced. Read the **What breaks** section below before trusting the result of
the last step.

**1. Photograph the horizon.** Individual overlapping frames, held roughly
level, all the way round. Nineteen shots two seconds apart worked; seventeen to
eighteen of them register, the exact count varying with what `cpfind` finds on
the day.

> Frames, **not** your phone's panorama mode. A finished panorama cannot be
> matched to another finished panorama, and `mosaic` will spend twenty minutes
> discovering that before it tells you.

**2. Register them into one equirectangular canvas, and segment while you are
there.**

```bash
terminus mosaic ~/horizon-frames --out pano --segment
```

`--segment` runs SegFormer on each **frame** and warps the labels through the
same solve, rather than segmenting the stitched result. That matters more than
it sounds: segmenting the finished canvas labels 44% of it — the black region
nobody photographed — as walls, grass and hills, and 3% of it as *sky*.

**3. Read a horizon off the panorama.**

```bash
terminus skymask pano.png --coverage pano.coverage.npy --classes pano.classes.npy
```

Out comes a mask with a per-column altitude, an obstruction type, and an
uncertainty — about 1° for structure, 3° for foliage. It is **unoriented**: the
azimuth is the panorama's own, and it refuses to export until that is solved.

**4. Solve where that horizon sits on the sky.** This is the step that needs
telescope measurements — though not necessarily a telescope tonight.

```bash
terminus orient pano_mask.yaml --out oriented.yaml
```

It measures a few evenly spaced columns, then chooses each next column by how
much it will shrink the uncertainty in the fit, refits after every one, and
stops when the solved yaw stops moving.

> The opening columns are spaced evenly and chosen **before** the Sun's position
> is consulted, so in daylight some of them can be unreachable and the run
> spends time discovering that. Worse, a run that ends without a fit currently
> discards the columns it did measure. Both are terminus-58, and until it lands
> a daylight run can lose an observing window. Roughly **2.5 minutes per column**, so
eight chosen columns is about **20 minutes** — against ~95 minutes for a blind
36-column circle.

Every column it measures is saved, so `--replay <profiles.json>` will re-run the
whole loop later with no telescope and no sky.

If you already have measured columns — a sweep from a previous night, or someone
else's — `--fiducials` takes them straight from the mask a sweep wrote:

```bash
terminus orient pano_mask.yaml \
  --fiducials horizon_part2.yaml --fiducials horizon_west.yaml \
  --out oriented.yaml
```

Repeatable and merged first-wins, because a sweep is routinely split across arcs
and nights. The planner still chooses the order by information gain — it is
confined to azimuths a fiducial exists for, not handed a fixed list — so this
also exercises the loop itself without spending a clear night on it.

**5. Export it.**

```bash
terminus export oriented.yaml --skysafari --landscape --pvsyst
```

**6. Look at it.**

```bash
terminus polar oriented.yaml
```

One self-contained HTML file — no network, no assets, nothing to serve — showing
the whole sky as a disc with north up and the photograph reprojected through the
solved rotation. The layers toggle: the horizon ring, the telescope's own
columns, the region no photograph covered, and the altitude grid.

This is the check that catches what a residual cannot. A wrong yaw stops being a
number and becomes the neighbour's house in the wrong place. Turn the horizon
layer off and on and see whether the yellow line follows the roofline; turn on
"not photographed" and see whether the gaps are where you think they are.

```bash
# draw the telescope's measured columns over the photograph too
terminus polar oriented.yaml --fiducials horizon_part2.yaml

# the disc reaches 20 degrees below the horizon by default, so the deck and
# fence are in frame; --floor 0 stops at the horizon
terminus polar oriented.yaml --floor -35 --size 1600
```

Columns that reached the sweep's altitude ceiling are drawn as upward chevrons
rather than dots, because the scope stopped at its tilt limit: the horizon there
is *at least* that high, not exactly that high. Without a panorama the command
still works and draws the measured horizon on a plain disc, which is all a
scope-only sweep can honestly support.

### Reproduce it without a telescope

The whole pipeline, photographs to a page you can look at, with no hardware and
no clear night. Every command below is the real one; the numbers are what this
produced on 2026-08-06 from the 19 frames in `captures/panoramas/2026-08-03-frames/`
and the telescope sweeps in `captures/2026-08-03-evening/`.

```bash
# 1. register the frames and segment each one  (~4 minutes)
terminus mosaic captures/panoramas/2026-08-03-frames --out pano --segment

# 2. read the skyline off the result  (~1 minute)
terminus skymask pano.png --coverage pano.coverage.npy --classes pano.classes.npy \
  --out photo_mask.yaml

# 3. solve the rotation against columns the telescope already measured
terminus orient photo_mask.yaml \
  --fiducials captures/2026-08-03-evening/horizon_part2.yaml \
  --fiducials captures/2026-08-03-evening/horizon_west.yaml \
  --out oriented.yaml

# 4. look at it
terminus polar oriented.yaml --fiducials captures/2026-08-03-evening/horizon_part2.yaml
```

What each step should tell you:

| Step | Expect |
|---|---|
| `mosaic` | `registered 17 frames, dropped 2` — the dropped ones have 0 control points |
| `mosaic --segment` | `55% of pixels labelled` (the rest is sky nobody photographed) |
| `skymask` | `360 columns, 0 clipped`, and a refusal to export: the mask is **unoriented** |
| `orient` | `yaw settled within 1 deg over 3 refits`, seven columns |
| `polar` | one self-contained HTML file, ~460 KB |

```
yaw 133.69 (+/- 2 deg)  pitch 0.93  tilt 2.62 toward 120.0
rms 0.22 over 7 columns (1 of them bounds, +2 degrees of freedom)
```

**Do not read that 0.22 as better than the published 0.69.** It is a fit to seven
columns where the published one used sixteen, and the extra nine are the ones
that were hardest to measure — a smaller fiducial set will almost always show a
smaller residual whether or not it is closer to the truth. What is comparable is
the geometry: tilt lands within 0.2° of the published 2.81°, and yaw within
about 2° of 135.81°.

The step that would need a telescope is the one supplying `--fiducials`. Those
columns came from two evening sweeps; measuring your own is `terminus sweep`, or
`terminus orient` with no `--fiducials` at all, which chooses columns adaptively
and typically wants seven or eight of them.

### What breaks

Written down because it will bite you, and because it is being worked on rather
than hidden.

**Do not trust a run that crosses dusk or dawn.** As the sky darkens, a column
that the detector can no longer resolve is recorded as *blocked above the search
ceiling* — a one-sided claim the fit treats as strong evidence. One such column
moved a solved yaw by **164°**. The fix is to select the detector from the
measured sky brightness (`night.py` exists for this and is not yet wired in);
until then, run in daylight or under a settled dark sky, not across the change.

**A sweep interrupted loses everything it measured.** The mask is written once,
at the end. Ctrl-C forty minutes in and forty minutes are gone.

**The Sun is checked for where it is now, not where it will be.** A column can be
clear when it is chosen and not by the time it is measured. At dusk this errs
safe; at dawn it does not.

**Pointing is only as good as the polar alignment**, and nothing yet measures
that for you — see *Plate solve first* below.

```bash
terminus preflight                 # connect, show Sun + which azimuths it blocks
terminus point 75 45               # slew to az 75 / alt 45 (Sun-guarded), verify
terminus classify                  # sky / vegetation / structure at current pointing
terminus sweep                     # full sweep -> horizon_mask.yaml + .hrz + .txt
terminus orient pano_mask.yaml     # solve where the photo horizon sits on the sky
terminus export horizon_mask.yaml  # re-export a mask without re-sweeping

# pictures, not just numbers: a Sky Safari panorama and a Stellarium landscape
terminus export horizon_mask.yaml --skysafari --landscape
# and a PVsyst .HOR profile, for the solar-siting tools that got here first
terminus export horizon_mask.yaml --pvsyst
# ...and with the panorama itself as the texture, gaps shown as sky
terminus export horizon_mask.yaml --landscape \
    --texture pano.png --coverage pano.coverage.npy
```

`--skysafari` writes a 2048x1024 RGBA PNG in Sky Safari's panorama convention —
north at the left edge, zenith at the top, and **alpha as the horizon**, since
that is what the app actually reads. Load it under *Settings -> Horizon & Sky ->
Show Horizon & Sky as Panoramic Image*.

`--landscape` writes a Stellarium landscape directory: `landscape.ini`,
`horizon.txt` and, with `--texture`, `maptex.png`. Without imagery it is
`type=polygonal`, because declaring a photograph that does not exist would be a
lie; with imagery it is `type=spherical` and ships the measured polygon
alongside the picture so the numbers and the image agree. Copy the directory
into Stellarium's `landscapes/` folder.

Where the panorama has no coverage the pixel is transparent, so the program
draws sky. A gap in the survey is not black ground, and presenting it as such
would give the viewer a wall to trust that nobody ever photographed.

`--pvsyst` writes a `.HOR` horizon profile. PVsyst's import dialog asks for the
rotation direction and the north azimuth angle, because its convention is not
ours — set **Clockwise** and **0**, which the file's own header repeats. Guessing
gives a mirrored horizon that still looks plausible.

The `.hrz` is drop-in where [HRZ-Creator](https://neuronburner.com/hrz-creator/)
or [panorama-horizon-maker](https://github.com/danngalann/panorama-horizon-maker)
output went: same space-separated `az alt` pairs, ascending from 0 and closed at
360. terminus adds `#` comment lines, which N.I.N.A. accepts and they never
wrote.

Review the `*_frames/` images, correct any misjudged rows in the mask by hand,
then `terminus export` again.

### Plate solve first

**A mask is only as good as the pointing underneath it.** An unverified polar
alignment biases *every* column by the same amount, and the fit absorbs that bias
silently rather than reporting it — a 3.7° drift here moved the solved
orientation by 5.6°. Plate solve and correct the mount before measuring columns
you intend to rely on.

### When to run it

**On a clouded-out night.** A full circle takes hours and returns nothing you
could have imaged instead, so it costs a night you were going to lose anyway.

Cloud helps rather than merely being tolerable. The sweep finds the horizon by
brightness — sky is the light source, everything terrestrial silhouettes against
it — and an overcast sky under suburban light pollution is a bright, even
backdrop. A clear moonless sky is the *harder* case: darker overhead means less
contrast against the treeline.

Two things matter:

- **Let the Sun set first.** The Sun guard refuses any slew whose path passes
  near it, so daytime columns toward the Sun are skipped rather than measured.
- **Budget the evening for the blind circle.** At the shipped `az_step = 5` a
  full circle is 72 columns, plus adaptive refinement where the horizon moves
  fastest. `az_step = 10` halves it, more coarsely. Within a column the scan is
  not a fixed ladder — it walks down from `alt_max` and bisects onto the
  brightness step to `alt_tol`. The open-sky reference is re-measured as the run
  proceeds, so a sweep may safely span twilight into full dark.

### As a library

```python
from terminus import Horizon, Sky
h = Horizon.from_mask("horizon_mask.yaml")
sky = Sky(39.79, -104.89, 1600)
h.is_visible(ra_hours=20.2, dec_deg=38.4, sky=sky)   # NGC 6888 clear right now?
h.altitude_at(120)                                    # horizon altitude at az 120
```

## Safety

terminus recomputes the Sun's position for the exact current time and refuses any
pointing within `sun_cone_deg`. Every slew is broken into small steps, each
re-checked, so the tube cannot arc through the Sun between waypoints; a two-hop
route re-checks clearance after the first leg lands, since a long slew can take
minutes. Even so: **run it with the scope in shade or well away from the Sun's
part of the sky**, and keep a solar-safe cap/dew shield on. Pointing a small
refractor at the Sun can destroy the sensor. Use at your own risk.

## How it decides

**From photographs**, the intended path: SegFormer on ADE20K decides sky versus
terrain, and the class just below the skyline gives the obstruction type. The
skyline must be a *sustained* run of non-sky rows, so the trace cannot thread
through lattice gaps or a gappy canopy. Without the segmentation stack a colour
heuristic stands in, and it is a real downgrade — an off-white wall reads as sky,
and a dark branch against bright sky still reads blue.

**From the scope**, the fallback: brightness alone. A conservative threshold
treats dappled canopy as blocked, so the recorded horizon is the *top* of the
foliage. After dark the two-level model breaks down — skyglow rises roughly
threefold toward the horizon and streetlights inside the terrain outshine the sky
— so the night path fits the skyglow gradient, masks the lamps, and finds where a
column departs the model.

Azimuth is true-north, increasing toward east.

## Prior art

Photographing a skyline to get an obstruction profile is not new.

- **[Georg Zotti's Stellarium landscape tutorial](https://www.archeoastronomy.org/assets/downloads/slides/ast/ast-12_seac2025-stellarium-landscape-course-notes.pdf)**
  (SEAC2025) documents the Hugin sequence terminus automates, and is the
  authoritative reference for `polygonal_horizon_list`, `angle_rotatez` and
  `maptex_top`/`bottom`.
- **`.hrz` generators**: [HRZ-Creator](https://neuronburner.com/hrz-creator/) and
  [panorama-horizon-maker](https://github.com/danngalann/panorama-horizon-maker).
  Both trace the skyline manually and set north by hand.
- **The N.I.N.A. Horizon Creator plugin** — phone frames at horizon inflections,
  angles from a phone alt/az app, skyline read by eye.
- **The solar industry, commercially, twenty years ago.** The
  [Solmetric SunEye 210](https://www.solmetric.com/product/suneye-210-shade-tool/)
  is a calibrated fisheye with compass, tilt sensor and GPS producing horizon
  altitude per degree of azimuth, exported as `.HOR` for PVsyst. Also
  HORIcatcher/Meteonorm and Solar Pathfinder.
- **Ecosystem neighbours**:
  [clear-horizons](https://github.com/njefferson/clear-horizons) solves north from
  the Sun; [seestar-mcp](https://github.com/OrangeAgente/seestar-mcp) infers
  obstruction arcs from where plate solving fails.
- **Closest published work**: Mephisto unobservable-region segmentation and
  LenghuSky-8 — all-sky cameras with star-based calibration, where the static
  obstructions are a *hand-drawn* mask rather than a segmentation output.

What this project has not found elsewhere is the combination: segmentation as the
sky/terrain decision with obstruction **type** carried into the export; a
telescope used as a fiducial source for a three-parameter orientation solve;
information-gain choice of the next column with a stopping rule on parameter
*stability* rather than residual; ceiling-limited columns as one-sided censored
bounds; and a night detector built *on* the skyglow gradient rather than
subtracting it.

That search was not exhaustive — the amateur-forum record was only sampled. If
you know of prior work here, please open an issue.

## Credits

Protocol groundwork from [seestar_alp](https://github.com/smart-underworld/seestar_alp);
key extraction from [seestar-tool](https://github.com/bguthro/seestar-tool).
Landscape-format details from Georg Zotti's Stellarium tutorial, above.
Apache-2.0.
