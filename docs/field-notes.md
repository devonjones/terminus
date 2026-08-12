# Field notes

What running terminus against real sky has taught us, with the numbers. The
README tells a new user what to do; this file is why it says so. Newest lessons
ride pull requests into `LESSONS.md`; these are the ones a user (rather than a
developer) most needs to believe.

## The first end-to-end run found four bugs that 211 passing tests had not

2026-08-05: nineteen phone frames to a solved orientation, with the yaw
reproducing exactly across two independent runs an hour apart. That run also
found, in one evening:

- every mask a real sweep wrote was unparseable,
- the Sun-cone check tested only a column's endpoints, not the path between,
- the Sun guard could refuse to move the mount *away* from the Sun,
- a darkening sky manufactured false measurements.

All four are fixed, and the class of each has a regression test constructed
from the failure. The lesson that outlived the fixes: passing tests measure the
environments the tests construct, and the sky constructs different ones.

## Why `orient` stops on yaw stability and not on the residual

With four columns fitting four parameters the fit interpolates and the RMS
reads near zero however wrong the answer is. One fit reported **0.28° from four
edges** and was nearly believed. RMS is also blind to uniform bias, because a
constant altitude offset is absorbed into pitch.

So the run stops when the solved yaw holds still across refits, and if it has
not settled by the column budget it says so and marks the result provisional
rather than letting it read as converged.

A smaller fiducial set will almost always show a smaller residual whether or
not it is closer to the truth — a 7-column fit at rms 0.22 is not better than a
16-column fit at 0.69 when the nine extra columns were the hard ones. Compare
geometry (yaw, tilt), not residuals, across runs of different sizes.

## The dusk straddle that moved a yaw by 164°

As the sky darkens, a column the day detector can no longer resolve is recorded
as *blocked above the search ceiling* — a one-sided claim the fit treats as
strong evidence. One such column moved a solved yaw by **164°**.

Both commands now pick their measurement channel from the Sun's altitude, but
differently: `sweep` re-decides per column and switches at the −12° boundary
mid-run, so a dusk-into-dark circle is the supported case; `orient` chooses
once at run start, so an orient run should stay on one side of the boundary
(day-into-night handover for `orient` is terminus-63). Heading into dawn,
`--stop-above-sun-alt -18` ends either run cleanly before the sky turns.

## Plate solve first: a 3.7° mount drift moved the solution 5.6°

An unverified polar alignment biases *every* column by the same amount, and the
fit absorbs that bias silently rather than reporting it — the residual stays
small because the error is shared. Measured here: a 3.7° pointing drift moved
the solved orientation by 5.6°. Plate solve and correct the mount before
measuring columns you intend to rely on.

## Moving the tripod: closer costs more than further gains

The horizon is specific to where the tripod stood, and `atan` is nonlinear, so
approaching an obstruction costs more than retreating from it gains:

| moving 2 m, against a 2 m fence 5 m away | change |
|---|---|
| **2 m closer** | **+11.9°** |
| 2 m further away | −5.9° |
| along the fence (perpendicular distance unchanged) | 0° |
| past a narrow obstruction — a post, a chimney | −1.4° |

A 100 m ridge 2 km out moves 0.003° for the same 2 m, so distance is the whole
story. The error is largest exactly where the horizon is highest, because the
tall obstructions are the near ones. This is geometry, not something measured
here — which is why the mask and the `.hrz` carry the warning in their headers.
(The Stellarium `.txt` cannot: that format forbids comments.)

## Cloud is an asset, and under a light dome the order of azimuths matters

The sweep finds the horizon by brightness — sky is the light source, everything
terrestrial silhouettes against it — so an overcast sky under suburban light
pollution is a bright, even backdrop. A clear moonless night is the *harder*
case: darker overhead means less contrast against the treeline.

Under a strong light dome, directions facing away from town are legitimately
darker and are the first to fail as twilight fades — and a circular sweep
visits azimuths in time order, so those failures masquerade as properties of
the direction. Measure the dark directions in twilight and leave the bright
ones for later. After full dark the night channel separates Bortle-8 skyglow
from terrain at better than 100:1, because it fits the skyglow gradient rather
than assuming two brightness levels — under a light dome the sky *brightens*
toward the horizon, so a two-level rule has nothing to find.

Why the night channel exists at all: the scenery stream is blind after dark.
Its ISP pins exposure at ~30 ms whatever is asked — measured, sky vs terrain
differed by 0.02 counts in 255 — so night frames come from the star-mode
imaging channel at 2 s instead.

## Night behaviours to expect

- Near due north the mount cannot converge (the column crosses the celestial
  pole), so individual samples there are skipped and a burst of failed gotos
  may reset the imaging socket — the run restarts the view and retries the
  column once.
- A glare-washed column reports "open", which carries no constraint, rather
  than a plausible number. Unmeasured is recoverable; plausible-wrong is not.
- Night columns carry no obstruction type — every silhouette is neutral after
  dark — and their saved profiles are marked with the channel so a replay
  judges them with the night judge.

## What still bites

- **A `sweep` interrupted loses everything it measured.** The mask is written
  once, at the end. (`orient` no longer has this problem: every attempt is
  checkpointed as it completes, and a killed run resumes with cached columns
  served free — proven live 2026-08-06, when a killed run resumed with two
  columns served without re-observation and re-judged from wrong verdicts to
  right ones.)
- **The Sun is checked for where it is now, not where it will be.** A column
  can be clear when it is chosen and not by the time it is measured. At dusk
  this errs safe; at dawn it does not.
- **Daylight columns near the Sun are unreachable** and the run spends time
  discovering that (terminus-54: no low route around the Sun exists yet).
