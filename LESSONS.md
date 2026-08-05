# Lessons and decisions

Things this project learned the hard way, and choices it made deliberately. Each
entry is here because getting it wrong cost real time, produced a confident wrong
number, or would be re-litigated without a record.

**Scope.** Findings that generalise — about measurement discipline, fitting, and the
instrument. Not a changelog, not a bug list (that is beads), not a narrative of
sessions. If an entry does not change what someone *does*, it does not belong.

**Format.** Stable ID, a one-line claim, the evidence, and what to do about it. IDs
are append-only: never renumber, mark superseded entries rather than deleting them.
`terminus-NN` refs point at beads.

---

## The recurring failure: success that isn't

This is the single most common defect in the project, across firmware, libraries and our
own code. It has its own section because it keeps arriving in new disguises and is
recognised late every time.

### S-01 — Commands report success while doing nothing

The roster, all found in this project:

| Thing | What it claimed | What was true |
|---|---|---|
| `Pointer._goto_wait` | landed az 179.9 alt −39.9 | mount never moved (arm closed) |
| `scope_move_to_horizon` | return code 0 | moved nothing — while pointed at the Sun |
| `lock_exposure` | setting accepted | value clamped, not applied |
| `set_setting` (heater) | accepted | ignored |
| RTSP capture | frame delivered, state `working` | same frozen frame at every pointing |
| `available('segment')` | backend ready | crashed on missing torchvision |
| `autooptimiser` | frame placed | placed on **zero** control points |

terminus-10 called it "the third silent-failure-by-stale-or-unverified-state bug today";
terminus-45.2 called `scope_move_to_horizon` "the fifth command in this project to report
success while doing nothing." It is a category, not a run of bad luck.

**The rule:** every state-changing call verifies its own readback, and every cached
measurement carries an age. A return code is not evidence. Where the effect is physical,
verify by a measurement that would differ if nothing happened — a position readback, a
frame that changes when the mount moves, a monotonically growing Sun separation.

### S-02 — Probe what is actually used, not a proxy for it

`available('segment')` checked that torch and transformers import. The processor also
needs torchvision, so on a host with two of three the check passed and `sky_mask()` died —
in exactly the configuration (Raspberry Pi) where the graceful fallback existed to help.

Same shape elsewhere: `get_view_state` reports `exp_ms 240.0` while `get_setting` reports
`isp_exp_ms 20.0`. **The setting we verify is not the exposure the camera uses**, which
is also why `lock_exposure` appears to clamp every requested value. Verifying the wrong
field is indistinguishable from verifying nothing.

### S-03 — A poll loop that times out must raise, never fall through

`_goto_wait` polled to its timeout and then fell through, so `point_to` returned whatever
position the mount happened to hold and the CLI printed it as a landing.

This is safety-critical rather than cosmetic: every Sun-safety guarantee is planned from
the *believed* current position. A slew that silently fails to arrive means the next path
is computed from a place the mount never reached, so the guard can authorise a path that
sweeps somewhere else entirely — the same class as the incident that swept the tube past
the Sun.

Two details worth keeping: the deadline must **extend while the mount is demonstrably
still moving** (`move_type`), because giving up mid-slew is the dangerous case — the scope
keeps moving after you stop watching. And the timeout must fit the real worst case; a
genuine ~118° RA slew outlasted a 35 s limit and was being reported as failure.

### S-04 — Stale state fails silently, and in the dangerous direction

`run_sweep` seeded the sky reference once from a near-zenith frame and reused it for a
3–4 hour circle. The blocked test is `median < 0.5 × sky_ref`; with `sky_ref = 254` that
threshold is 127, so once real sky fell below 127 after sunset **every remaining column
would report blocked_above** — a mask claiming a whole hemisphere is obstructed, returned
with no error and no warning.

Caught mid-sweep before it fired. Fixed with a max age (420 s) and a refresh inside the
column loop. Note the failure direction: stale state made everything look *dark*, which
is the same direction as the auto-exposure failure and the frozen stream. Silent failures
in this project have a house style, and it is "confidently reports obstruction."

---

## Measurement discipline

### M-01 — Timestamp the observation, not the calculation

An ephemeris must be evaluated at the moment the photo was taken, never at the
moment the arithmetic runs.

The solar sighting loop captured a frame, then called `Time.now()` afterwards to
get the Sun's position. Between the two sat a 25 s slew, a scan loop of up to
150 s, and the capture itself — two to four minutes, all of it in one direction,
because the Sun never moves back. The Sun's apparent motion is **0.239°/min**, and
its azimuth component is positive for the entire afternoon (+0.14 to +0.24 °/min
from 17:00 to 23:00 UTC), so the bias has a fixed sign and cannot average out.

Fed into a loop where a human adjusts the mount on each reading, the error
integrates: roughly 0.5–1° per iteration, and after six to eight iterations the
mount was **over 8° off, counter-clockwise** — driven there by the corrections
themselves. Devon caught it from the physical evidence, not from the numbers.

**Do:** return the capture timestamp from the capture call and evaluate the
ephemeris at it. Bracket the exposure, use the midpoint, and carry half the spread
as an explicit uncertainty (0.239°/min × half-width). A reading whose uncertainty
exceeds its own value must refuse to recommend an adjustment. For a goto, compute
for predicted arrival or re-read on arrival. Check host clock against the scope's
`pi_get_time` — a skew does the same damage silently.

**Blast radius:** every "mount coords vs Sun ephemeris" number is contaminated.
Measurements built from frame-to-frame displacement (drift rate) or from sensors
(balance angle) are not, because no ephemeris enters them. Partition results this
way before trusting any of them.

### M-02 — An edge is a persistent level shift

Not the largest single drop, and not the longest sustained decline. Both were tried
and both failed, in opposite directions.

*Largest single drop* fires on a transient sky wobble — it caught a cloud flicker at
alt 27 and aimed autofocus at empty sky. *Longest sustained decline* replaced it and
failed the other way: at az 60 it picked a gentle 19-count slope across terrain
shading (alt 29→19) and missed the **102-count cliff** at alt 37→35, because it
rewards the length of a run rather than its magnitude.

The discriminator that works is a large drop after which the level never returns.
A wobble recovers; shading is small. `night.find_horizon` already implements this
("confirm it stays down"), so column walks should call it rather than hand-roll a
rule in a script.

### M-03 — A boundary is a sky boundary only if the level above it is still sky

Persistence alone is not enough on a layered column.

az 190 has three persistent drops against a 65.63-count sky reference — alt 38
(62.88→32.66), alt 26 (38.78→27.66) and alt 21 (36.26→21.75). None of them
recovers, so a persistence test accepts all three. Only the first leaves sky; the
other two are different faces of a house, and the level immediately above each is
already far below sky. This is why az 190 accumulated four different published
answers (12.5, 21.8, 28.6, 32.5) before measuring **38.59**.

**Do:** test each candidate against the *absolute* sky reference, not just against
persistence. Getting this wrong reports a horizon 12–17° too low, as a confident
number rather than a bound. See terminus-47.

### M-04 — Estimate the reference from a region that excludes the signal

The same mistake appeared in two different functions and produced two different
disasters.

`fit_skyglow` was handed the whole column, terrain included, so terrain set the
model slope — on az 60 it returned slope 4.24 counts/deg and intercept −55.1, a
model predicting *negative* sky brightness below alt 13. Downstream, the
`pred > 1e-6` guard clamped deep terrain to ratio 1.0, so the further into the
ground the column ran, the more sky-like it scored. `find_edge`'s noise estimate has
the same shape: it counts the sky gradient it is detecting against as noise.

Fitting the model to the top of the column alone — the part known to be sky —
recovered the az 60 edge to within one sampling step. See terminus-47, terminus-14.

### M-05 — Compare structure, not brightness

Verifying the solar filter by brightness produced three false aborts and one false
pass. A threshold set below the sensor floor "confirmed" 35× attenuation that was
not real.

The decisive test was structural: correlation 0.0012, sd 0.082 against 20.15,
gradient exactly 0 — the filter blocks the image entirely. Near a bright source the
rule inverts again: use saturation and absolute mean, because the Sun's halo
gradient is itself structure. Pick the invariant that matches the regime instead of
reusing one that worked elsewhere.

### M-06 — Lock the exposure, and verify the readback

Auto-exposure destroys the brightness step the horizon detector depends on: it
raises gain over dark terrain and flattens exactly the contrast being measured. A
first attempt at az 190 produced an unusable profile that was pure auto-exposure
noise; locking it gave 165 counts of contrast on the same column.

Locking is not enough — the requested value is not always the applied value. Read it
back and confirm before trusting the profile.

### M-07 — The sky reference goes stale, inside a single session

Two walks of az 190 twenty minutes apart in fading twilight saw open sky move from
**65.6 to 40.2 counts**. The edge altitude was stable across that (38–39, then
38.59) only because it is a ratio test; any absolute threshold carried between the
two runs would have been wrong.

**Do:** re-measure the reference per column, never once per session. terminus-10.

### M-08 — Dark directions want twilight, bright directions want night

After midnight the open-sky reference fell to ~20 counts. Unlit terrain reads near
zero, so the *ratio* stays excellent — better than 100:1, far beyond daylight's
4:1 — but the absolute numbers are small enough that ordinary scatter swamps the
edge test. Six columns failed outright.

The camera's live exposure is fixed at 240 ms and cannot be raised, so this is a
scheduling constraint, not a tuning problem. It also disguises itself: a circular
sweep visits azimuths in time order, so a systematic failure toward the faintest
directions looks like a property of those directions.

### M-09 — A capped column is a bound, not a value

A sweep that reaches its altitude ceiling knows only that the horizon is *at or
above* it. Scoring that as an equality discards real agreement; clamping two-sided
is worse, because it also clamps genuine measurements and manufactures a flattering
residual. Score one-sided, and let every column carry its own ceiling so sweeps at
different ceilings can be mixed.

Corollary, learned twice: **a too-low ceiling manufactures confident false edges.**
If the horizon lies above the search range, every sample sits inside terrain, and a
step fitted to variation *within* an obstruction is still reported as an edge. The
35° sweep did this at az 40 (32.5, later 48.8) and az 60 (recorded blocked>35, truly
36.46). terminus-7, terminus-8.

### M-10 — Save the numbers, not just the images

The first az 190 re-measurement saved only JPEGs. When its value was later
questioned, nothing could be re-derived offline and the whole column had to be
re-observed under a fresh sky.

Profiles are small, and they make a disputed result answerable at a desk instead of
at the telescope. Every column walk now writes its profile JSON, and those files
double as regression fixtures with independently known answers — which is what lets
terminus-47 be fixed and tested without waiting for a clear night.

### M-11 — Averaging repeats cannot fix variation that is not measurement noise

The quantity `find_edge` divides by was assumed to be measurement noise. It is not:
per-frame scatter at a fixed pointing, measured with 6 repeats, is **0.00–0.16 counts** —
captures at one pointing are essentially identical. The variation *between altitudes* is
real scene structure.

So averaging repeats buys nothing, and an attempt to beat the noise down that way rested
on a wrong premise. Establish what your scatter actually is before choosing a remedy;
stacking is the reflex answer and here it was answering a question nobody had.

### M-12 — At night the two-level model inverts: lights sit inside terrain

`find_edge` assumes bright sky above, dark terrain below, one step between. After dark
terrain contains streetlights and windows far brighter than the night sky.

az 340 runs 5.8–10.7 counts from alt 60 down to 17.5, spikes to **53.9 and 56.4** at alt
15 and 12.5, then goes dark again — a streetlight inside otherwise fully blocked terrain.
A targeted run duly reported az 340 = 10.0°, which was the lamp. The evening sweep's
"blocked above 60" and the photo's ~56° were both right; the newest measurement was the
wrong one.

The light dome is the second problem: az 300 rises threefold from zenith to horizon
before dropping at the real edge. A night detector cannot be a two-level step fit on raw
brightness — detrend the skyglow, mask isolated bright excursions inside dark runs, or
classify per frame with segmentation, which does not care about absolute brightness.

### M-13 — Pick the detector per column, from that column's own conditions

One run's sky reference climbed 25 → 82 counts as dawn came on, so later columns were
measured in twilight while earlier ones were measured at night. The two detectors
disagree materially on the same data — az 190 gives 35.0 by `find_edge` and 12.5 by the
night detector; az 150 gives 35.0 versus "open to the floor."

Applying one model to a whole run means the answer depends on when the loop happened to
reach a column. The reference is already recorded per column; select the detector from it.

---

## Fitting and validation

### F-01 — A residual only says a fit is self-consistent

It is not evidence the fit is right, and it is measured against the very fiducials
the fit was given. Mode B fitted 5 parameters to 8 points and flattered itself at
2.38°; checked against Mode A across the whole circle the two disagree by **5.55°
RMS, 12.1° worst case**.

Validate out-of-sample instead. Three checks the photo mosaic could have failed and
did not: the circle closes (19 frames must accumulate to exactly 360°, and Hugin
solved the lens to 53.3° from a 56.8° guess); columns excluded from the fit agree
(az 0, 20, 340 were "blocked above 35°" and read 61.0, 61.4, 56.2); and two
"unresolved" columns resolved to ordinary values, meaning they had been failed
measurements rather than obstructions.

### F-02 — A frame that cannot be constrained must be dropped, never placed

One frame with **zero control points** was placed anyway by `autooptimiser`, at
whatever its default orientation happened to be. The result looked exactly like a
classifier bug — the horizon reached into open cloud in thin straight-edged wedges,
and the wedges were straight because they were frame boundaries.

Every plausible classifier cause was chased and eliminated first: not branches or
wires, not celeste's cloud model, not alpha feathering, not isolated spikes. The
diagnostic had been sitting in the logs the whole time. Dropping that frame and two
others surviving on 1 and 4 points cut the solver residual **10.35 → 2.51** on *more*
control points (422 vs 350) — the bad frame had been degrading the entire solve, not
just its own patch.

**Do:** count control points per image after cleaning, drop below a floor, and repeat
— dropping frames changes connectivity, so one pass is not enough. Report what was
dropped; a silently discarded frame is a silently missing piece of sky.

### F-03 — Correcting one member of a set with shared systematic error proves nothing

az 200 was the largest disagreement on the page. Re-measured on a corrected mount it
moved 13.8° → 35.0°, and refitting shifted the solved orientation by only 0.13°.
That was read as evidence the orientation was robust. It was not: it showed the fit
was insensitive to *that column*, which says nothing about what happens when the
whole set is replaced.

Replacing all of them moved yaw **130.25° → 135.81°**, a 5.56° shift, and improved the
residual 3.10 → 0.69. The control test was sound as far as it went and its conclusion
was wrong.

### F-04 — A test that builds its fixtures with the same shortcut as the code cannot catch the bug

The rotation model claimed to be exact and was not: it sampled the photo at
`az − yaw` and scored the result as though it belonged to `az`. That is correct for
yaw, which only relabels azimuth, and wrong for tilt, which moves a point in azimuth
too. Against an honest forward model the residual at the *true* parameters was 0.56°
RMS at 12° of tilt instead of zero.

A test existed and could never have failed, because it generated its own fiducials
using the same shortcut — it scored the code against its own defect. On real data the
correction shifted the sampled column by 1.21° median, 8.48° worst.

Reassuringly, yaw survived unchanged at 135.81°; pitch and tilt did not
(+2.5°/3.0°@165° became +1.94°/2.81°@180°).

### F-05 — Scan the landscape; do not trust the minimum

A residual cannot distinguish two fits sitting 60° apart. The solution space was
scanned rather than assumed: best yaw 135.81° at RMS 0.95, next distinct minimum
154° at RMS 12.19 — separated by 3.7×.

Then the winner was checked against something the fit never saw: the photo's single
tallest feature, 74° high, lands at true azimuth 3 — due north, exactly where the
telescope independently reports the horizon stays above 60° across four columns.

### F-06 — One-sided bounds exclude wrong answers; they do not select among survivors

The four ≥60° bounds were assumed to be what pinned the rotation. They are not: a
one-sided bound scores zero wherever it is satisfied, so a wide range of yaws fits
them perfectly and they are degenerate alone. The ordinary edges do the selecting.

### F-07 — A fix that improves every measure at once is real; one that trades is tuning

Replacing the brightness gate with a colour rule moved fit-to-telescope 2.59° →
2.23°, sky recognised 43.1% → 50.9%, mean horizon 31.9° → 29.6°, and left solved yaw
unchanged. Four earlier variants that improved one measure at the expense of another
were rejected as tuning.

Corollary: **the metric can be blind to the error class you are fixing.** The fit
absorbs a uniform altitude bias into its pitch term, so RMS could not see the defect
that segmentation actually repaired — which is why round after round of tuning
"improved" a number while the picture got no better. When the metric and the eye
disagree, find out which one is blind before believing either.

### F-08 — A capture that succeeds and returns constant data is indistinguishable from a measurement

The video stream froze and served one stale frame whatever the mount did. Every
capture succeeded, every column returned a number, and ten western azimuths were
recorded as blocked above 60° — roughly a hundred degrees of obstruction invented out
of open sky. Folding those in moved yaw 130.0 → **70.4**, pitch to −17.7, tilt to
21.2, RMS to 10.3. Re-measuring against a live stream returned it to 130.25.

The frozen frames hold three to five distinct pixel values and identical statistics at
every azimuth and altitude — there is no faint image to stretch. Nothing downstream
can tell a successful constant capture from a real one, which is the argument for
cross-checking two instruments rather than trusting either. terminus-12.

### F-09 — Do not double-apply a transform

The first attempt to verify the re-fit sampled the already-rotated profile and rotated
it again, reporting a residual of 20.67° — a number that looks like catastrophic
disagreement and means nothing.

### F-10 — Reducing an image to one curve throws away nearly all of it

Registering photos by cross-correlating their detected skyline curves failed outright. A
suburban skyline is a repetitive 1D signal — matching rooflines, evenly spaced fence
posts, near-identical neighbouring houses — and the *same photo* landed at az 44, 214 and
335 under small, defensible changes to the objective, with no way to tell which was right.

Phase correlation **on pixels** worked: peaks 6–23× the noise floor, and it agreed with
the skyline method to 0.5° in the one case where that method happened to be right. The
images share thousands of features; the curve kept almost none of them.

### F-11 — A scoring function that rewards a degenerate overlap will find one

Two traps, both of which fired while registering panoramas:

- **RMS rewards a small flat overlap.** The search slid the images apart until only
  featureless fence agreed, reported a tiny error, and returned a vertical scale of
  **0.02** — it had flattened one image to a constant.
- **Plain NCC rewards a shrinking overlap.** Every direct vertical-offset search railed to
  its boundary.

Phase correlation is immune to both because it whitens the spectrum, so the peak does not
depend on overlap size. Before trusting an optimiser, ask what its objective pays for at
the edge of the domain.

### F-12 — A bad refinement is worse than none

Windowed refinement recovered scale drift and tilt correctly on wide sweeps. On a narrow
still whose overlap was nearly all foliage, 8 windows returned **109° of tilt and +17%
scale** — physically impossible, and it would have been applied.

Now rejected on explicit bounds (|scale| < 5%, |tilt| < 15°, azimuth spread > 25°) with a
documented fallback to the visually verified constant offset. A refinement step needs a
sanity gate and a defined thing to do when it fails.

### F-13 — A calibration is only valid for the detector version that produced it

Applying a saved fit to a re-detected skyline produced a **systematic +20.1° bias** (RMS
21°) — not scatter. The fitted horizon-row parameter had silently absorbed the *old*
detector's bias, so the calibration encoded the detector as much as the site.

Store calibrations with the detector version, and refuse to apply one produced by a
different version. Without that this recurs silently every time the sky test is touched.

### F-14 — Ground truth built from the data it validates is not ground truth

Tiles were cut from stitched panoramas at 30° spacing and the solve recovered
30.72/30.85/30.51° — reported as sub-degree absolute accuracy. Devon objected that the
inputs were already modified, and he was right, twice over: the 30° spacing assumed 55.8
px/deg and the tiles' FOV was declared using **the same assumption**, so the test measured
internal consistency; and tiles inherit the stitcher's local warping, which real frames
will not have.

The retraction is a good template. What survived the objection was stated explicitly:
338 control points versus 41 at default parameters, and RMS 273 → 10.76 — both raw
correspondence counts and convergence facts that no geometry assumption enters. So the
diagnosis ("the blocker is input FOV, not Hugin") held; the accuracy class did not.

### F-15 — Detection succeeding does not mean matching will

`cpfind` found 6,617–11,570 keypoints on every ultra-wide panorama. Detection was never
the problem. Matching was: it remaps wide-FOV images before detection, and its RANSAC
geometric model is a **homography**, which is invalid for a 358° image — so consensus
filtering was meaningless and outliers survived. `autooptimiser` then drove the reference
FOV to 414°, which is impossible.

Locking the FOVs, the obviously correct constraint, made it *worse* (RMS 104 → 273). The
same root cause rules out OpenCV's `cv2.detail`, whose camera model assumes pinhole
inputs — rejected on capability, not dependency weight.

When a model assumption is violated upstream, everything downstream keeps returning
confident numbers.

### F-16 — One outlier tips the whole sphere

A rigid rotation cannot invent a bend in the horizon, but it can tip everything, so plain
least squares will happily rotate the sky to accommodate a single wrong column — and a
too-low ceiling is known to manufacture confident false edges (M-09).

Use a robust loss (Huber, trimmed) and report per-fiducial leverage, so a dominant point
is visible rather than silent.

### F-17 — Find the binding constraint before optimising anything else

Absolute calibration was attacked three ways: a hand-rolled 6-parameter fit, a refit with
vertical scale free, and Hugin's bundle adjustment. All three ran out of constraints on
**the same 6–7 usable fiducials**.

Recorded at the time as "not fixed by better tooling or better photo processing — fixed by
the denser baseline sweep." Considerable effort went into the registration path while the
actual limit was upstream of all of it.

### F-18 — Residuals 180° apart in azimuth are a tilt signature

Fitting the panorama without a tilt term gave RMS 5.5° with residuals **+10.3 at az 80 and
−9.3 at az 280** — opposite in sign, half a circle apart. That is what a non-level camera
looks like, and adding one sinusoid took RMS to 2.6° while independently recovering a
358° span.

A structured residual pattern names its own missing parameter. Read the residuals by
azimuth before adding degrees of freedom blindly.

---

## The instrument

### I-01 — The telescope can focus on terrain

Everything earlier rested on it being unable to: at 250 mm focused at infinity, every
object at this site sits about a thousand times inside the hyperfocal distance. True,
and the conclusion drawn from it — that no setting could fix it — was wrong. Focusing
at 40 m needs about 1.26 mm of focuser travel. Nobody had tried.

Gradient p99 goes **0.73–1.34 blurred to 27.52 focused**. `start_auto_focuse` searched
1838 → 2027 → 1718 and settled at 1839 in about eight seconds, within one step in 2600
of the position found by hand. The scope reports `moving` then `idle`, so confirm
completion by reading the position back rather than trusting a return code.

This makes obstruction type, gap fraction, and per-column *distance* things the
telescope measures rather than infers. terminus-32.

### I-02 — Some columns will not focus at any focuser position; the cause is unsettled

**Measured and solid:** on az 170 and az 190 the sharpness metric `gradient p99` is
**flat at 1.12–1.50 across the entire travel, step 200 to 2600**, in R, G, B, R−B, G−B
and saturation alike, against 27.52 on a sunlit roof at 40 m the same morning. No
focuser position sharpens these targets.

Six explanations were proposed and killed by measurement: darkness (Sun at +7.02°),
colour blindness (all channels flat), coarse sampling (a 10-step sweep across 41
positions is equally flat against a ~7-step depth of focus), dew (ambient 71 °F), stream
degradation (1080×1920 both times), and a decoupled focuser (correlation falls to 0.5887
between travel extremes, so the optics do move).

**Leading hypothesis: the near-field limit.** These columns point at the observer's own
house at a steep angle, plausibly inside the minimum focus distance, while the 40 m roof
that focuses normally is outside it. Devon confirmed the pointing.

**Not demonstrated — an earlier version of this entry overstated it.** The claim was that
contrast rises monotonically to the mechanical stop (sd 2.63 → 8.08 at step 2600), so
best focus must lie beyond the travel. That reasoning is wrong: `sd` is whole-frame
contrast, driven by scene content and exposure, and is not a focus metric. Across the
same sweep `gradient p99` — which *is* the focus metric — stayed flat and fell slightly
(1.50 → 1.17). Had focus been improving toward the stop it would have risen.
terminus-30.8 records an independent fine sweep at 2440–2600 on az 190 that is flat,
consistent with the corrected reading rather than the original claim.

**Do:** judge focus with a sharpness metric, never with `sd`. Settle the near limit by
measuring infinity focus and the step-to-distance curve (terminus-46), not by inferring
it from a contrast trend. Until then "no focus achievable here" is the finding and the
near limit is the hypothesis. Detection is cheap either way: flat `gradient p99` across
the range means stop hunting and fall back to the brightness profile, which is
unaffected and kept working on these columns throughout.

An instance of F-07 in its own right — a number moved the way the hypothesis wanted, and
it was not measuring the thing the hypothesis was about.

### I-03 — The scale wall: telescope frames cannot be registered into phone imagery

Feature-matching scope frames into the photo mosaic would have removed the edge
detector, the night detector and the censored-bound machinery in one stroke. It cannot
work. Scope resolution is 1505 px/deg against the panorama's 55 px/deg — a **27–33×**
ratio — so at panorama scale the entire telescope frame is **40 × 71 pixels**: a
diagonal edge and one dark blob.

The limit is the phone, not the telescope, so better scope data cannot fix it. What
survives the scale wall is *meaning* rather than pixels: a tree at one azimuth and a
roofline at the next is a class sequence that can be aligned against the panorama's own
classes, and that comparison does not care about resolution at all. terminus-33,
terminus-39.

### I-04 — Sharpness finds a daylight edge that brightness cannot

Walking a column under auto-exposure, the brightness step vanished while the edge
stayed obvious in a different channel: luminance moved 2% across a roofline (99.0 →
101.5) while gradient p99 moved **20:1** (26.79 → 1.28).

Caveat kept deliberately: that run used auto-exposure, so locking it may restore the
brightness step (see M-06). Both channels are worth having, and this inherits the near
limit in I-02. terminus-37.

### I-05 — Local variance does not separate sky from structure

The textbook cue — sky is smoother than buildings — fails outright here. Measured on
this mosaic, sky reaches a 90th-percentile local deviation of **55.8** against
structure's median of **23.5**. Heavy cloud is more textured than siding. The
assumption does not hold under overcast.

### I-06 — Foliage moves between frames; rigid structure does not

The panorama set spans 14:14–14:16, and tree crowns move within it. Rooflines, porch
posts, fence lattice are trustworthy cues; canopy is not. This is why the narrow still's
windowed refinement failed — its overlap was nearly all foliage (F-12).

Weight rigid structure, and expect robust fitting to be doing real work rather than
insurance. With hundreds of control points a swaying tree is a minority; with a sparse
set it is the dataset.

### I-07 — One control connection, and diagnostics read the log

The Seestar tolerates a single control connection. Attaching diagnostic scripts while a
sweep held the socket produced "cannot read current pointing" and a `BrokenPipeError`,
and is a suspected contributor to the frozen stream (S-01).

A sweep should hold a lock. Anything that wants to watch it reads the log, not the scope.

---

## Safety

### SAFE-01 — Check the path, not the endpoints

A candidate column sat **59.4° from the Sun at its endpoint** — comfortably outside the
30° cone — while the slew path passed within **7.5°**. In EQ mode a goto swings through
RA, and the arc is not the straight line between endpoints.

Every commanded motion goes through `Pointer.point_to`, which steps each slew and
re-checks the Sun, recomputed for the current time, at every waypoint. Recorded verbatim
because the temptation recurs: *do not solve this by widening the cone or by checking only
endpoints — both have been tried elsewhere, and both are how a tube ends up pointed at the
Sun.*

### SAFE-02 — Enforce deadlines inside the loop, not at launch

Observing ran **14 minutes past** the stated safety cutoff. No harm resulted — the Sun was
21° below the horizon — but the 45-minute buffer exists to absorb exactly that drift and
it was spent unnoticed.

The cause generalises: a long run was launched and the clock was never re-checked while it
executed. Each column took longer than estimated *because* dawn was approaching — the sky
brightened, the reference rose 25 → 82 counts, more columns resolved, and the run slowed
precisely as the deadline neared. **A caller that starts an N-column run cannot know how
long N columns will take, and the estimate degrades in the direction that matters.**

Check the Sun and the remaining time before each column, and stop cleanly if the next one
would cross the line.

### SAFE-03 — Feasibility filters before ranking, never after

The planner ranked columns purely by information gain, with no notion of whether a column
could be reached; `point_to` would then refuse it, and the planner had no way to know or
re-plan.

The ordering matters and is easy to get backwards: **filtering after ranking gives you the
best infeasible column plus a fallback; filtering before gives you the best feasible
configuration**, which is a different and better answer. This matters more under the
three-column scheme (terminus-39), where the value is in the geometric configuration — the
Sun does not remove one column, it can destroy the arrangement.

Feasibility is also time-dependent, which the planner has no concept of: a column refused
now may be fine in two hours, and under a deadline the right move is sometimes to wait
rather than substitute. At minimum, report *why* a candidate was excluded and when it
becomes available.

---

## Decisions

### D-01 — Individual photos over a stitched panorama

Mode A (19 handheld frames → 16 used) beats Mode B (one phone panorama): fit to
telescope 0.69° vs 2.24° RMS, azimuth answered 100% vs 76%, 16 fiducials against 4 free
parameters vs 8 against 5.

The mechanism, not the score, is why this is settled: a 358° panorama has no meaningful
focal length, so feature detection remaps it into nonsense and the homography model used
for outlier rejection is invalid. Ordinary frames have exact pinhole geometry from EXIF
focal length, and outlier rejection works — 544 control points, 53 discarded, hundreds
surviving, so a tree moving between frames is a minority rather than the whole dataset.

### D-02 — Semantic segmentation primary, heuristics kept as fallback

Three failures survived every attempt at heuristic tuning, and each fix traded against
the others: requiring not-warm and not-green rescued dark cloud but admitted off-white
siding, because siding is neutral and so is cloud.

Segmentation removes the problem instead of balancing it — SegFormer on ADE20K labels
each pixel by what it *is*, so cloud is sky because it is sky and siding is building
because it is building. Cost is ~2 s/frame on CPU, ~15 MB model, no hand tuning.
Segmentation independently recovers the same yaw (136.50°) as the colour method, which
is the point.

The heuristic is kept rather than deleted: it needs no torch, which matters on a
Raspberry Pi, and it scores comparably. Both feed identical downstream code.

### D-03 — Report remaining error in degrees; assume nothing about the hardware

Devon: *"don't assume thumb screws, that's specific to my wedge."* The interface reports
remaining error and updates it live; the user stops when it reaches zero. Thumb screws,
knobs, a worm drive, or shoving the tripod are all identical to the software.

This is the better design, not a compromise. The manual session measured this wedge at
about 0.45°/half-turn and the figure drifted (0.456, 0.452, 0.415) because the human was
not being precise. The closed loop absorbed it completely; a calibrated open-loop
instruction table would have been worse. terminus-45.3.

### D-04 — Flag ambiguity; never average it away

A column that will not converge in two or three tries is reporting something real —
thin branches against bright sky, or a boundary that is not a line. Record it as
ambiguous rather than retrying into the observing deadline, and never merge two
disagreeing sources into a mean that hides both. Where the telescope and the photo
disagree, that disagreement is the signal.

### D-05 — Leave the rooflines

Recovering the last sliver of sky beside a roof edge costs far more model complexity
than the sky it returns. Deliberately not fixed.

### D-06 — Two modes, never mixed

A solve contains **either** one stitched phone panorama **or** a set of individual frames.
Never both, and never two panoramas.

Devon's instinct, then validated: the same sky region taken from two different stitched
panoramas should solve to identical yaw. It does not — the difference drifts from −3.75°
to +6.53° across 90° of azimuth. That drift is the phone stitcher's own internal
inconsistency, and mixing stitched sources imports it into the solve.

### D-07 — Tilt is a feature; do not tell users to hold the camera level

The 12° tilt recovered from Devon's panorama was deliberate — he tilted up to fit a very
tall tree in frame. Tall obstructions are exactly the ones worth capturing, and they are
the reason a user tilts.

The correct instruction is the opposite of the obvious one: *frame however you need to get
the obstructions in; the fit solves for it.* Report the recovered tilt back as a stated
result ("camera tilted 12° toward az 152"), not as a warning — it is a sanity check the
user can confirm from memory, exactly as Devon did.

This also raises confidence in the model rather than lowering it. A deliberate, consistent
tilt is a single rigid rotation, which is precisely what one sinusoid represents; random
handheld wobble would appear as per-column scatter instead. Cutting RMS 5.5 → 2.6 with one
term therefore indicates a real physical parameter was recovered.

Shooting spec that follows: main camera in portrait (not ultrawide), ~30° azimuth steps,
tilt up ~25°, **lock exposure and focus**, no HDR / night mode / panorama mode, shot from
the scope's position at scope height.

### D-08 — Never blend seams away

Visible seams are how a person checks the fit. Blending makes a bad registration look
plausible and removes the cheapest available diagnostic. (`enblend` is absent on the dev
box and deliberately not wanted.)

### D-09 — Use the field's vocabulary

"Porosity" is windbreak and shelterbelt vocabulary and is absent from the canopy-photography
canon; the established term for what `horizon_band` computes is **gap fraction**, or
directional gap fraction (Jonckheere et al. 2004). Renamed before the API went public.

The same applies elsewhere: the orientation estimator is **Wahba's problem**, upper limits
are **censored regression**, and the column planner is **D-optimal sequential design**.
Using the right name costs nothing and is what makes the work findable by the people best
placed to check it.

### D-10 — The mask describes one spot, and must say so

The horizon is recorded from wherever the tripod stood. Parallax for a ridge two kilometres
away is nil; for a fence five metres away, moving two metres sideways swings its apparent
altitude by degrees — and the error is largest exactly where the horizon is highest and
most consequential.

State it in the mask meta, the exports and the README. Quantify it where possible: per-column
distance (terminus-32) is exactly what sets parallax sensitivity, so a mask carrying distance
can also carry "how far can you move before this column is wrong."

Recorded with its own correction: the bead was filed on the theory that a scope/observer
disagreement about the Moon was parallax. It was not — the scope saw flat blue sky, and the
cause was smoke haze. The limitation is real and reasoned from geometry, but it was **never
observed here**, and the original text implied field evidence that did not exist.

### D-11 — Prefer the sweep whose ceiling exceeded its own result

Not "prefer newer." The rules, in order:

- a column is trustworthy when its result sits comfortably below the ceiling it was scanned with;
- a column that reached its ceiling is a bound, not a value;
- when two sweeps disagree, prefer the one whose ceiling exceeded its own result;
- a column whose result equals its ceiling must never be stored as type `edge`.

Corollary: re-scan any column from an older mask whose altitude sits within ~5° of that
sweep's ceiling. Those are the likely artefacts.
