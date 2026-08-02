# terminus

*terminus* (Latin: boundary, limit) measures the **local horizon** at your
observing site with a [Seestar S50](https://www.seestar.com/) — the real skyline
of rooftops, fences, and trees that decides what you can actually image — and
exports it for planning tools like **N.I.N.A.** and **Stellarium**, or queries it
directly as a library so a scheduler can ask *"is this target clear of my horizon
right now?"*

It works by pointing the scope around the sky and, at each azimuth, finding the
altitude where clear sky meets an obstruction — classifying that obstruction as
**tree** (green/yellow, and therefore *seasonal*) or **structure** (permanent) by
color. The result is a horizon map tied to true azimuth and altitude.

> Companion to [uranometria](https://github.com/devonjones/uranometria) (star
> charts and annotated astrophotos). terminus is the horizon; uranometria is the
> sky above it.

## What you get

- A durable, hand-editable **mask** (`horizon_mask.yaml`) with per-azimuth
  altitude and obstruction type.
- **`horizon.hrz`** for N.I.N.A. (Options → General → Horizon) and
  **`horizon.stellarium.txt`** for a Stellarium polygonal landscape.
- A per-azimuth **review frame** so you can eyeball every measurement.
- A **`Horizon`** class for planners: `altitude_at(az)`, `is_above(az, alt)`,
  `is_visible(ra, dec, sky)`.

## Requirements

- A Seestar S50 on your network, in **EQ mode on a polar-aligned mount**
  (terminus points by RA/Dec goto; in EQ mode the scope's alt/az readout is not
  trustworthy but its RA/Dec pointing is).
- Python ≥ 3.11 and **ffmpeg** on `PATH` (the scenery preview arrives over RTSP).
- The **interop key** (see below).

Runs headless — a Raspberry Pi is a fine host, same as
[seestar_alp](https://github.com/smart-underworld/seestar_alp). All dependencies
(numpy, pillow, cryptography, astropy) ship ARM wheels; `apt install ffmpeg`.

## The interop key

Firmware 7.18+ requires a signed challenge/response before it will accept
commands. The signing key is an RSA private key ZWO ships **in the clear** inside
the Android app. terminus does **not** include it; you extract it once from the
app you already own:

- Use [`bguthro/seestar-tool`](https://github.com/bguthro/seestar-tool)
  ("Extract PEM"), or unzip the APK and pull the `-----BEGIN PRIVATE KEY-----`
  block out of `lib/arm64-v8a/libopenssllib.so`.
- Save it (e.g. `~/.seestar/seestar_client_key.pem`, mode 600) and point
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
re-checked, so the tube cannot arc through the Sun between waypoints. Even so:
**run it with the scope in shade or well away from the Sun's part of the sky**,
and keep a solar-safe cap/dew shield on. Pointing a small refractor at the Sun
can destroy the sensor. Use at your own risk.

## How it decides

- **sky**: blue-dominant and bright (survives auto-exposure).
- **tree**: green/yellow — flagged *seasonal*, since a canopy that blocks in
  summer is bare branches in winter.
- **structure**: any other non-sky (roof, wall, fence).
- A conservative `clear_thresh` treats dappled canopy as blocked, so the recorded
  horizon is the *top* of the foliage — a star behind leaves is gone anyway.

Azimuth is true-north, increasing toward east; the mask is accurate to your polar
alignment (~1° on a repeatable setup) and can be refined with a night plate-solve.

## Credits

Protocol groundwork from [seestar_alp](https://github.com/smart-underworld/seestar_alp);
key-extraction method from [seestar-tool](https://github.com/bguthro/seestar-tool).
Apache-2.0.
