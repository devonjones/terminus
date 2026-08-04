# terminus

*terminus* (Latin: boundary, limit) measures the **local horizon** at your
observing site — the real skyline of rooftops, fences, and trees that decides
what you can actually image — and exports it for planning tools like **N.I.N.A.**
and **Stellarium**, or queries it directly as a library so a scheduler can ask
*"is this target clear of my horizon right now?"*

The horizon comes from **photographs**. A [Seestar S50](https://www.seestar.com/)
is used to **orient** them.

> Companion to [uranometria](https://github.com/devonjones/uranometria) (star
> charts and annotated astrophotos). terminus is the horizon; uranometria is the
> sky above it.

## The method, and why it is split this way

A telescope **focused at infinity** cannot see your neighbour's roof. The Seestar
is 250 mm at f/5, putting its hyperfocal distance around 4.3 km, so every
terrestrial target is roughly a thousand times inside it and arrives badly
blurred. That is the state the scope is in for astronomy, and the state the
sweep runs in — which is why the type column it produces is not trustworthy: it
is colour guessed from a blur.

> **This is a limit of the focus position, not of the instrument.** The Seestar
> *can* autofocus in scenery mode, sharply enough to resolve individual grains in
> roof shingles. That reopens obstruction type — and distance — as things the
> telescope could measure directly, and makes the two sources cross-checkable
> rather than one substituting for the other. Design is tracked as `terminus-32`;
> nothing below depends on it yet.

A phone photograph, meanwhile, is in focus by default and samples the skyline
roughly two orders of magnitude more finely than a column-by-column sweep. So
for now the two instruments are given the jobs they are currently good at:

- **The photographs are the mask.** A registered panorama gives a dense skyline
  and, through semantic segmentation, what each obstruction *is*.
- **The telescope is the calibration.** It supplies a handful of accurately
  known altitudes, which fix the three parameters — yaw, pitch, tilt — that say
  where the photo sphere sits on the real sky.

That reduces the scope's job from measuring 360 columns to pinning three numbers.
At this site the orientation stopped moving after **seven** columns chosen by
information gain — about twenty minutes — where a blind 36-column circle takes an
evening and answers a question the photographs already answered better.

**Two capture modes, never mixed in one solve:** individual photos (preferred —
exact pinhole geometry, registered with Hugin), or one already-stitched phone
panorama (fallback — the phone's own projection is unknown and it cannot see
above its own frame).

## Status: what ships today

Be aware of this before planning a session.

| | State |
|---|---|
| Scope sweep, mask, exports, `Horizon` queries | **Working, on the CLI** |
| Photo registration, segmentation, orientation fit, adaptive column choice, night detection | **Implemented and tested, but not yet on the CLI** |

The photo-first modules (`skymask`, `mosaic`, `orient`, `plan`, `night`) are in
the package and covered by the test suite, but they are not exported from the
top-level API and no subcommand reaches them; they are importable only by full
module path. The published result was produced by driving them from scripts.
Wiring them into the API and CLI is tracked as `terminus-18`, `-19`, `-20`.

Until that lands, `terminus sweep` runs the **scope-only** method: a blind
circle, with obstruction type guessed from colour. It works, and it is what the
commands below do — but it is the weaker method, and its type column is not
trustworthy for the reason given above.

## What you get

- A durable, hand-editable **mask** (`horizon_mask.yaml`) with per-azimuth
  altitude and obstruction type.
- **`horizon.hrz`** for N.I.N.A. (Options → General → Horizon) and
  **`horizon.stellarium.txt`** for a Stellarium polygonal landscape.
- A per-azimuth **review frame** so you can eyeball every measurement.
- A **`Horizon`** class for planners: `altitude_at(az)`, `is_above(az, alt)`,
  `is_visible(ra, dec, sky)`.

Anything classified as vegetation is exported with a **3° safety margin** added.
A tree has gaps low down with more canopy above them, it moves in wind, and a
crown measured in August is not the horizon you get in January — so the measured
edge is not treated as a promise. The margin is applied at a single choke point
that every exporter routes through, so it cannot be bypassed.

## Requirements

**Core** (everything on the CLI today):

- A Seestar S50 on your network, in **EQ mode on a polar-aligned mount**
  (terminus points by RA/Dec goto; in EQ mode the scope's alt/az readout is not
  trustworthy but its RA/Dec pointing is).
- Python ≥ 3.11 and **ffmpeg** on `PATH` (the scenery preview arrives over RTSP).
- The **interop key** (see below).

**Optional**, for the photo pipeline:

- **hugin-tools** for registration — `pto_gen`, `cpfind`, `cpclean`,
  `autooptimiser`, `nona`, `pano_modify`. Without them, photo registration is
  unavailable; the scope path is unaffected. No blender (`enblend`) is needed:
  `nona` renders separate `TIFF_m` layers and terminus composites them itself,
  solving per-frame gains first, because a full circle under sun guarantees
  frames metered differently and averaging them raw darkens every overlap that
  includes a dim one.
- **torch**, **torchvision**, **transformers** for semantic segmentation
  (SegFormer, ADE20K). Without them `skymask` falls back to a colour heuristic,
  which is markedly worse — it is what mistook an off-white house wall for sky
  here. Roughly 15 MB of model download, ~2 s per frame on CPU.

Runs headless — a Raspberry Pi is a fine host, same as
[seestar_alp](https://github.com/smart-underworld/seestar_alp). The core
dependencies (numpy, pillow, cryptography, astropy, pyyaml) ship ARM wheels;
`apt install ffmpeg`. The optional segmentation stack is heavier and may not be
practical on a Pi.

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

```bash
terminus preflight                 # connect, show Sun + which azimuths it blocks
terminus point 75 45               # slew to az 75 / alt 45 (Sun-guarded), verify
terminus classify                  # sky / vegetation / structure at current pointing
terminus sweep                     # full sweep -> horizon_mask.yaml + .hrz + .txt
terminus export horizon_mask.yaml  # re-export a mask without re-sweeping
```

Review the `*_frames/` images, correct any misjudged rows in the mask by hand,
then `terminus export` again.

### Plate solve first

**Do not trust a mask taken on an unverified polar alignment.** This is not a
theoretical caution. A drift of 3.7° accumulated here over a few days without
plate solving, and every fiducial measured in that state was wrong by that much;
correcting it moved the solved orientation by **5.56°** (130.25° → 135.81°) —
far more than the 1–2° a single-column sensitivity test had suggested was
reaching the data.

Plate solve, correct the mount, and only then measure the columns you intend to
rely on. A sweep is only ever as good as the pointing underneath it.

### When to run it

**On a clouded-out night.** A full circle takes hours and returns nothing you
could have imaged instead, so it costs a night you were going to lose anyway.

Cloud is not merely tolerable here, it helps. The sweep finds the horizon by
brightness: sky is the light source and everything terrestrial silhouettes
against it. An overcast sky under suburban light pollution is a bright, even
backdrop, which is close to ideal. A clear moonless sky is the *harder* case —
darker overhead means less contrast against the treeline, not more.

Two things do matter:

- **Let the Sun set first.** The Sun guard refuses any slew whose path passes
  near it, so daytime columns toward the Sun are skipped rather than measured.
- **Expect the blind circle to take the evening.** The shipped default is
  `az_step = 5`, so a full circle is **72 columns**, plus adaptive refinement
  where the horizon moves fastest — several hours. Set `az_step = 10` for a
  quicker, coarser 36.
  The open-sky reference is re-measured as it goes, so a run may safely span
  twilight into full dark. (The photo-first method needs far fewer columns — see
  above — but is not yet on the CLI.)

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
route re-checks clearance after the first leg lands, against a freshly computed
Sun, because a long slew can take minutes. Even so: **run it with the scope in
shade or well away from the Sun's part of the sky**, and keep a solar-safe
cap/dew shield on. Pointing a small refractor at the Sun can destroy the sensor.
Use at your own risk.

## How it decides

**From photographs** (the intended path): semantic segmentation with SegFormer
on ADE20K decides sky versus terrain, and the class just below the skyline gives
the obstruction type. A colour heuristic is used when the segmentation stack is
absent, and is a genuine downgrade — colour rules classified an off-white house
wall as sky here, and a dark branch silhouetted against bright sky still reads
blue. The skyline is required to be a *sustained* run of non-sky rows, so the
trace cannot thread through lattice gaps or a gappy canopy.

**From the scope** (the fallback path, and what `terminus sweep` does today):
brightness alone, at infinity focus. Sky is the light source; terrain
silhouettes against it, and a conservative threshold treats dappled canopy as
blocked, so the recorded horizon is the *top* of the foliage. Type is guessed
from colour here and should be treated as a hint, not a measurement — see the
note on autofocus above. After dark this two-level model breaks down —
skyglow rises roughly threefold toward the horizon and streetlights inside the
terrain outshine the sky — so the night path fits the skyglow gradient, masks the
lamps, and looks for where a column departs the model.

Azimuth is true-north, increasing toward east.

## Prior art, and what is actually new

Photographing a skyline to get an obstruction profile is **not** a new idea, and
this project is better served by saying so plainly.

- **Georg Zotti's Stellarium landscape tutorial** (SEAC2025) documents the exact
  Hugin sequence terminus automates, and is the authoritative reference for
  `polygonal_horizon_list`, `angle_rotatez` and `maptex_top`/`bottom`.
  [Course notes](https://www.archeoastronomy.org/assets/downloads/slides/ast/ast-12_seac2025-stellarium-landscape-course-notes.pdf)
- **Existing `.hrz` generators**: [HRZ-Creator](https://neuronburner.com/hrz-creator/)
  and [panorama-horizon-maker](https://github.com/danngalann/panorama-horizon-maker).
  Both trace the skyline manually and set north by hand.
- **The N.I.N.A. Horizon Creator plugin** workflow — phone frames at horizon
  inflections, angles from a phone alt/az app, skyline read by eye.
- **The solar industry got there first**, twenty years ago and commercially. The
  [Solmetric SunEye 210](https://www.solmetric.com/product/suneye-210-shade-tool/)
  is a calibrated fisheye with compass, tilt sensor and GPS producing horizon
  altitude for every degree of azimuth, exported as `.HOR` for PVsyst. Also
  HORIcatcher/Meteonorm and Solar Pathfinder. Photo-to-horizon-profile is a
  solved, shipped product — in a different field.
- **Nearest ecosystem neighbours**:
  [clear-horizons](https://github.com/njefferson/clear-horizons), which already
  solves north from the Sun, and
  [seestar-mcp](https://github.com/OrangeAgente/seestar-mcp), which infers
  obstruction arcs from where plate solving fails.
- **Closest published work**: Mephisto unobservable-region segmentation and
  LenghuSky-8 — all-sky cameras with star-based alt/az calibration, where the
  static obstructions are a *hand-drawn* mask rather than a segmentation output.

Against that, the combination this project has not found elsewhere is narrower
than "photograph your horizon":

- semantic segmentation as the sky/terrain decision for an astronomy horizon
  export, with obstruction **type** carried from segmentation classes into the
  exported product;
- a telescope used as a source of angular fiducials for a three-parameter
  orientation solve of an independently built photogrammetric sphere;
- **information-gain (D-optimal) choice of which column to measure next**, with
  the stopping rule on parameter *stability* rather than residual RMS;
- ceiling-limited columns treated as one-sided censored bounds rather than
  discarded or scored as equalities;
- a night detector built *on* the skyglow altitude gradient rather than
  subtracting it.

That search was not exhaustive: Cloudy Nights and Stargazers Lounge refuse
automated fetches, so the amateur-forum record was only sampled, and the N.I.N.A.
Discord, ZWO bbs, indilib forum and Seestar Facebook groups were not searched at
all. If you know of prior work here, please open an issue — a correction is more
useful than a claim.

## Credits

Protocol groundwork from [seestar_alp](https://github.com/smart-underworld/seestar_alp);
key-extraction method from [seestar-tool](https://github.com/bguthro/seestar-tool).
Landscape-format details from Georg Zotti's Stellarium tutorial, above.
Apache-2.0.
