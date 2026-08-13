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


## Index

**The recurring failure: success that isn't**

- `S-01` Commands report success while doing nothing
- `S-02` Probe what is actually used, not a proxy for it
- `S-03` A poll loop that times out must raise, never fall through
- `S-04` Stale state fails silently, and in the dangerous direction
- `S-05` A verified readback is worthless if it reads a field the device does not use

**Measurement discipline**

- `M-01` Timestamp the observation, not the calculation
- `M-02` An edge is a persistent level shift
- `M-03` A boundary is a sky boundary only if the level above it is still sky
- `M-04` Estimate the reference from a region that excludes the signal
- `M-05` Compare structure, not brightness
- `M-06` Lock the exposure, and verify the readback
- `M-07` The sky reference goes stale, inside a single session
- `M-08` Dark directions want twilight, bright directions want night
- `M-09` A capped column is a bound, not a value
- `M-10` Save the numbers, not just the images
- `M-11` Averaging repeats cannot fix variation that is not measurement noise
- `M-12` At night the two-level model inverts: lights sit inside terrain
- `M-13` Pick the detector per column, from that column's own conditions
- `M-14` The real uncertainty is the spread across method variants, not the residual of one
- `M-15` Skyglow varies with azimuth, so one pooled sky reference is a false detector
- `M-16` Include known-good controls, and never change two things between comparisons
- `M-17` A single-pass sweep confounds time with the swept parameter
- `M-18` Know the measurement floor before chasing a number toward it
- `M-19` Return "unmeasured", never a plausible wrong number
- `M-20` No single photometric primitive separates sky from terrain
- `M-21` Straight edges in a natural scene mean a pipeline artifact
- `M-22` Calibrate a control's rate before using it, and do not hill-climb on a drifting signal
- `M-23` At night the edge is a persistent, unrecovered drop — and only negative steps are roughness
- `M-24` Overhead wires read as sky through the segmentation — and the unguarded number is `top`, not the skyline

**Fitting and validation**

- `F-01` A residual only says a fit is self-consistent
- `F-02` A frame that cannot be constrained must be dropped, never placed
- `F-03` Correcting one member of a set with shared systematic error proves nothing
- `F-04` A test that builds its fixtures with the same shortcut as the code cannot catch the bug
- `F-05` Scan the landscape; do not trust the minimum
- `F-06` One-sided bounds exclude wrong answers; they do not select among survivors
- `F-07` A fix that improves every measure at once is real; one that trades is tuning
- `F-08` A capture that succeeds and returns constant data is indistinguishable from a measurement
- `F-09` Do not double-apply a transform
- `F-10` Reducing an image to one curve throws away nearly all of it
- `F-11` A scoring function that rewards a degenerate overlap will find one
- `F-12` A bad refinement is worse than none
- `F-13` A calibration is only valid for the detector version that produced it
- `F-14` Ground truth built from the data it validates is not ground truth
- `F-15` Detection succeeding does not mean matching will
- `F-16` One outlier tips the whole sphere
- `F-17` Find the binding constraint before optimising anything else
- `F-18` Residuals 180° apart in azimuth are a tilt signature
- `F-19` To test whether a forward model is exact, evaluate it at known-true parameters
- `F-20` A stability stopping rule needs information-ordered additions
- `F-21` Information about a parameter lives only where the data has gradient
- `F-22` Measure a model correction's effect on the clean input set
- `F-23` Multiplying a robust loss by 1/σ² is not inverse-variance weighting
- `F-24` Floor a weight at the instrument's resolution, not a numerical epsilon
- `F-25` A score is comparable only when everything except the thing under test was held fixed
- `F-26` Dropping under-constrained inputs is iterative
- `F-27` Detrending and outlier-robustness are different properties of a noise estimator
- `F-28` Verify both arms run the current version of every shared component
- `F-29` A detection that assumes correct pointing cannot be evidence about pointing
- `F-30` Register the prediction before the measurement arrives

**The instrument**

- `I-01` The telescope can focus on terrain
- `I-02` Some columns will not focus at any focuser position; the cause is unsettled
- `I-03` The scale wall: telescope frames cannot be registered into phone imagery
- `I-04` Sharpness finds a daylight edge that brightness cannot
- `I-05` Local variance does not separate sky from structure
- `I-06` Foliage moves between frames; rigid structure does not
- `I-07` One control connection, and diagnostics read the log
- `I-08` Do not carry an instrument's guess at a quantity it cannot measure
- `I-09` In EQ mode the frame comes from the mechanics, not from stored software state
- `I-10` Driving the error at one sky position to zero is not alignment
- `I-11` Check whether a sensor's geometry can express the quantity at all
- `I-12` A fixed terrain edge is a free session-to-session pointing reference
- `I-13` Per-axis tolerances are unsatisfiable at a coordinate singularity
- `I-14` There is no still-image path for terrestrial frames; RTSP is not a choice
- `I-15` The scenery stream is blind at night; star mode sees, on a different channel
- `I-16` Near due north the mount cannot converge, and failed gotos escalate to connection resets

**Safety**

- `SAFE-01` Check the path, not the endpoints
- `SAFE-02` Enforce deadlines inside the loop, not at launch
- `SAFE-03` Feasibility filters before ranking, never after
- `SAFE-04` An operator's approval is permission, not evidence about a computable quantity
- `SAFE-05` A safety-critical rewrite gets its first live test supervised and minimal
- `SAFE-06` Never design a workflow that requires a human to look at the Sun
- `SAFE-07` A hazard that cannot reach the optics is not a hazard
- `SAFE-08` A command that lies can also act later
- `SAFE-09` Over-the-top Sun avoidance is unavailable near summer solar noon

**Code, tests and the repo**

- `E-01` Mutation is the standard of proof
- `E-02` A test can pin a bug instead of catching it
- `E-03` An invariant is not pinned if the code that must obey it is unreachable
- `E-04` A test that asserts what the dev box happens to satisfy passes vacuously
- `E-05` Exception type cannot separate "the environment is short something" from "we have a bug"
- `E-06` An absent value is not a matching value, and a fabricated default is a claim
- `E-07` A guard in one caller is not a guard
- `E-08` A half-written multi-file artifact reads as corrupt, not absent
- `E-09` Two silent coercions: a string is iterable, and `{:g}` emits letters
- `E-10` A threshold in degrees is not a threshold in distance, and longitude wraps
- `E-11` When the raw data is not in version control, the fixture *is* the data
- `E-12` A human-editable safety flag needs a closed vocabulary and a typed error
- `E-13` Verify documentation by executing it
- `E-14` Grep is not proof
- `E-15` Concurrent agents make shared-checkout operations unsafe
- `E-16` When two instruments write one record, merge field by field
- `E-17` Validate operator input before the instrument moves, and pin it clock-independently
- `E-18` Prose asserts causes the data cannot support, and reviewers catch it late

**Decisions**

- `D-01` Individual photos over a stitched panorama
- `D-02` Semantic segmentation primary, heuristics kept as fallback
- `D-03` Report remaining error in degrees; assume nothing about the hardware
- `D-04` Flag ambiguity; never average it away
- `D-05` Leave the rooflines
- `D-06` Two modes, never mixed
- `D-07` Tilt is a feature; do not tell users to hold the camera level
- `D-08` Never blend seams away
- `D-09` Use the field's vocabulary
- `D-10` The mask describes one spot, and must say so
- `D-11` Prefer the sweep whose ceiling exceeded its own result
- `D-12` Fragility is a per-layer policy: brittle on interpretation, robust on execution
- `D-13` Don't re-derive an input the user has said is settled
- `D-14` After the second refuted hypothesis, stop theorising and go look
- `D-15` A file at a canonical path is not evidence that it is current
- `D-16` Checkpoint attempts, not successes — and re-judge saved profiles on load

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

### S-05 — A verified readback is worthless if it reads a field the device does not use

`lock_exposure` set and verified `isp_exp_ms` and dutifully reported clamping — "asked 100, got
20.0", "asked 400, got 31.5" — for hours. Every request clamped to 20. Then `get_view_state`
showed `exp_ms: 240.0` against `get_setting`'s `isp_exp_ms: 20.0`. The real control is a nested
object, `set_setting {exp_ms: {stack_l, continuous}}`, and scenery mode uses `continuous`. The
field being verified was a writable-looking mirror that nothing consumed.

The dew heater was the same shape: `set_setting heater_enable` returned success and changed
nothing, because that key is read-only status and the real control is `pi_output_set2`. In both
cases the answer came from reading a third-party client's source, not from the API's responses.

So S-01's rule is necessary but not sufficient. A device can expose a settable field that no
code path reads, and the verification passes while the setting is inert. **Cross-check against a
second, independent status endpoint** — here the *view* state against the *settings* state —
before believing a lock. Commanded exposures of 1.6, 6.4, 25 and 100 ms all returned `isp
1.6296` with frame means identical to three decimals; that identity was the available tell.

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

### M-14 — The real uncertainty is the spread across method variants, not the residual of one

Five sky-classifier variants, all defensible, produced fit residuals between 2.08° and 2.59° —
and solved yaw ranging **132.5° to 141.25°**. No single residual reflects that ±5°, which is the
actual uncertainty in where north is.

Re-running with each defensible nuisance choice and taking the spread of the *answer* costs a few
reruns and is the only honest error bar when the nuisance parameters were never independently
determined. Quote it alongside the residual, never instead of it.

### M-15 — Skyglow varies with azimuth, so one pooled sky reference is a false detector

Column-top luminance across 22 measured azimuths ran **7 to 104 counts** — az 10 at 8, az 30 at
7, az 170 at 99, az 270 at 104. Setting the reference to the pooled median (74.1) returned 11
edges and declared az 300 and 310 "blocked above the ceiling" when they are 10.0° and 12.5°:
columns facing away from town get called blocked for being legitimately darker.

Sweeping the parameter, `sky_ref = 40` reproduces the published table exactly and yields 15 edges
instead of 11. Distinct from M-07, which is staleness in *time*; this is variation across the
dimension being measured. A reference estimated by pooling over a dimension the quantity varies
along converts the low end of that dimension into a false detection class.

Re-running archived raw profiles through the shipped detector to reproduce a published table is a
free validation of both detector and parameter, and it is how this was found.

### M-16 — Include known-good controls, and never change two things between comparisons

After a plate solve found the mount 3.7° off in azimuth, four columns were re-measured and a
script printed `median |shift| 2.50 deg, max 21.20 — VERDICT: old data was affected by the
misalignment`. The verdict was too confident: az 200's +21.2° was **the algorithm fix**, not the
realignment — the re-run used the corrected `find_edge` and landed on exactly what re-judging the
old saved profile gave. The genuine alignment signal was the other three at −2.5, −2.5, −1.2.

What made the two separable was deliberately re-measuring columns that already agreed: "I
included az 100 and az 70 as controls. If those two shift as much as 200 and 310, it's global. If
only the disputed ones move, it's local."

Note also that the polar error is not uniform — computed pointing error was 0.01° at az 0/alt 40
(that *is* the pole, immune) and 3.67° at az 120 and 240. An azimuth-dependent signature with the
same shape as the fit's tilt term, so it can be silently absorbed as camera tilt.

### M-17 — A single-pass sweep confounds time with the swept parameter

A four-hour circle started in daylight and finished in dark. Any drift across the run appears as
a smooth function of azimuth — and the orientation fit contains a smooth azimuth term. **A
four-hour brightness drift can be absorbed as camera tilt and still look like a good fit.**

Confirmed afterwards: in the 18-fiducial fit the only two large residuals were az 60 (−17.40) and
az 190 (+14.58), the two columns measured while the sky reference climbed 25 → 82 counts at dawn.
All twelve others fell within ±3.0. The two detectors disagreed on exactly those columns.

Randomise the visit order, re-visit a subset, or flag the regime boundary explicitly. A model
with a smooth term in the swept parameter will hide this rather than report it.

### M-18 — Know the measurement floor before chasing a number toward it

`start_scan_planet` stops when it *has* the Sun, not when it is centred. Disc offsets from frame
centre across one day: 0.316, 0.030, 0.124, 0.350, 0.030 degrees — up to a third of a degree of
framing slop silently added to every "pointing error" reading. Below about 0.3° that scatter
dominates, which became the stated stopping point for the whole alignment loop.

Two free scale checks came from the same frames: the solar disc measured 0.524° across against a
true 0.52°, and two independent autofocus runs landed on 1838 and 1839 of 2600 steps. Objects of
known angular size validate the entire geometry chain at no cost.

### M-19 — Return "unmeasured", never a plausible wrong number

A sweep produced a mask whose columns had SNR ≈ 1 — steps of 4–23 counts against noise of 5–18,
with only two genuine. The summary looked convincing and matched an independent daytime probe,
while the raw profile for az 20 read `76, 116, 88, 92, 101, 104, 97, 79`: a one-frame spike, not
terrain. The mask and exports were **deleted** rather than left on disk.

The detector was changed to match, gating at 2.5× the column's own noise so noisy columns come
back unmeasured instead of wrong. A file loses its provenance immediately and will be consumed by
the next tool and by future-you. Design for "refused to answer" and "answered" — never "answered
plausibly".

### M-20 — No single photometric primitive separates sky from terrain

Each candidate fails on a specific real material, and each failure was caught by ground truth:

- **Blue-dominance** — a dark branch silhouetted against bright sky still reads blue, from
  scattered skylight plus blur. A frame visibly ~20% branches scored sky = 0.96.
- **Brightness** — a shadowed cloud base is genuinely dark, so brightness rejects it wherever the
  threshold sits.
- **Hue (not-warm and not-green)** — off-white siding is neutral and so is cloud, so the boundary
  walked down into the building and outlined windows and trim.
- **Texture** — measured rather than assumed: sky median 1.3 against ground 13.2, but cloud edges
  reach p90 ≈ 14, overlapping structure (see I-05).

Every classifier of this shape encodes an assumption about the materials present. Enumerate the
materials in the scene that violate it before trusting a threshold, and expect the primitive to
change with lighting regime rather than expecting one rule to hold.

### M-21 — Straight edges in a natural scene mean a pipeline artifact

Dark wedges in the sky were blamed on the classifier for six or seven rounds. Eliminated in turn:
branches and wires (tightening made the fit *worse*, 2.38° → 3.10°), celeste's cloud model
(0.0% of pixels, and its mask polarity is inverted from the obvious guess), alpha feathering
(`nona` writes binary 0/255, zero partial-alpha pixels), and median filtering across azimuth
(largest jump moved only 36.6° → 33.9°, so the excursions are broad, not spikes).

The tell was noticed twice and not acted on: *"note the wedges have straight edges, which clouds
don't."* Devon solved it from one image — "that's the umbrella from my garage in the corner of
that image." One frame had been placed in the wrong part of the sky.

Physical processes produce soft, irregular boundaries; software produces boundaries aligned to
frame, tile or array edges. The geometry of an artifact identifies the layer it came from and
rules out an entire class of hypotheses before any of them is tested.

### M-22 — Calibrate a control's rate before using it, and do not hill-climb on a drifting signal

`scope_speed_move` at speed 200 for 2 s, used as a "small probe", walked the scope **31°** off
the Sun — about 7.5°/s. Linear extrapolation said speed 10 for 1 s would give ~0.4°; measured,
speed 10 for 2 s moved **0.018°**, about 0.009°/s. A factor of **800 in rate for a factor of 20
in speed**, with nothing usable in between. Devon supplied the missing context afterwards:
"joystick has two modes, fast and slow, and I left it on fast" — the API presets are 1000 and 10.

The 31° error then broke `start_scan_planet`, which searches around the current position, and the
clear-sky window closed.

In the same episode a hill-climb on sky brightness rose 28.06 → 38.36 over eight iterations while
travelling ~0.15° in total, with the scope **82° from the Sun**. The signal was cloud thinning,
not approach. Never hill-climb on a signal whose environmental drift is comparable to the effect
of your step — the loop reports success while going nowhere.

---

### M-23 — At night the edge is a persistent, unrecovered drop — and only negative steps are roughness

The day model — two brightness levels — fails at night twice over (M-12's consequence, measured
2026-08-06): lit terrain patches overlap sky levels across altitudes (terrain at alt 10 read
brighter than sky at alt 60), and under light pollution the sky is not a level but a smooth
gradient brightening toward the horizon, accelerating as it goes (+48 counts per step at alt 55,
+192 by alt 30). Three rules came out of live columns breaking their absence, one column each:

- **Only negative steps are roughness.** Bounding step magnitude called two textbook edges
  "blocked" because the real skyglow gradient exceeded the bound. Sky never darkens 8% in a step;
  it brightens without limit.
- **An edge's drop is never recovered.** A single dark sample 52.5° up an open-looking column
  fired as an edge with the real answer 40° below; below a true horizon everything is terrain, so
  any later sample climbing back over the old sky level marks the dip as a transient — a bird, a
  wisp, one bad frame — however many samples it lasted (M-02 at night).
- **The boundary of the data is not the boundary of the sky.** A drop on the final sample has
  nothing after it to confirm persistence and must never fire; one such called an edge at 3.75°
  under an 18°-higher horizon lit by glare.

A glare column — median dragged above the sky trend by a streetlight inside terrain — is
indistinguishable by shape from a genuinely open one. It reads "open", which carries no
constraint, so the honest ambiguity costs the fit nothing (M-19).

### M-24 — Overhead wires read as sky through the segmentation — and the unguarded number is `top`, not the skyline

Measured 2026-08-07 on a 15-frame yard set crossed by mains and service drops, run through
`mosaic --segment` → `skymask`. The policy question came first: wires are largely not worth
avoiding with a telescope, so wires-as-sky is the *desired* reading, not a defect.

The segment backend already delivers it, by an accident worth knowing about: SegFormer's
processor resizes every frame to its 512 px input before inference, and at that scale a wire
is sub-pixel. Of 99,997 dark pixels in covered open sky across the whole panorama, 99,949
came back class 2 (sky); the blocked map held **2** genuine wire pixels, and they hit none of
the 360 mask columns. No filtering was needed, and none was added.

Where the exposure actually lives, found by trying to break it:

- `horizon_rows`' run-of-6 rule fully guards `first` — under a wire-removal filter, **zero**
  columns moved. But the mask records `band["top"]`, which fires on the first blocked pixel
  anywhere in the column with **no persistence requirement**. `top` is safe today only
  because the segmentation never hands it a wire pixel.
- The **heuristic backend has no such luck**: a wire is dark and neutral, so it reads as
  terrain (750 of those same pixels). A heuristic-backend mask of a wired yard will hang
  columns on the wires — the Raspberry Pi path is the vulnerable one.
- Per-column thinness is the wrong wire test. A first attempt reclassified any isolated
  blocked run ≤4 rows as sky, and it ate a house eave tip and a branch tip — both thin in
  their own column, both attached to a parent mass a degree or two over. The discriminator
  that works is *thin AND no thick blocked mass within ~2° of azimuth / ~0.75° of altitude*:
  a wire spans tens of degrees with nothing solid near it, an eave tip does not.

**Do:** prefer the segment backend for any site with visible wires. If the heuristic must
produce the mask, filter thin runs by proximity-to-mass before `horizon_band` — not by
thinness alone. And any future guard on `top` gets its mutation test against `top`, not
`first`; the two are protected by different rules and only one of them currently has one.

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

### F-19 — To test whether a forward model is exact, evaluate it at known-true parameters

Do not fit. Build a synthetic case, place it with a *known* rotation through the forward model,
then ask what residual comes back **at those exact parameters**. Zero is the only passing answer.

The rotation model claimed exactness and was not: at 12° of tilt the residual at the true
parameters was `rms 0.556, max 1.289`, scaling cleanly with tilt (1° → 0.055, 3° → 0.143,
6° → 0.248). After the fix, `rms 0.000, max 0.001`.

Two fixture traps surfaced in the same pass. The pre-existing round-trip test generated its data
with the buggy convention, so it *pinned* the defect (see E-02), and it used `cos(2a)`, making
yaw ambiguous mod 180. And a pure-sinusoid test horizon is exactly degenerate with a tilt — a
tilt about a horizontal axis adds precisely one sinusoid in azimuth — so the fit returned yaw
65.5 against a true 40.0. That was a bad fixture, not bad code.

A converging fit proves nothing about model exactness, and a fixture whose structure is
degenerate with the parameter under test cannot detect an error in it.

### F-20 — A stability stopping rule needs information-ordered additions

Adding columns in measurement order gave yaw 131.38, 133.44, 133.38, 135.00, 135.00 — a spread of
1.62° over the last four additions, still moving when the data ran out. Adding the same clean
edges in D-optimal information-gain order gave **135.81, 135.81, 135.81, 135.88 — spread 0.06°,
stable at seven columns**.

Front-loading the columns that constrain rotation means the later additions are exactly the ones
that *should not* move it, which is what makes the test mean something rather than reflecting
exhausted data. Devon set the criterion: "we are looking for stability, the fit stops changing."

Companion trap seen repeatedly: RMS was 0.03 at n=4 and *rose* to 0.55 at eight edges. **Four
points fitting four parameters is interpolation** — the residual is meaningless until there is
redundancy, and reads near zero however wrong the answer is.

### F-21 — Information about a parameter lives only where the data has gradient

For the 3-parameter rotation, `∂alt/∂yaw = −dH/daz`. So a column in a flat stretch of fence tells
you almost nothing about yaw, while pitch and tilt want even coverage — and the D-optimal
determinant balances the two automatically.

The consequence is counterintuitive and it is the project's biggest single efficiency win: **a
jagged skyline is an advantage and a flat, open one is the hard case.** Devon's site converged at
**seven columns in about twenty minutes**, against a 36-column blind circle that took most of two
nights and handed back ten fabricated columns. Offline replay against the completed circle showed
4–6 seed columns already landing within ~3° of the full answer.

Devon proposed the whole scheme: "we can start with 3 at 120, 4 at 90 or 6 at 60… match the
photo, then calculate which new column will reduce the error the most… repeat until we are
certain the yaw won't move." It maps 1:1 onto computerised adaptive testing — item information ↔
Jacobian row, stop when SE(θ) stabilises ↔ stop when yaw stops moving.

A completed brute-force dataset is the free simulator for validating an adaptive scheme offline.

### F-22 — Measure a model correction's effect on the clean input set

Fixing the rotation model and refitting reported "yaw moved **+1.38°**", 135.81 → 137.19. That
number went into a commit message, a PR description, a review reply, and a session summary.

It was measured on the 18-column set that still contained the two dawn-contaminated columns. On
the curated 16, **the corrected model reproduces the published yaw exactly at 135.81°**. What
actually moved was pitch (2.54 → 1.94), tilt direction (165° → 180°), and n (12 → 16 for a
comparable residual).

Running a fix against a set that still holds known-bad data attributes the contamination to the
fix. Re-derive on the curated set before publishing the delta. The reassuring outcome here is
that the headline number survived a genuine defect in the model that produced it.

### F-23 — Multiplying a robust loss by 1/σ² is not inverse-variance weighting

`weight /= uncertainty**2` applied as `w * huber(r, delta)` looks like inverse-variance weighting
and is not, because `delta` is a threshold on the *residual* and stays fixed. Worked against the
real `_huber`: at r=10°, σ=3, δ=4 the code gives **3.556** where the standardised form
`huber(r/σ, δ)` gives **5.556** — a 36% gap. At r=2, σ=3 both give 0.2222 exactly.

The two forms agree in the regime you test casually and diverge exactly where robustness matters —
large residuals on high-σ vegetation columns, which is the case the weighting was added for.

Scale must enter *before* the threshold. And keep the two concepts separate: **weight is how well
the edge was detected; sigma is how far the thing detected may move.** Corollary when refactoring:
standardising the input silently moves the breakpoint — extracting `objective()` changed δ=4 from
4° to 12° for σ=3 columns. Re-derive every constant expressed in the old units, and pin the
identity case (σ=1 everywhere) against the pre-refactor formula.

### F-24 — Floor a weight at the instrument's resolution, not a numerical epsilon

`max(float(uncertainty), 1e-6)` was added to avoid a divide-by-zero. With a fiducial at σ=1e-6
carrying a mere 0.5° residual, `objective()` reads **399998.4** at the "four good columns exact"
solution against **0.1** at the "sacrifice the four good columns by 0.5° each" solution. The
optimiser will always pick the latter — one observation buys the whole fit, which is exactly what
`orient.py`'s own docstring forbids, reintroduced by the guard meant to make sigma safe.

Shipped as `MIN_SIGMA_DEG = 0.1`, the mosaic's real resolution: nothing may claim more certainty
than the instrument delivered. An epsilon chosen to avoid a numerical problem is not a statement
about the world, and wherever a weight is 1/σ it will be read as one.

### F-25 — A score is comparable only when everything except the thing under test was held fixed

Two instances. A "refinement" compared phase-correlation sharpness across *different resamplings*
and moved a photo's solved azimuth from 20.8° to 326.9° for a gain inside the noise — resampling
changes the peak statistics, so the comparison was invalid and the visually verified solutions
stood. And after removing a misregistered frame the fit to the telescope got *worse* on paper,
2.08° → 2.50°, which was correctly declined as a regression: the 2.08 came from a mosaic
containing a misplaced frame plus three more frames of coverage, so part of it was fitting noise.
The trustworthy number was the solver residual, four-fold better at 10.35 → 2.51.

Changing inputs, resampling, or degrees of freedom changes the metric's own distribution.

### F-26 — Dropping under-constrained inputs is iterative

Removing the frame with zero control points changed what `cpclean` kept, which exposed two more
weak frames at 1 and 4 points. Dropping those too: 19 → 16 frames, control points 350 → **422**,
solver RMS 10.35 → **2.51**.

Robust estimators are coupled to their input set — one bad member distorts the consensus that
decides which other members look acceptable. Re-run the check after every removal until it
reports clean, and log every drop.

### F-27 — Detrending and outlier-robustness are different properties of a noise estimator

`find_edge` estimated noise from mean absolute *consecutive* differences, so under light
pollution the smooth skyglow gradient counted as noise — az 300 gave step 19.9 against noise 9.44,
SNR 2.11. Switching to second differences gave noise 0.75 and SNR 26.58, and five failed columns
"recovered".

It was refused rather than shipped: az 350's recovered "edge" was a step of **3.7 counts against
a sky of 19.7** — a gradient wobble, not a horizon. Then the *median* of second differences broke
a regression test built from a real auto-exposure scatter profile, because the median ignores
large excursions and in a pure-scatter profile those excursions **are** the noise. With a proper
RMS second-difference estimator az 300's noise came out 8.2 against the original 9.44 —
essentially unchanged. The fix recovered nothing, and the over-claim was retracted on the ticket.

Keep a regression test built from a real pathological profile; it is what catches a "fix" that is
merely worse in the other direction.

### F-28 — Verify both arms run the current version of every shared component

Mode B was reported as intrinsically worse than Mode A. Devon sent an image: "no wonder the phone
panorama fails, JFC, look at this boundary." Mode A had been upgraded to SegFormer; **Mode B was
still running the old brightness detector.** Segmenting the panorama in 8 tiles — needed because
it is 6:1 and would distort squashed to a square input — moved fit RMS 2.51° → **2.24°**, usable
columns 56% → **78%**, azimuth answered ~56% → **76%**.

"That boundary was my error, not the mode's — I'd been reporting a fixed defect as an intrinsic
limitation," and designing around it. A related counterintuitive result followed: the *wider*
panorama was the worse input, because it was framed low and clipped the rooflines — 356° span
with 16% sky gave 65% usable columns, against 346° with 60% sky giving 78%.

### F-29 — A detection that assumes correct pointing cannot be evidence about pointing

A daylight Moon "detection" reported disc excess +0.449% of sky at **320σ**, with a radial profile
falling from centre and a centroid 0.063° from field centre. That evening's direct solar image
established the mount carried a **3.2°** error at the time, so a 0.5° Moon was never inside a
1.28° field. What read as a disc was residual gradient structure between frames.

The same object had earlier produced the opposite error: a within-frame disc-versus-annulus test
returned a confident *negative* because the fixed instrumental gradient dominated a 0.45% signal.
Differencing between frames cancels that gradient; the naive within-frame test is worse than
useless because it returns a confident wrong sign.

When a measurement's validity depends on the quantity it claims to measure, a large sigma is
circularity with error bars. An image of the target beats an inference about the target.

---

### F-30 — Register the prediction before the measurement arrives

A ~10° yaw discrepancy between a night fit and the photo reference had a seductive explanation:
the wedge was knocked ~8° out of alignment two days earlier. The hypothesis was REGISTERED on the
ticket — with both outcomes spelled out — before Devon plate solved. The solve said 2.1°: refuted,
cleanly, with no room to absorb the result into the story. The columns then got re-examined as
detector problems, which they were.

Corollary from the same night: comparing a measurement at az X against a reference at az X±5 is
only valid where the horizon is flat. At a 6°-of-altitude-per-degree-of-azimuth tree edge, 5° of
azimuth slack permits 30° of legitimate altitude difference — an expectation check without
gradient-aware tolerance manufactures disagreements exactly where the horizon is most
informative (F-21's dark side).

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

### I-08 — Do not carry an instrument's guess at a quantity it cannot measure

At 250 mm focused at infinity the hyperfocal distance is `f²/(N·c) ≈ 4.3 km`, and nothing at this
site is further than a house across the street — three orders of magnitude inside the limit. That
is what forced the split: **the scope measures altitude, the photos identify what.**

The bug this produced: the mask's `type` field came from the scope's `obstruction_type()`, which
infers vegetation from green and yellow colour in a 250 mm blur taken after dark, so after sunset
it labels essentially everything `structure`. The residual table duly showed all four large
disagreements as "structure" while Devon, reading the photo labels, counted six of nine as trees.

Once an instrument provably cannot determine a quantity, stop storing its guess at all — a
plausible field is consumed downstream exactly like a measured one.

### I-09 — In EQ mode the frame comes from the mechanics, not from stored software state

**Corrected 2026-08-05.** This entry previously said a power cycle wipes the mount's model and
prescribed re-syncing before any measurement. That is wrong for this hardware, and acting on it
cost an observing session.

How the Seestar actually works in EQ mode, per Devon: *"the Seestar plate solves and then asks you
to fix the problems, it expects in EQ that it's within one degree of the right angle and 1 degree
of pointing north."* The wedge is physically aligned to the pole and RA/Dec follows from the
encoders relative to a base assumed correct. **The plate solve is a check that tells the operator
to adjust hardware — it is not a stored calibration.** So there is nothing to lose across a power
cycle, and no re-sync is required before measuring.

The evidence this entry was built on says so too, read properly. After a power cycle, gotos to the
Sun's ephemeris found nothing and the true error measured **8.150°**. That was not a lost software
model: the wedge was genuinely that far out, from the previous afternoon's adjustment loop (M-01).
It **survived the reboot precisely because it was mechanical**.

What still stands, and is the part worth keeping:

- **Never read a goto residual as a pointing error.** The mount reported arriving within 0.14° of
  where it was told while showing no disc at all. Landing on the commanded coordinates only proves
  the mount executed the slew in its own frame; it says nothing about whether that frame matches
  the sky. This trap was written down here and then walked into again a day later.
- **Precise commands in an unvalidated frame are precisely wrong**, and the failure looks like
  "search harder" — 49 grid positions over ±1.65°, a 16-position altitude sweep, and a brightness
  hill-climb all found nothing, because the search box was in the wrong coordinate system.
- **Check the mechanics, not the software.** When pointing is wrong, the question is whether the
  wedge is within its tolerance, and the answer comes from a plate solve or an absolute sighting —
  not from re-issuing a sync that nothing persists.

### I-10 — Driving the error at one sky position to zero is not alignment

A closed loop on the Sun ran all afternoon: 3.045° → 0.865°, with a screw calibration that looked
beautifully linear (0.456, 0.452, 0.415 °/half-turn, agreeing to under 1%). That evening an
8-minute drift run gave 0.87°/hour, implying polar misalignment ≈ **3.3°** — essentially where the
day started, and where July's plate solve had put it.

A single sky position cannot distinguish "the axis is tilted" from "the frame is rotated", and the
metric responds to the axis through an unknown, position-dependent geometric gain (≈0.5 here). So
the per-turn constant was calibrated against the wrong quantity, and ~8½ half-turns of *observed
error* correction swung the axis through zero to roughly −4.5°. Devon caught the consequence:
"wait, are we off by 3.3 in the other direction now?"

A feedback loop converges beautifully on whatever you measure. Drift, or two widely separated
targets, responds to polar error directly — which is why every real alignment procedure uses it.

### I-11 — Check whether a sensor's geometry can express the quantity at all

One magnetometer reading gave 275.59° magnetic against 282.69° true — a 7.10° difference against
Denver's ~7.4°E declination, agreeing to under a degree. Six pointings across 150° of azimuth then
gave implied offsets of +15.8, +29.2, +44.6, +48.8, −23.2, −37.2 — an **86° spread**.

The structural cause: `gz` held constant to 0.005 across pointings spanning 90° of azimuth and 15°
of altitude, at 0.646 against the 0.640 expected for a vector along the polar axis. **The sensors
are in the base, not the tube**, so the assembly is blind to the declination axis. No amount of
fixing makes a one-axis sensor report a two-axis quantity.

Referenced against gravity instead — both fields are world-fixed, so their rotations must differ
by a constant — the residual inconsistency is about ±11°. Also note the device's stored
hard/soft-iron calibration is not optional: raw horizontal magnitude spanned 43–133, a factor of
three; calibrated, 114–125.

One agreeing sample from a direction-dependent sensor is a coincidence. Establish the geometry
before characterising the accuracy, and reference a sensor against a second physical invariant
rather than against your model of the world.

### I-12 — A fixed terrain edge is a free session-to-session pointing reference

The same commanded pointing 40 minutes apart, with an app session in between that took the
control connection: edge angle −48.1975° → −48.2927° (Δ0.095°) and edge row 874.32 → 830.62 px
(**Δ0.029°, 1.7 arcmin**), against a measurement floor of 0.026° in angle and 0.007° in position.
The angle change is largely parallactic and computable, leaving ~1.7 arcmin of pointing residual.

Sensitive from about 0.01°, useful to roughly half a frame (~0.6°) before the edge leaves the
field, after which it degrades to a binary alarm — which is still exactly the alarm that was
missing when 3.7° of drift went unnoticed for days. No stars, no Sun, no clear sky, no filter.

### I-13 — Per-axis tolerances are unsatisfiable at a coordinate singularity

Two live sweeps died identically: `goto did not arrive within 90s (wanted RA 16.763 Dec 89.89, at
RA 14.716 Dec 80.65)`. At this latitude **az 0 at altitude ≈ 39.8° is the celestial pole**, so
scanning north walks Dec to 89.8 and the arrival test — comparing raw RA — is degenerate. An
arcminute of error swings RA by hours, so arrival can never be detected.

The first fix was aimed at the waypoint router and was wrong; the failure was in the target
itself. Two changes: compare true angular separation (`ang_sep < 0.6°`) rather than per-axis
deltas, and nudge az 0 → az 3.0. **The azimuth is nudged rather than the altitude because
altitude is the quantity being measured**, and 3° of azimuth is well inside the mask's own
resolution.

When you must perturb a target away from a singularity, perturb the axis you are not measuring.

### I-14 — There is no still-image path for terrestrial frames; RTSP is not a choice

Asked the reasonable question "why use video for a column when we only need one still?", the
answer turns out to be that the device offers nothing else.

There *is* a second imaging channel on **port 4800**, separate from RTSP on 4554: connect, send
`{"id": 21, "method": "begin_streaming"}`, then read an 80-byte header (first 20 unpack as
`>HHHIHHBBHH` → size, id, width, height) followed by exactly `size` bytes of raw uint16 —
`w*h*6` for RGB16, `w*h*2` for Bayer GRBG16. Being a plain TCP transfer it would be strictly
better on a lossy link, since loss costs latency and the frame still arrives, whereas one lost
packet destroys an H.264 keyframe and ffmpeg emits nothing at all.

**It does not work in scenery mode.** Tested live: the connection succeeds, `begin_streaming` is
acknowledged (header id 21, size 4), and then no frames follow — a 60 s wait returned nothing.
That matches the source: this channel serves the preview and stacking paths, which belong to star
mode, while scenery publishes only through the RTSP encoder.

So every robustness measure for column work has to be built around a video stream that fails
all-or-nothing, which is what makes terminus-15 (stop_view does not reliably stop) and the
frozen-frame case expensive rather than merely annoying. Worth knowing for plate solving, though:
star mode *does* feed this channel, so a solve path could read frames here and be immune to the
RTSP failure modes entirely.

---

### I-15 — The scenery stream is blind at night; star mode sees, on a different channel

Measured 2026-08-06, Bortle 8, Sun at -12: the scenery ISP pins exposure at ~30 ms and gain at
112.5 whatever `set_setting` asks (every request from gain 5-400 and 30-1000 ms read back
identical), and 16-frame stacks of known sky versus known terrain differed by 0.02 counts in 255 —
with terrain the brighter. No statistic fixes a channel with no signal (F-08's sensor-level twin).

Star mode exposes for seconds but kills the RTSP stream. Its frames arrive raw on the imaging
channel (port 4800, `begin_streaming`, 80-byte headers, raw16 at 1080x1920): 2 s exposures
separate skyglow from terrain at >100:1. Use the MEDIAN per frame — hot pixels and streetlights
are bright outliers inside terrain and the mean follows them (M-12) — and drain the stream
through one full exposure after arriving at a pointing, or the counted frame was exposed at the
previous one.

### I-16 — Near due north the mount cannot converge, and failed gotos escalate to connection resets

Due north at altitude near the site latitude is the celestial pole (dec = 90 − |alt − lat|), and
a column walk at az 0 crosses that band mid-column: the mount reaches the right Dec and RA never
converges (7° short after minutes). Worse, a burst of failed near-pole gotos made the scope RESET
every connection it held — control and imaging — twice in one night, and a dead imaging socket
then failed every subsequent column in seconds until reopened. az 20 also produced an
11.5-hour RA jump between adjacent altitude samples, pier-flip-shaped and not yet understood.

Skip the pole-band SAMPLE inside a column (one altitude is recoverable; a column is not), exclude
near-north candidates at night outright, and treat imaging-socket death as reopen-and-retry-once,
never as a column verdict. Daytime sweeps have measured az 0 successfully; the difference is an
open question.

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

### SAFE-04 — An operator's approval is permission, not evidence about a computable quantity

Devon: *"sun is low, and there's heavy cloud cover so heavy that I can't tell where the sun is, so
you have my approval to start."* Preflight returned `Sun: az 272.8 alt 24.3` at 17:55 with sunset
at 19:57 — the Sun was 24° up, due west, two hours early.

Note the structure of the failure: **the cloud that made it feel safe was exactly what removed the
ability to check.** The guard computes the Sun from time and site, so cloud does not weaken it —
but it does mean the human can no longer eyeball the tube's relation to the Sun, so the guard is
carrying the safety alone, which is precisely when it should be trusted least on its first outing.

Conditions that degrade human observation — cloud, darkness, distance — systematically inflate
confidence. Compute the quantity; take the approval as permission to proceed, not as data.

### SAFE-05 — A safety-critical rewrite gets its first live test supervised and minimal

A 3-hour daytime sweep was queued as the first run of a rewritten Sun guard that had never
executed with the Sun above the horizon. Devon stopped it: *"wait, no, let's not risk another pass
that might go right past the sun again, we'll wait until twilight."* The agreed sequence became
mount → confirm polar alignment → read-only preflight → one supervised point → then sweep.

Separately, his question "oh, do you want me to put the telescope on its mount?" caught that the
scope was not on its tripod at all — preflight had reported `az 180 alt -39.9`, a parked tube, and
the code was happy to sweep anyway. The instrument's physical configuration was a precondition no
software check covered.

The riskiest possible validation of new safety code is the longest unattended run of it.

### SAFE-06 — Never design a workflow that requires a human to look at the Sun

After blind searches failed, the proposed division was "the human does coarse acquisition — you
can see the Sun, and no sensor on that mount can." Devon: *"no, human doing coarse correction
sucked and I still see trails, I don't want to do that again."*

That is the operator's eyes paying for a design decision. The replacement uses a signal that never
needs the disc in frame: filtered sky brightness reads 22–24 far from the Sun, 27–28 within a few
degrees, 32–40 closer — a monotonic gradient over tens of degrees, hill-climbable in 5–10° steps
inside a box bounded by the accelerometer and compass.

"The human can just look" is not an acceptable fallback for a hazard whose damage is to the human.
A design that offloads an unsafe step to the operator has not solved it.

### SAFE-07 — A hazard that cannot reach the optics is not a hazard

A sweep finished early having skipped ten columns, refusing paths with the Sun at `az 297.2 alt
−4.7`. The Sun was **below the horizon** and the guard still refused, because a column extends to
alt 0 and −4.7° is within 30° of that. A third of the data lost to a guard that was wrong, not
merely conservative.

Fixed with `SUN_SAFE_ALT = -3.0`, with the margin chosen for the physics that bends it —
refraction lifts the Sun about 0.6° — and tests in both directions so the guard still refuses when
the Sun is genuinely up.

A safety predicate expressed purely as distance-to-hazard ignores whether the hazard is
physically reachable, and will quietly veto valid work.

### SAFE-08 — A command that lies can also act later

With the tube on the Sun, `scope_move_to_horizon` returned `{"result": 0, "code": 0}` and RA was
unchanged — no motion, at the worst possible moment. An explicit goto through `point_to` cleared
it, separation growing monotonically 30° → 58° → 70°.

Later the mount was found at az 0.24 / alt 30.34 **with nothing having commanded it** — plausibly
the earlier park finally acting, having reported success and done nothing at the time. That is
worse than either failing or working, and verify-by-readback does not catch it.

Confirm a park or abort by position *over time*, not once. And log the power state with every run:
Devon's "telescope was at 6% power anyway, needs charging so I shut it off" is a plausible common
cause for a class of impossible behaviour, and terminus reads battery state nowhere, though
`seestar_alp` watches it for safe shutdown.

### SAFE-09 — Over-the-top Sun avoidance is unavailable near summer solar noon

`point_to` falls back to routing over a high waypoint when a direct slew would pass the Sun, on an
assumption stated in its own comment: "the Sun is never at high altitude from a mid-latitude site."
At 39.79°N in early August the Sun reaches **alt 67**. The candidate waypoint altitudes are
`(min(85, max(alt,70)), 75, 65, 55)` — the last three sit at or below the Sun's own altitude — so
every candidate is rejected and the whole strategy is unavailable for several hours a day, exactly
when daylight column work happens.

Measured with the same path sampler, then flown:

| route | leg clearances (°) | |
|---|---|---|
| direct to (340,70) | 12.8 | refused |
| (40,10) (0,12) (340,15) (340,40) (340,70) | 58.4 · 75.8 · 98.1 · 78.9 · 50.7 | **clear** |
| (300,10) (320,20) (340,40) (340,70) | 58.6 · 99.4 · 78.9 · 50.7 | **clear** |

**Going under works when going over cannot**, because the Sun's altitude bounds the problem from
above but never from below. Two further notes: the low route needed three intermediate stops and
the code tries exactly one; and my own pre-check computed separation for each *position* in the
column, got a comfortable 54.2° minimum, and concluded "safe" — positions are not paths (SAFE-01).

Report the strategy that failed, not just the refusal. "No Sun-safe path" gave no hint another
route shape existed; "over-the-top routing needs the Sun below about 50°, it is at 57" is a
decision the operator can act on.

---

## Code, tests and the repo

Most of these came out of adversarial PR review. The recurring shape is a change that is correct
in the place you were looking and absent everywhere else, or a test that passes for a reason
unrelated to the thing it names.

### E-01 — Mutation is the standard of proof

A green suite proves nothing about a specific guard; only breaking that guard does. Revert the
fix in a throwaway worktree, confirm the *named* test fails, restore, confirm `git diff` is clean.

It repeatedly caught tests asserting less than they appeared to: "the three-library test passed
against `return False`; the fallback test passed against hardcoding auto to heuristic." A
reviewer proved one round's fix was unguarded by **deleting it entirely and rerunning — 170/170
still passed.** Removing dedup from `sorted({int(round(a)) % 360 ...})` survived the full suite.
A test rewrite that swapped `len(head) == N` for substring checks silently lost header-bloat
coverage, proved by inserting one unrelated header line and watching both tests still pass.

Two cautions. A mutation that produces a collection or syntax error is a **null result, not a
pass** — removing a whole `except` block leaves a bare `try:`. And verify you mutated what you
think: an editable-install `.pth` pointing at the original checkout meant a reviewer's worktree
mutations were never imported, and the first run "passed" for the wrong reason. Print the
`__file__` of the module you believe you changed.

### E-02 — A test can pin a bug instead of catching it

`_rotate_east` rolled the texture the wrong way and the PR's own test asserted the wrong
direction, locking it in. Mutation-checked: flipping the sign to the correct one made that exact
assertion fail.

The right answer came from the convention, not the code: `orient.native_column` defines
`phi = (target_az - yaw) % 360`, so `true_az = native_az + yaw`, so a marker at native 0 with yaw
90 belongs at true 90 — columns 16–23 of a 64 px image, not 48–55. The failure was invisible by
eye: "for a small yaw that still looks like a horizon, just not this one."

A test written by reading the code under test asserts only that the code does what it does. **A
test that locks a bug in is worse than no test at all.**

### E-03 — An invariant is not pinned if the code that must obey it is unreachable

After fixing the Huber weighting (F-23), a test asserting the standardisation property was added.
Its own mutation — reverting to scaling the loss — **survived, 123 passed.** The test checked the
property in isolation and never pinned that `fit` applied it, because `cost_from` was a closure
inside `fit`. Extracting `objective()` to module level made the same mutation fail.

Testing a re-implementation of a rule proves the rule, not the code. If the production expression
of an invariant is only reachable through a closure or a long pipeline, refactor until a test can
call it directly.

### E-04 — A test that asserts what the dev box happens to satisfy passes vacuously

Three instances in one session. `test_optional_backends_report_rather_than_raise` gave zero
coverage because torch and transformers are installed locally, so the `except ImportError` branch
never ran — mutating it to `raise` survived all 88 tests, and CI installs neither, so the blocker
"could be deleted outright with the test still passing. It only ever meant anything on this
workstation." Fixed by injecting `types.ModuleType` stubs and asserting both directions.

Then CI caught what no local run could: `terminus export` demanded a `config.toml` it never reads —
"my checkout has one; CI doesn't."

A claim about the environment must be tested by *constructing* the environment, not by inheriting
it.

### E-05 — Exception type cannot separate "the environment is short something" from "we have a bug"

A fallback that degrades gracefully was made to re-raise `TypeError, AttributeError, NameError` so
it would not swallow programming errors. The reviewer killed it: the segmenter calls transformers'
API directly, so a version-skew break — the exact scenario the code's own comment assigns to the
degrade side — raises `TypeError`. Asymmetric the other way too: `KeyError` and `IndexError` are
equally bug-shaped and still degraded.

The catch went back to broad and the fix moved somewhere it can work: **record which backend
actually ran.** That immediately exposed two worse bugs — `cmd_skymask` wrote the *requested*
backend into the mask meta, so a fallback produced a durable artifact claiming `backend: segment`
that the heuristic made; and fixing that revealed the CLI pre-resolved `auto` → `segment`, turning
it into an explicit request that `sky_mask` is correctly required to fail loudly on. **The CLI was
defeating its own auto path.**

Classifying failures by exception class is guessing. Report and persist the path that executed.
And pre-resolving a policy value upstream silently disables the policy downstream.

### E-06 — An absent value is not a matching value, and a fabricated default is a claim

Three instances. The merge-position guard only fired when the prior mask carried `lat`/`lon`, so a
London sweep merged into a positionless mask with no error — and the merged meta still had no
position, propagating the hole forever. `write_landscape` defaulted latitude and longitude to 0,
"so a mask that never recorded a site produced a `landscape.ini` placing the observer in the Gulf
of Guinea and saying nothing was wrong"; altitude was dropped entirely because 0 m is a claim
rather than an absence. And `boundary_model("")` silently returned the structure model — but an
unnamed column is most often a tree measured at night, so the fallback pointed the wall rule at
precisely the columns the vegetation rule exists for.

Recorded deliberately as a counterpoint: the per-column Sun read falls back to the *last known*
altitude rather than `None`, because `None` means "the caller did not say", which would restore
the daylight assumption at the worst moment.

Missing data must propagate as missing — not as zero, not as a permissive default, and never as
"no mismatch detected". Choose the fallback by which wrong answer is dangerous.

### E-07 — A guard in one caller is not a guard

A PR claimed unoriented masks were refused "in the header, in meta.oriented, on stdout, and by
refusing to export". The reviewer reproduced the opposite: the exporters had zero gating, and
`terminus export photo_mask.yaml` produced a `.hrz` headed "true-north azimuth" carrying
panorama-local values. The only refusal was a print inside a command that never calls export.

The fix then covered **two of the three** exporters the finding had named — in a change whose own
comment says a guard in one caller is not a guard. The miss was the worst one: the Stellarium
format forbids comments, so unlike a `.hrz` header that file **cannot carry a warning at all**.

Put the check where every path converges, enumerate the call sites, and re-read the finding's own
list before declaring it fixed. Related shape: hardening only the code you just wrote leaves the
sibling path broken and looks complete — an `except OSError` wrap was added around the new picture
exporters while plain `terminus export` still traced back on the identical failure, and the test
could not even reach the new code. **If a test cannot exercise the guard, that is evidence about
placement.**

### E-08 — A half-written multi-file artifact reads as corrupt, not absent

`write_landscape` wrote `horizon.txt` before rendering the texture, so a mid-way failure left a
directory Stellarium reads as a *broken* landscape — the failure looked like a corrupt install
rather than an error. Building everything in memory first fixed the render phase, but the write
phase was still three sequential `open()` calls, and a failure on the last leaves the first behind.

Cleanup now removes only files this call created: "a landscape the person already had isn't ours
to delete, though our own half-written files inside it are." A stale `maptex.png` is also removed,
on the grounds that the harm is to the person who opens the folder, sees a photograph, and
reasonably concludes it is part of the landscape.

Partial success is a worse failure mode than none, because consumers cannot distinguish it from
corruption.

### E-09 — Two silent coercions: a string is iterable, and `{:g}` emits letters

`skipped_az: "190"` — hand-edited, which the mask's own docstring invites — iterates to the
characters `1`, `9`, `0`, recording three wrong columns as skipped **with no error at all**. A
reviewer had reported it as a crash; the reality was worse, wrong-and-silent rather than
loud-and-wrong.

And PVsyst's `.HOR` spec says "all lines containing text are considered comment lines", while
`format(v, 'g')` produces `1e-05`, `nan`, `inf` — so a non-finite altitude would be silently
dropped as a comment rather than rejected. Guarded at the choke point so every exporter refuses
non-finite altitudes.

Validate iterables by type before iterating, and never emit a numeric format that can produce
alphabetic output into a format whose comment rule is "contains text".

### E-10 — A threshold in degrees is not a threshold in distance, and longitude wraps

One degree threshold was compared against both latitude and longitude. At lat 39.79, 0.0003° is
**33.4 m** north-south but **25.7 m** east-west — the east-west test 23% tighter than intended,
and the docstring's "~30 m" true of one axis only. Proof it was untested: dropping the longitude
check entirely survived the full 141-test suite, because no test moves purely east-west.

Fixed by computing metres against a named constant — after which the metres conversion turned out
to have no wrap, so two points ~2 m apart straddling the antimeridian computed as **39,466 km**.

Any tolerance in angular units is a different physical tolerance per axis and per latitude, and
any longitude difference taken by subtraction is wrong across ±180°.

### E-11 — When the raw data is not in version control, the fixture *is* the data

A regression fixture "wasn't a literal transcription — six of sixteen values off by up to 1.7
counts, which matters more than usual because `captures/` is git-ignored and nobody can re-derive
them." Fixed by re-reading from the saved frames and adding a programmatic check that the
transcription is literal.

Any paraphrase of unreproducible data becomes an unfalsifiable fabrication.

### E-12 — A human-editable safety flag needs a closed vocabulary and a typed error

Three iterations on one flag. `meta.get("oriented") is False` is an identity check, so
`oriented: 'false'` and `oriented: 0` walk straight past it — and the mask is documented as "the
durable, hand-editable artifact", so a *supported workflow* defeats the guard. Interpreting
strings via a negative allowlist then failed **open**: `"flase"`, `"nope"`, `"unoriented"`, `"n"`
all read as oriented. The fix is explicit true/false vocabularies and a raise on anything else,
with the writer calling the same function so the file's header and the export gate cannot disagree.

Then a fourth bug from the fix: the raise was a bare `ValueError` while `main()` caught only the
domain exception, so a typo gave a raw traceback while a legitimately unoriented mask got a clean
sentence — backwards, since the typo is the one with a five-second fix.

Both truthiness and negative allowlists fail open. And a new exception type must be catchable by
whatever already reports errors to the user.

### E-13 — Verify documentation by executing it

A reviewer that *ran* every command and snippet found three errors a stranger would have acted on:
"250 mm at f/4.9" when 250/50 is f/5 exactly; a dependency on `enblend`, which is invoked nowhere,
costing everyone who installs it; and "36 columns at 10° spacing" when the shipped default is 5°
→ 72 columns, contradicting a comment in `config.example.toml` in the same repo.

That last one is the dangerous shape: **the error is silent, because the sweep still completes and
still produces a horizon file** — just the wrong shape and twice the duration.

Verify each claim against the artifact that owns it, and treat a doc contradicting a config file
in the same repo as a defect, not a style nit.

### E-14 — Grep is not proof

Three code sites told users to run a command that does not exist. Two were found by grep; the
third "evaded my grep because the reference wraps across a line break", and surfaced only on a
third recurrence. The working technique joins lines first, masking the legitimate module reference
to avoid false hits.

The converse also nearly shipped: grepping `deadline` in `sweep.py` matched, making a ticket look
fixed, but all three matches were the goto timeout.

A negative grep proves nothing about wrapped or reformatted text, and a positive one proves
nothing about *which* mechanism matched. Read every hit before acting on a count.

### E-15 — Concurrent agents make shared-checkout operations unsafe

Three distinct losses in one project. **`git add -A`** run while a reviewer had a live mutation on
disk committed and pushed `# mutation: shadow sweep.classify`, and CI failed on ruff — its edits
are indistinguishable from yours at the staging step. **`git checkout --ours`** on
`.beads/issues.jsonl` during a rebase silently deleted a ticket: right for a file holding machine
state, wrong for an append-only log, "and it fails silently because a shorter log looks exactly
like a valid one." **`bd` allocating IDs from the working checkout** reused `terminus-32` when
another agent moved the tree across branches, losing three beads.

The fixes are structural, not procedural: isolate reviewers in `git worktree`s rather than timing
around them (which then introduced the editable-install trap in E-01); union-merge append-only
logs and never resolve them with `--ours`/`--theirs`; close the window between allocating an ID
and publishing it — "I committed **and pushed** in the same breath, so there was no window."

Two smaller ones worth keeping: when data loss is suspected, diff the whole ID set against a
pre-incident commit rather than restoring only the item someone noticed (37 issues against 48,
"exactly one missing, twelve legitimately added"). And `&&`-chaining let a failed `cd` skip a
`bd update` while the commit went ahead — "I pushed a commit whose message described two changes
and contained one."

### E-16 — When two instruments write one record, merge field by field

A targeted re-measure must improve a record, never degrade it. Three defects in one merge path:
`meta.update()` replaced rather than merged, so a 2-column patch run overwrote `skipped_az` and
destroyed the record of *why* columns were missing; `skipped_az` was recomputed from only this
run's measurements, so a column measured in patch 1 and Sun-blocked in patch 2 went back on the
skip list while the mask still held its altitude; and the column merge was a whole-record replace,
so re-measuring at 2 a.m. a column the photo had segmented as `tree` overwrote it with `type: ""`,
losing the seasonal buffer on export.

The rule that emerged: take the fresh altitude always, but **a fresh naming replaces an old one
and a fresh silence does not** — justified by which instrument can make that claim at all.
Conversely `clipped` *is* cleared by a real re-measure, because it means the altitude was a lower
bound from the photo and the scope's value is a real crossing.

Unconditional preservation is the mirror bug, so each rule needs a mutation test in both
directions.

### E-17 — Validate operator input before the instrument moves, and pin it clock-independently

Two halves, one incident (terminus-65). `--re-measure` was parsed where it was consumed, next to
the checkpoint load — after the view start, the channel choice, and the daytime anti-Sun seed
slew — so a typo'd azimuth list cost a real slew before it errored. And the test covering the typo
runs deliberately unpatched, which made it pass at night (the seed slew is skipped below −12°) and
fail in every daytime run: PR #31 merged green on night CI, and each daytime CI run afterward was
red — caught by a docs-only PR whose diff could not have broken anything.

Three rules emerged:

1. **Everything pure runs before the first scope call.** Flag parsing, checkpoint loading,
   validation — an operator typo must cost ten seconds at launch, never an observing window.
2. **A refusal that refuses nothing is itself a typo.** `--re-measure 342` against a checkpoint
   whose poisoned column is az 324 parses fine, matches nothing, and quietly re-serves the very
   verdict the operator meant to reject — so a redo set that matches no cached verdict is a typed
   error, not a no-op.
3. **A test that pins a hardware-free contract must fail on the contract, not on the sky.** Assert
   the scope mock saw no calls (minus the sanctioned `stop_view` cleanup), not merely that the
   error appeared — because the code path that spends the telescope can be conditional on the Sun,
   and then the test's verdict depends on when CI happens to run.

### E-18 — Prose asserts causes the data cannot support, and reviewers catch it late

The code in this project is disciplined about never recording an unevidenced value (M-19, E-06).
The COMMENTS were not, and the same defect in prose survived three rounds of review before it was
removed rather than re-fixed.

An incident narrative about a bad run was written into a docstring, the README, a PR description
and a memory note. Each round of review checked it against the record and found it wrong in a
different way:

1. First telling: "cloud produced 57 deg edges over a 15 deg treeline, and the collapsing sky
   reference is the tell." The treeline is 46 deg, and those columns were measured at the two
   HIGHEST references of the run — the collapse came later, against columns that read normally.
2. Second telling, corrected: "the bank sat above the treeline so the scan stopped at its lower
   edge; no statistic caught it." The frames show the recorded edge at the bank's TOP, and a
   statistic did catch it: the yaw spread was 17.06 deg against a 1 deg rule and the run never
   settled.
3. Third telling: cloud dropped as the explanation entirely. A night column two degrees away, with
   no cloud available, overshoots by the same 7.5 deg — and the residual is measured against a
   photo column selected by an UNSETTLED yaw, so the comparison is not independent of the thing
   being diagnosed.

What made it durable was that each version was *plausible* and partially true. The mechanism was
never checked end to end against the frames and the neighbouring columns; only the half that
supported the story was.

**The rule: a comment asserting WHY something happened is a claim, and carries the same burden as
a recorded value.** If the evidence supports the observation but not the cause, write the
observation and stop. "Three columns read 7-11 deg high and the run did not settle" needed no
mechanism to justify embedding the frames — the argument was always that the numbers do not
identify the fault, which is when a person looks at pictures. Reaching for a cause made the case
weaker, not stronger, because it could be refuted while the real argument could not.

Corollary for reviewers: check an anecdote's mechanism against the raw artifacts, not against its
own summary. Every refutation here came from opening the frames or joining the checkpoint to the
photo mask — never from reading the paragraph more carefully.

**The same defect applies to claims about your own diff, and it bit in the same PR.** A test
hardening was written by a script whose anchor did not match; the script raised, the surrounding
commands ran on regardless, the test suite went green — because that test had been passing BEFORE
the edit too — and the change was reported as fixed in a review reply while absent from the tree.
The mutation harness then printed `SKIP: anchor not found` for that very guard, twice, and it was
waved through both times as "checked separately".

Two rules fall out. A test that passes after an edit proves nothing unless it FAILED before it: the
mutation is the evidence, not the green run. And a skipped mutation is a failed mutation — the
anchor not matching means the code is not what you think it is, which is the same alarm as a
surviving mutant and deserves the same stop.

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

### D-12 — Fragility is a per-layer policy: brittle on interpretation, robust on execution

A review doctrine was imported from a batch-parser project largely unchanged, where "wrong data is
worse than no data" makes failing at the first error correct. Devon corrected it. terminus drives
hardware that is fragile by nature — mounts stall, streams freeze, sockets drop, cloud moves — so
the doctrine splits:

- **Interpretation** (observation → number) is **brittle.** Never record a value that is not
  evidenced.
- **Execution** (getting through the night) is **robust.** Retry, skip, degrade, continue.
- The bridge between them: **a thing that failed must be recorded as failed.** Skipped columns go
  into `skipped`; a ceiling-limited column becomes a bound; a no-edge column is a non-result with
  a stated reason.

"Robustness that quietly omits its failures is silent corruption with better manners." Concretely,
the unadapted doctrine would have argued *against* the fix it took PR #1 six rounds to land.

Importing a doctrine from a project with a different failure economy inverts its effect. State
which layer each rule governs, and require that every recovery leaves a record.

### D-13 — Don't re-derive an input the user has said is settled

Asked for an observing plan, the first version was built from the sparse telescope columns; then
the photo-derived boundary was judged "the right source", found never to have been saved as data,
and a full Hugin re-stitch was launched to regenerate it. Devon: *"man, come on, I've lost 10
minutes of telescope time on a boundary we more or less already knew, just give it a 5% buffer and
give me something to work with."*

The job was killed and the complete plan — five targets, the moonless window, moon separations,
framing notes — was produced about 45 seconds later from data already in hand. The re-derivation
was never needed for the deliverable.

When the deliverable is a decision aid rather than a measurement, precision already in hand plus a
stated margin beats regenerating the input from source. "We know what X is like" is an explicit
instruction that the input is settled.

### D-14 — After the second refuted hypothesis, stop theorising and go look

Six explanations for frames that would not sharpen were proposed and separately measured against,
across ~35 minutes of the remaining daylight: darkness, a north-facing wall in shadow, exposure
lock breaking autofocus, similar sky and siding colour, dew, coarse focus sampling, degraded
stream resolution. Two died to one-line facts that cost Devon nothing — *"it's still very bright
out, it's not even sunset, I can see the sun"* and simply *"it's 71f"*.

The fact that mattered had been stated at the start: *"right now it's pointed right at my house."*

The cheapest available observation — look at the image, or ask the operator what the instrument is
pointed at — outranks a measurement run you have to build. And an operator's free one-line facts
are the highest-value input available; solicit them early rather than spending the window
generating candidates.

### D-15 — A file at a canonical path is not evidence that it is current

`horizon_mask.yaml` at the repo root was read as the mask and a census computed over it — "six of
sixteen columns sit at exactly 35.0" — and an earlier result justified against its values. Its own
`meta:` block said `measured: 2026-08-02, az_step: 20, alt_search: [0, 35]`, superseded a day
later by a sweep at `az_step: 10, alt_search: [0, 60]` in `captures/`, where the disputed column
reads 32.5 rather than 12.5. The error surfaced only incidentally, from another agent's commit
message.

Read the provenance metadata every derived file carries before editing it or computing statistics
over it — especially when a second agent is landing work in the same repo. The real finding
underneath was better than the wrong one: az 0–60 has no current measurement at all.

### D-16 — Checkpoint attempts, not successes — and re-judge saved profiles on load

Chosen while fixing terminus-58, each half after the naive version failed the same night.

The first checkpoint prototype appended a line per SUCCESS; its very first crash came before the
first success and left nothing — the exact loss it was built to prevent. An append-only line per
ATTEMPT, with conditions (time, channel, exposure, verdict, profile), survives any crash and is
cheap to ignore.

On resume, the stored VERDICT is what an old judge thought; the stored PROFILE is what the sky
did. Re-judging profiles with the current detector turned two wrong verdicts into right ones with
zero re-observation minutes. Judge is code; data is data. The `--replay` feature is the same
principle wearing a different hat.
