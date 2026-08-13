# terminus

*terminus* (Latin: boundary, limit) measures the **local horizon** at your
observing site — the real skyline of rooftops, fences, and trees that decides
what you can actually image — and exports it for planning tools like **N.I.N.A.**,
**Stellarium** and **Sky Safari**, or queries it directly as a library so a scheduler can ask
*"is this target clear of my horizon right now?"*

The horizon comes from **photographs**. A [Seestar S50](https://www.seestar.com/)
is used to **orient** them.

**[See a real report](https://devonjones.github.io/terminus/example-horizon.html)** —
the author's backyard, solved to true north at rms 0.31°: the photographed sky
as a polar disc, the horizon ring, the telescope's own columns with their
residuals, and one column excluded with its reason on the page. Self-contained
HTML, exactly as `terminus polar` wrote it.

> Companion to [uranometria](https://github.com/devonjones/uranometria) (star
> charts and annotated astrophotos). terminus is the horizon; uranometria is the
> sky above it.

## How it works

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
chosen by information gain and the run stops when the orientation stops moving —
typically **seven or eight columns, about twenty minutes**, against ~95 minutes
for a blind 36-column circle. How many you need depends on your horizon, and
not in the direction you would expect: yaw is only constrained where the
horizon has *gradient*, so cliffs, tall trees and sharp rooflines carry the
information and **an even, open horizon is the hard case**, needing more
columns rather than fewer.

**Two capture modes, never mixed in one solve:** individual photos (preferred —
exact pinhole geometry, registered with Hugin), or one already-stitched phone
panorama (fallback — the phone's projection is unknown and it cannot see above
its own frame).

## What you get

- A durable, hand-editable **mask** (`horizon_mask.yaml`) with per-azimuth
  altitude and obstruction type. A column can carry `exclude: "<reason>"` to
  keep it out of the orientation fit — for a measurement you know is bad
  (dawn-contaminated, glare) — and the fit records the exclusion and its
  reason rather than silently omitting the column. The reason is required:
  a bare `exclude: true` is refused.
- **`horizon.hrz`** for N.I.N.A. (Options → General → Horizon) and
  **`horizon.stellarium.txt`** for a Stellarium polygonal landscape, plus
  Sky Safari and PVsyst formats (see **Exports**, below).
- A per-azimuth **review frame** so you can eyeball every measurement.
- A **`Horizon`** class for planners: `altitude_at(az)`, `is_above(az, alt)`,
  `is_visible(ra, dec, sky)`.

Azimuth is true-north, increasing toward east, everywhere.

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

# system tools: ffmpeg for the scope's RTSP preview, hugin-tools for `mosaic`
sudo apt install ffmpeg hugin-tools        # macOS: brew install ffmpeg hugin

# optional, the segmentation backend for `skymask`/`mosaic --segment`;
# without it the colour heuristic stands in, which is markedly worse.
# The extra is pinned to CPU wheels (~200 MB) on Linux/Windows; plain
# `pip install torch` would pull the CUDA build, which is gigabytes.
uv sync --extra segment    # pip users: pip install -e '.[segment]' --extra-index-url
                           #   https://download.pytorch.org/whl/cpu

cp config.example.toml config.toml   # edit scope host + pem path
```

Only the commands that talk to the scope need `config.toml`: `terminus mosaic`,
`skymask`, `export`, `polar`, and `orient` with `--fiducials` or `--replay` all
run with no telescope, no network and no config. Importing terminus never pulls
in torch or transformers and never shells out to Hugin; `skymask.available()`
and `mosaic.hugin_available()` report what you have.

## Quickstart: photograph to planning file

Six steps. The numbers quoted are from a real seven-column run at the example
site, to shape-check yours against. (The example report linked above is a
different fit of the same yard — 8 of 9 columns, rms 0.31° — so its numbers
differ:
[residuals aren't comparable across fits](docs/field-notes.md#why-orient-stops-on-yaw-stability-and-not-on-the-residual).)

**1. Photograph the horizon.** Individual overlapping frames, held roughly
level, all the way round. Nineteen shots two seconds apart worked; seventeen to
eighteen of them register, the exact count varying with what `cpfind` finds on
the day.

> Frames, **not** your phone's panorama mode. A finished panorama cannot be
> matched to another finished panorama, and `mosaic` will spend twenty minutes
> discovering that before it tells you.

**2. Register them into one equirectangular canvas, and segment while you are
there** (~4 minutes):

```bash
terminus mosaic ~/horizon-frames --out pano --segment
```

`--segment` runs SegFormer on each **frame** and warps the labels through the
same solve, rather than segmenting the stitched result. That matters more than
it sounds: segmenting the finished canvas labels 44% of it — the black region
nobody photographed — as walls, grass and hills, and 3% of it as *sky*.

**3. Read a horizon off the panorama** (~1 minute):

```bash
terminus skymask pano.png --coverage pano.coverage.npy --classes pano.classes.npy \
  --out photo_mask.yaml
```

Out comes a mask with a per-column altitude, an obstruction type, and an
uncertainty — about 1° for structure, 3° for foliage. It is **unoriented**: the
azimuth is the panorama's own, and it refuses to export until that is solved.

**4. Solve where that horizon sits on the sky.** This is the step that needs
the telescope — though not necessarily a telescope tonight (see below).

```bash
terminus orient photo_mask.yaml --out oriented.yaml
```

It measures a few evenly spaced columns, then chooses each next column by how
much it will shrink the uncertainty in the fit, refits after every one, and
stops when the solved yaw stops moving. It stops on the **stability of the
yaw, not on the residual** — a small fit can read rms ≈ 0 however wrong it is
([why](docs/field-notes.md#why-orient-stops-on-yaw-stability-and-not-on-the-residual)).
If the yaw has not settled by the column budget, the run says so and marks the
result provisional rather than letting it read as converged.

Roughly **2.5 minutes per column**, so eight chosen columns is about 20
minutes. Every attempt is checkpointed as it completes — one append-only line
in `<out>_fiducials.jsonl`, conditions included. Kill the run and run it
again: cached columns are served without re-observation, and saved night
profiles are re-judged by the *current* detector, so a detector fix improves
an old night for free. A cached verdict whose *conditions* you no longer
trust — a column measured through cloud, glare, or a lingering twilight
arch — can be refused with `--re-measure 324,177`: the old lines stay in the
append-only checkpoint, the columns are measured again, and the fresh result
supersedes.

Two rules for when to run it:

- **Stay on one side of the twilight boundary.** `orient` picks its
  measurement channel (day scenery stream vs night star-mode imaging) once at
  run start, from the Sun's altitude. A run that straddles dusk records
  false measurements
  ([the 164° story](docs/field-notes.md#the-dusk-straddle-that-moved-a-yaw-by-164)).
  Heading into dawn, `--stop-above-sun-alt -18` stops cleanly before any
  column once the Sun passes astronomical twilight.
- **Plate solve and correct the mount first.** An unverified polar alignment
  biases every column by the same amount and the fit absorbs it silently
  ([measured](docs/field-notes.md#plate-solve-first-a-37-mount-drift-moved-the-solution-56)).

**5. Export it:**

```bash
terminus export oriented.yaml --skysafari --landscape --pvsyst
```

**6. Look at it:**

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
still works and draws the measured horizon on a plain disc.

**The page is also the bug report.** If the run went wrong, `polar` picks up the
checkpoint and the scan frames lying beside the mask and puts the whole run on
the page: whether the yaw actually settled, every attempt including the ones
that produced nothing, the conditions each was measured under (channel, Sun
altitude, sky reference, clock, duration), the yaw across refits, and the scan
frames themselves with the chosen edge outlined.

That last part is the one that matters most, and the numbers are why. A storm
bank sat *above* the treeline here at sunset, so the scan walked down and
stopped at the cloud's lower edge: two columns read 57.5° and 53.8° where the
photograph puts the treeline at 46°. Every diagnostic available at the time was
healthy — those columns carry the two **highest** open-sky references of the
whole run, because bright sky above bright cloud is still bright sky. The same
direction read 46.2° on the night channel hours later. No statistic caught it;
the frames catch it on sight, as a black bank at 50° under blue sky at 60°.

```bash
terminus polar oriented.yaml                 # everything: site, clock, frames
terminus polar oriented.yaml --privacy       # no site, no dates, no clock times
terminus polar oriented.yaml --no-frames     # smaller page, no visual evidence
```

`--privacy` redacts *who and where*, never *what happened*: altitudes, verdicts,
sky references, Sun altitudes and the frames all still ride along, because a
report nobody can debug is not worth sending. It does not anonymise the
photographs — the panorama is a picture of your horizon and that is the point of
the page.

What each step reported on that run:

| Step | Expect |
|---|---|
| `mosaic` | `registered 17 frames, dropped 2` — the dropped ones have 0 control points |
| `mosaic --segment` | `55% of pixels labelled` (the rest is sky nobody photographed) |
| `skymask` | `360 columns, 0 clipped`, and a refusal to export: the mask is **unoriented** |
| `orient` | `yaw settled within 1 deg over 3 refits`, seven columns |
| `polar` | one self-contained HTML file: ~460 KB with `--no-frames`, roughly double once the scan frames ride along |

### Without a telescope tonight

Step 4 is the only one that needs the scope, and it will take measurements
from elsewhere:

```bash
# columns a sweep already measured — yours from an earlier night, or someone
# else's for the same spot; repeatable, merged first-wins
terminus orient photo_mask.yaml \
  --fiducials sweep_east.yaml --fiducials sweep_west.yaml \
  --out oriented.yaml

# re-run a night you already measured, no hardware at all: every run writes
# <mask>_profiles.json beside its mask
terminus orient photo_mask.yaml --replay run_profiles.json
```

The planner still chooses the order by information gain — it is confined to
azimuths a fiducial exists for, not handed a fixed list — so this also
exercises the loop itself without spending a clear night on it.

`terminus export --allow-unoriented` also exists and is only correct if you
know the azimuths are already true — if you set north by hand, say. Otherwise
the exported horizon is rotated by an unknown amount, which a planner cannot
detect.

## Exports

```bash
terminus export horizon_mask.yaml                        # .hrz + .txt
terminus export horizon_mask.yaml --skysafari --landscape --pvsyst
# ...and with the panorama itself as the texture, gaps shown as sky
terminus export horizon_mask.yaml --landscape \
    --texture pano.png --coverage pano.coverage.npy
```

The `.hrz` is drop-in where [HRZ-Creator](https://neuronburner.com/hrz-creator/)
or [panorama-horizon-maker](https://github.com/danngalann/panorama-horizon-maker)
output went: same space-separated `az alt` pairs, ascending from 0 and closed at
360. terminus adds `#` comment lines, which N.I.N.A. accepts and they never
wrote. Load under N.I.N.A. Options → General → Horizon.

`--skysafari` writes a 2048x1024 RGBA PNG in Sky Safari's panorama convention —
north at the left edge, zenith at the top, and **alpha as the horizon**, since
that is what the app actually reads. Load it under *Settings -> Horizon & Sky ->
Show Horizon & Sky as Panoramic Image*.

`--landscape` writes a Stellarium landscape directory: `landscape.ini`,
`horizon.txt` and, with `--texture`, `maptex.png`. Without imagery it is
`type=polygonal`, because declaring a photograph that does not exist would be a
lie; with imagery it is `type=spherical` and ships the measured polygon
alongside the picture so the numbers and the image agree. Copy the directory
into Stellarium's `landscapes/` folder. Where the panorama has no coverage the
pixel is transparent, so the program draws sky: a gap in the survey is not
black ground.

`--pvsyst` writes a `.HOR` horizon profile. PVsyst's import dialog asks for the
rotation direction and the north azimuth angle, because its convention is not
ours — set **Clockwise** and **0**, which the file's own header repeats. Guessing
gives a mirrored horizon that still looks plausible.

Review the `*_frames/` images, correct any misjudged rows in the mask by hand,
then `terminus export` again.

## The scope-only fallback

Without photographs, `terminus sweep` runs a blind circle: brightness finds the
horizon, obstruction type is guessed from colour in a blurred frame — treat
that type column as a hint, not a measurement.

```bash
terminus preflight                 # connect, show Sun + which azimuths it blocks
terminus point 75 45               # slew to az 75 / alt 45 (Sun-guarded), verify
terminus classify                  # sky / vegetation / structure at current pointing
terminus sweep                     # full sweep -> horizon_mask.yaml + .hrz + .txt
```

Run it **on a clouded-out night** — a full circle takes hours, and overcast
under suburban light pollution is actually the *easier* case, because the sweep
finds the horizon by brightness and cloud makes the sky a bright, even
backdrop. At the shipped `az_step = 5` a full circle is 72 columns
(`az_step = 10` halves it, more coarsely). Let the Sun set first: the Sun
guard refuses any slew whose path passes near it, so daytime columns toward
the Sun are skipped rather than measured. When the Sun crosses −12° mid-run,
`sweep` switches to the night channel on its own, so a dusk-into-dark circle
is the supported case. Under a strong light dome, measure the directions
facing *away* from town during twilight and leave the bright ones for later
([why](docs/field-notes.md#cloud-is-an-asset-and-under-a-light-dome-the-order-of-azimuths-matters)).

Know before you start: an interrupted `sweep` loses everything it measured —
the mask is written once, at the end. (`orient` checkpoints per column;
`sweep` does not yet.)

## Practical cautions

The measured horizon is only as good as the run that produced it. Beyond the
two rules in Quickstart step 4 (plate solve first; stay on one side of the
twilight boundary), each of these links to the incident that earned it:

- **The Sun is checked for where it is now, not where it will be.** A column
  clear when chosen may not be by the time it is measured; at dusk this errs
  safe, at dawn it does not
  ([what still bites](docs/field-notes.md#what-still-bites)).
- **The horizon is specific to where the tripod stood.** Re-measure if you
  move — above all if you move *nearer* an obstruction, which costs about
  twice what moving away gains: 2 m closer to a 2 m fence 5 m away is
  **+11.9°**, 2 m further is −5.9°
  ([the geometry](docs/field-notes.md#moving-the-tripod-closer-costs-more-than-further-gains)).

## As a library

```python
from terminus import Horizon, Sky
h = Horizon.from_mask("horizon_mask.yaml")
sky = Sky(31.9583, -111.5967, 2096)   # lat, lon, elevation m (example: Kitt Peak)
h.is_visible(ra_hours=20.2, dec_deg=38.4, sky=sky)   # NGC 6888 clear right now?
h.altitude_at(120)                                    # horizon altitude at az 120

from terminus import mosaic, skymask, orient, plan
```

## Safety

terminus recomputes the Sun's position for the exact current time and refuses any
pointing within `sun_cone_deg`. Every slew is broken into small steps, each
re-checked, so the tube cannot arc through the Sun between waypoints; a two-hop
route re-checks clearance after the first leg lands, since a long slew can take
minutes. Even so: **run it with the scope in shade or well away from the Sun's
part of the sky**, and keep a solar-safe cap/dew shield on. Pointing a small
refractor at the Sun can destroy the sensor. Use at your own risk.

## Further reading

- **[docs/field-notes.md](docs/field-notes.md)** — what running this against
  real sky taught us, with the numbers: the run that found four bugs 211
  passing tests had not, the 164° dusk straddle, why RMS lies.
- **[docs/PANORAMA-PIPELINE.md](docs/PANORAMA-PIPELINE.md)** — how the
  published example was actually built, stage by stage, with the traps in the
  order they will hit you.
- **[docs/prior-art.md](docs/prior-art.md)** — the survey of who has done
  skyline-to-obstruction-profile before, and what is different here. If you
  know of prior work, please open an issue.
- **[LESSONS.md](LESSONS.md)** — the full decisions-and-lessons log, developer-facing.

## Credits

Protocol groundwork from [seestar_alp](https://github.com/smart-underworld/seestar_alp);
key extraction from [seestar-tool](https://github.com/bguthro/seestar-tool).
Landscape-format details from Georg Zotti's Stellarium tutorial, above.
Apache-2.0.
