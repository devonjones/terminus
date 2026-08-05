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

### I-02 — Focus has a near limit, and it is where your own house is

Contrast rises monotonically to the mechanical stop (sd 2.63 → 8.08 at step 2600 =
`max_step_range`) and never turns over, because best focus lies beyond the travel. The
target is nearer than the minimum focus distance — roughly **12 m**, resting on an
assumed infinity-focus position that is still untested (terminus-46).

The columns inside that limit are not exotic; they are the observer's own roof and
fence, which is exactly where the horizon is highest. Detect the case in three
captures — contrast still rising at `max_step_range` — and fall back to the brightness
profile, which is unaffected. Do not hunt.

Six explanations were proposed and killed by measurement before the geometry was
found: darkness (Sun at +7.02°), colour blindness (all channels flat), coarse sampling
(a 10-step sweep across 41 positions is equally flat against a ~7-step depth of focus),
dew (ambient 71 °F), stream degradation (1080×1920 both times), and a decoupled focuser
(correlation falls to 0.5887 between travel extremes, so the optics do move).

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
