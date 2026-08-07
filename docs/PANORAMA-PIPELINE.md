# How the panorama and horizon map were actually built

**Purpose.** Reproduce the published Mode A result: a photo mosaic of the site, registered
to true azimuth/altitude against telescope fiducials, solving to **yaw 135.81°, pitch +1.94°,
tilt 2.81° toward 180°, residual 0.69° RMS on 16 fiducials.**

**Provenance of this document.** Reconstructed on 2026-08-06 from the git history, `terminus-4`,
the published report, and the input files on disk. The original run happened in a different
session on 2026-08-04 and its scratchpad is gone. Every number below is quoted from a durable
source (a bead, a commit, or a file that still exists), not from recollection. Where something
is *not* recoverable, it says so.

---

## 1. The single most important fact

**The source frames are not in git, and the files that look like them are the wrong ones.**

| What you want | Where it is |
|---|---|
| **Mode A: 19 individual frames** — what the published fit used | `captures/panoramas/2026-08-03-frames/` on this machine; originals at `/mnt/d/Dropbox (Personal)/Camera Uploads/2026-08-03 15.33.22.jpg` … `15.34.36.jpg` |
| Mode B: stitched phone panoramas — a *different, worse* mode | `captures/panoramas/2026-08-03/14.1*.jpg` |

"Not in git" is the precise claim: `captures/` is in `.gitignore`, so a fresh
clone has none of this and a stranger must supply their own photographs. On the
machine this was built on, the 19 Mode A frames are already staged at
`captures/panoramas/2026-08-03-frames/`, and **that** is the directory to point
`terminus mosaic` at. The Dropbox path is where they came from, not where to read
them.

`captures/panoramas/2026-08-03/` holds **five** files: four stitched panoramas and
`14.12.15.jpg`, which is an **indoor workshop photo**, not a panorama at all (`terminus-4`
recorded this correction explicitly after an earlier note wrongly called it a fifth panorama).

If you point the pipeline at `captures/panoramas/` you are running **Mode B**, which is a
documented fallback that scores far worse — 2.24° RMS against Mode A's 0.69°, and it answers
only 76% of azimuths against 100%. It is not a smaller version of the same thing.

The 19 Mode A frames: Pixel 9 Pro Fold, 3000×4000 **portrait**, EXIF
`FocalLengthIn35mmFilm = 24` (actual 4.53 mm), timestamps 15:33:22–15:34:36.

---

## 2. Which code state to use

The published fit was computed on **2026-08-04 at ~15:42**. That falls between two commits:

| Commit | When | What |
|---|---|---|
| **`4866544`** | 08-04 09:32 | PR #1 — `mosaic.py`, `skymask.py`, `orient.py`, `plan.py`, `night.py` all first land. **This is the state that produced the published numbers.** |
| `78d2670` | 08-04 15:58 | PR #6 — the `mosaic` / `skymask` CLI subcommands land, *after* the fit |
| `745c4bc` | 08-04 18:33 | PR #9 — obstruction type as fit weight; `Fiducial` gains `sigma` |
| `c660ad7` | 08-05 17:12 | PR #21 — terminus-48 / terminus-14 changes |

**So the published result was never produced by a CLI.** At `4866544` the CLI had only
`preflight`, `point`, `classify`, `sweep`, `export`. The mosaic ran through the library API
plus scratchpad scripts. If you are building the CLI path, you are building something that did
not exist when this worked — which is fine, but it means a CLI mismatch is not evidence you
have the pipeline wrong.

### What has drifted since `4866544`

Measured against `main`, not against whatever happens to be in your working tree:

```
src/terminus/orient.py   +122   Fiducial gains sigma/photo_type/scope_type; objective() extracted
src/terminus/plan.py     +112   feasibility filtering
src/terminus/skymask.py  +196   _tiles(), segment_classes(); porosity -> gap_fraction rename
```

`mosaic.py` has **not** drifted. Per-frame label transport — `source_images`,
`remap_labels`, `combine_labels` — arrives with PR #23 and is not on `main` yet,
so check which branch you are on before looking for those names.

Two of the above will bite you directly:

- **`porosity` was renamed to `gap_fraction`** in `8c8dd51`. At `4866544` the parameter is
  `type_uncertainty(classes, porosity, ...)`; on HEAD it is `gap_fraction`. Same quantity.
- **`Fiducial.__init__` changed shape.** At `4866544` it is
  `Fiducial(az, alt, ceiling=None, bound=False, weight=1.0)`. On HEAD it also takes `sigma`,
  `photo_type`, `scope_type`, and `weight`/`sigma` are deliberately *separate* — see `LESSONS.md`
  F-23, because multiplying a Huber loss by 1/σ² is not inverse-variance weighting.

Two defects that were open in this area are **fixed** on HEAD, but not in the same
place, and the difference matters if you go looking:

- `terminus-48` — `from_mask` matched type strings no writer produced. Fixed in
  `c660ad7`, which is after the `4866544` baseline, so it *does* show up in a diff
  against it. It now reads an explicit `bound:` field.
- `terminus-14` — the find_edge noise estimator. Already fixed **at** `4866544`:
  the RMS second-difference estimator and the `EDGE_MIN_STEP_FRAC` floor are both
  in that commit's `sweep.py`. `c660ad7` added only the regression test. Diffing
  against the baseline will show nothing, because there is nothing to show.

The beads still read OPEN for both, which is bead housekeeping rather than code
state; the code is the authority. If HEAD misbehaves, diff against `4866544`
before assuming the pipeline is wrong.

---

## 3. The pipeline

### Stage 1 — register the frames (Hugin)

`mosaic.solve(image_dir, work_dir, lens=None, min_points=12, celeste=True)`

```
pto_gen  -o project.pto <frames>
cpfind   --multirow --celeste -o cp.pto project.pto
cpclean  -o clean.pto cp.pto
        -> count control points per image
        -> drop any frame below min_points, ITERATE (up to 6 passes)
autooptimiser -a -l -s -o solved.pto clean.pto
```

**`--celeste` masks cloud before feature detection.** These skies are full of moving cloud,
which generates spurious features.

**Dropping is iterative and must be.** Removing a frame changes connectivity, which changes what
`cpclean` keeps, which exposes more weak frames. On the real data: 19 → 16 frames, and the
solver residual went **10.35 → 2.51 units on *more* control points (350 → 422)**. The bad frame
was degrading the entire solve, not just its own patch.

The three dropped frames, by name, so you can confirm you reproduced it:

| Frame | Control points |
|---|---|
| `15.33.52` | **0** — `autooptimiser` placed it anyway, at its default orientation |
| `15.33.26` | 1 |
| `15.33.30` | 4 |

That zero-control-point frame is the one that put a garage umbrella in the middle of the sky and
cost six rounds of blaming the sky classifier (`LESSONS.md` F-02, M-21). **Log every drop** — a
silently discarded frame is silently missing sky.

**Expected output:** 16 frames, ~422 control points, solver residual ≈ **2.51 px ≈ 0.09°**.

### Stage 2 — render to equirectangular

`mosaic.render(pto, work_dir, width=2880, height=1440)` → `pano_modify --projection=2` + `nona`.

Equirectangular is the point: **x is azimuth, y is altitude**, natively. That is the frame the
horizon mask wants.

Two rules encoded here:
- **Per-frame exposure is solved before compositing** (gains spanning 0.74–1.11 on this set).
  Shooting toward and away from the Sun makes this unavoidable; without it, overlaps darken
  wherever a dim frame contributes.
- **Seams are not blended.** A visible seam is how a person checks the fit. `enblend` is
  deliberately not used and is not a dependency.

### Stage 3 — classify sky vs terrain

`skymask.sky_mask(image, backend="auto")` → SegFormer `nvidia/segformer-b0-finetuned-ade-512-512`
on ADE20K, tiled.

Use **segmentation**, not the colour heuristic. Three failures survived every attempt at
heuristic tuning and each fix traded against the others — the full account is `LESSONS.md` M-20
and D-02. Summary: no single photometric primitive separates sky from terrain. Blue-dominance
fails on backlit branches (a frame visibly 20% branches scored sky = 0.96), brightness fails on
shadowed cloud, hue fails on off-white siding, and local variance fails outright here — sky
reaches a 90th-percentile local deviation of **55.8** against structure's median of **23.5**,
because heavy cloud is more textured than siding.

The heuristic is kept as a Raspberry Pi fallback (no torch) and scores comparably, but it needs
hand tuning and segmentation does not. Segmentation independently recovers the same yaw
(136.50°) as the colour method, which is the real argument for it.

**Check `available('segment')` actually means something** — it needs torch, transformers **and
torchvision**; the torchvision check was missing and `sky_mask()` died on hosts with two of the
three (`terminus-34`).

### Stage 4 — extract the skyline

`skymask.horizon_band(sky, valid=None, run=6)` → per-column rows plus gap fraction.

**Stop at the FIRST SUSTAINED run of non-sky.** Any rule that keeps descending — last-sky-row,
deepest-sky-pixel — traces the *fence bottom* instead of the treeline, because a lattice fence
and a tree canopy both show sky through their gaps. `run=6` px at 1/8 scale tolerates a single
branch crossing an open column.

Columns where the obstruction runs past the top of frame are flagged `clipped`. Those record the
crop, not the sky, and **must be excluded from matching**.

### Stage 5 — fiducials from the telescope

`orient.from_mask(mask, ceiling=None)` → `[Fiducial(az, alt, ceiling, bound, weight)]`

Source: the 2026-08-03 evening sweep, ceiling 60°, az_step 10 —
`captures/2026-08-03-evening/horizon_part2.yaml` (az 70–250, 19 columns) and
`horizon_west.yaml` (az 260–350, 12 columns).

**Use `horizon_west`, not `horizon_part3`.** `part3` reports az 260–350 all pinned at 60 — every
one censored. `west` measured the same arc an hour later and gets real values (11.2, 2.5, 2.5, 0,
0, 11.2, 15, 23.8). Reading part3 for those azimuths treats censored bounds as terrain.

**Do not use `horizon_mask.yaml` at the repo root.** It is the 2026-08-02 sweep at a 35° ceiling
and is superseded; it carries a banner saying so. A too-low ceiling manufactures confident false
edges (`LESSONS.md` M-09).

**A capped column is a bound, not a value.** Score one-sided. Clamping two-sided also clamps
genuine measurements and manufactures a flattering residual.

**Stages 5 and 6 have no CLI path.** `terminus orient` gets its fiducials by
measuring them — live, or re-judged from a sweep's own `<mask>_profiles.json`
with `--replay`. It cannot take them from a mask file that already holds solved
columns, which is what these two stages do, so reproducing them today means
calling `orient.from_mask` and `orient.fit` from Python as shown. This is the
same point §2 makes generally, stated where it will actually bite.

### Stage 6 — solve the rotation

```python
orient.fit(fids, sample,
           yaw_step=0.5, tilt_max=15.0, tilt_step=1.5,
           pitch_range=12.0, robust=True, delta=4.0,
           min_headroom=2.0)
```

Three parameters — yaw, pitch, tilt (magnitude + direction) — as one **rigid rotation**.

- **Use `rotate()` / `native_column()`, not a small-angle sinusoid.** The obvious model,
  subtracting `c0 + A·cos(az) + B·sin(az)`, is a first-order approximation. At 12° of tilt it
  errs **2.19° at altitude 60** — comparable to the entire error budget, and worst exactly on the
  tall obstruction that matters. Mode B's own instructions tell users to tilt up, so this is the
  regime the tool creates.
- **`native_column` solves for the photo column that *lands* at each fiducial's azimuth.** Yaw
  only relabels azimuth, but tilt moves a point in azimuth too, so reading the photo at
  `az - yaw` and scoring it as `az` silently reintroduces the approximation. On the real data the
  correction shifts the sampled column by **1.21° median, 8.48° worst**.
- **Robust loss is not optional.** A rigid rotation cannot invent a bend in the horizon but it
  can tip everything, so plain least squares will rotate the sky to accommodate one bad column.
- **`min_headroom=2.0`** drops columns whose result sits too near their own ceiling.

### Stage 7 — the exclusions

**Exclude az 60 and az 190.** Both were measured during a dawn transition with the sky reference
and terrain contrast moving. Including them moves yaw to 137.19° and inflates the residual to
**4.08°** — that is a statement about those two columns, not about the orientation.

(Separately: the 2026-08-05 re-measurements of those same two azimuths — 36.46° and 38.59° — are
**retracted**, because the wedge was ~8° out of polar alignment at the time. Don't substitute
them in. See `terminus-32`.)

---

## 4. Expected results

| | |
|---|---|
| Frames | 19 → **16 used** |
| Control points | **422** |
| Solver residual | **2.51 px ≈ 0.09°** |
| Yaw | **135.81°** |
| Pitch | **+1.94°** |
| Tilt | **2.81° toward 180°** |
| Fit to telescope | **0.69° RMS**, worst column 1.33° |
| Fiducials | **16** (12 edges + 4 bounds) |

### Validation that isn't the residual

A residual only says a fit is self-consistent. Three independent checks it could have failed:

1. **The circle closes.** 19 frames must accumulate to exactly 360°. Hugin closed the loop and
   solved the lens to 53.3° from a 56.8° starting guess.
2. **Out-of-sample agreement.** az 0, 20 and 340 were "blocked above 35°" in the sweep and were
   never used in the fit; the photo reads 61.0°, 61.4°, 56.2° — consistent with the bounds.
3. **Uniqueness.** Best yaw 135.81° at RMS 0.95; next distinct minimum 154° at RMS 12.19 — 3.7×
   worse. And the photo's single tallest feature, 74° high, lands at true azimuth 3 — due north,
   exactly where the telescope independently says the horizon stays above 60° across four columns.

**The ±5° caveat.** Across five defensible sky-classifier variants the solved yaw ranged
**132.5°–141.25°**. That spread, not any single residual, is the real uncertainty in where north
is (`LESSONS.md` M-14).

---

## 5. Traps, in the order they will hit you

1. **`pto_gen` guesses a rectilinear 50° lens for everything.** Photographs need their real FOV
   from EXIF; a stitched panorama needs projection `f4` (equirectangular) plus its angular span.
   Getting this wrong is **silent** — the solve just converges somewhere wrong.
2. **Never mix Mode A and Mode B.** A solve contains either one stitched panorama or a set of
   individual frames. Never both, never two panoramas. The same sky region taken from two
   different stitched panoramas solves to yaw differing by **−3.75° to +6.53°** across 90° of
   azimuth — that drift is the phone stitcher's internal inconsistency, and mixing imports it.
3. **Ultra-wide input breaks `cpfind`'s outlier rejection, not its detection.** It finds
   6,617–11,570 keypoints on a 358° panorama quite happily, then filters them with a
   **homography** RANSAC model that is invalid for that projection — so consensus filtering is
   meaningless and outliers survive. `autooptimiser` then drove the reference FOV to **414°**,
   which is impossible. Locking the FOVs made it *worse* (RMS 104 → 273). This is why Mode A
   exists: modest-FOV rectilinear frames make the model valid.
4. **A stored calibration is only valid for the detector that produced it.** Applying a saved fit
   to a re-detected skyline gave a **systematic +20.1° bias** — the horizon-row parameter had
   silently absorbed the old detector's bias. Version calibrations against the detector.
5. **Tree sway is real.** The frames span 15:33–15:34 and crowns move within that. Rooflines,
   porch posts and fence lattice are trustworthy; canopy is not. With hundreds of control points
   sway is a minority; with a sparse set it is the dataset.
6. **Don't blend seams away** (see Stage 2).
7. **Don't tell users to hold the camera level.** The 12° tilt in the Mode B panorama was
   deliberate — tilting up to fit a tall tree in frame. The fit solves for it, and tall
   obstructions are exactly the ones worth capturing. Report the recovered tilt back as a result
   ("camera tilted 12° toward az 152"), not a warning.

### Shooting spec, if new frames are ever needed

Main camera, **portrait**, not ultrawide. ~30° azimuth steps → 12 frames for 360°, ~47% overlap.
Tilt up ~25°, covering roughly −11° to +61° altitude in one ring. Extra frames aimed higher
wherever the obstruction runs past the top of frame. **Lock exposure and focus.** No HDR, no
night mode, no panorama mode — all of them fuse or warp geometry. Shoot from the scope's position
at scope height; parallax against the near fence is the one error this does not remove.

---

## 6. What is not reproducible

The original fiducial set and mosaic tiles lived only in a session scratchpad and are gone. The
**inputs** all survive — 19 frames staged at `captures/panoramas/2026-08-03-frames/`,
telescope sweeps in `captures/2026-08-03-evening/` —
so the pipeline is reproducible end to end, but you will be *recomputing* the published numbers,
not verifying stored intermediates against them.

The rendered views in the published artifact are at yaw **135.00°**, which is 0.81° off the
converged 135.81° — inside the mask's own 2.5° quantisation and not worth re-rendering.

Earlier published figures of **yaw 135.00, pitch +2.5, tilt 3.0 toward 165, RMS 0.55 on 12
fiducials** came from the defective rotation model described in Stage 6. Yaw survived the
correction unchanged; pitch and tilt direction did not.
