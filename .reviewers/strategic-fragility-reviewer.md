# strategic-fragility-reviewer
**Checks:** Failures turned into measurements: a value recorded without readback evidence, a caught exception falling through to a recorded result, a failure dropped from `skipped` or a partial sweep, 'blocked above ceiling' conflated with 'open to the search floor'; also needless brittleness (abort on a known-transient, removed retry, broad instrument-I/O catch that neither re-raises nor records).
**Spawn when:** A diff to `src/**/*.py` (notably `sweep.py`, `client.py`, `alpaca.py`, `src/terminus/server/**`) adds or changes a state-changing scope call, a `try`/`except` or retry around instrument I/O, a recorded mask/profile/fiducial value, `skipped` handling, or an `assert`/`raise` pinning an invariant; skip tests, docs, and pure formatting or math-only changes.
**Contents:** So the doctrine here splits along a line that pfsrd2 does not need · FLAG (P1) when a PR · FLAG (P2) when a PR

Adapted from the pfsrd2 parser's reviewer of the same name — **and the
adaptation is the point, because the two projects want opposite things from a
failure.**

pfsrd2 is a large batch parser. Fail-fast is right there: one malformed record
among thousands is invisible to the eye, so stopping at the first surprise is
the only way anyone finds out. terminus is not that. It drives **hardware that
is fragile by nature** — mounts stall near the pole, sockets drop on long runs,
the video stream freezes, cloud drifts across a column, a goto occasionally does
not arrive. Those are not surprises. They are the normal texture of a
multi-hour unattended session, and a run that aborts on the first one collects
nothing.

**So the doctrine here splits along a line that pfsrd2 does not need:**

| | Rule |
|---|---|
| **Interpretation** — turning an observation into a number | **Brittle.** Never record a value that is not evidenced. A call that returned without error is not proof it did anything. |
| **Execution** — getting through the night | **Robust.** Retry, skip, degrade, continue. Losing thirty good columns because the thirty-first stalled is itself data loss. |

The bridge between them, and the rule that makes both safe: **a thing that
failed must be recorded as failed.** Skipped columns go into `skipped`. A
column that hit its ceiling becomes a *bound*, not a value. A column that never
departed the sky model is a non-result with a stated reason, and
"blocked above the ceiling" and "open to the search floor" are opposite
conclusions that must never collapse into each other. Robustness that quietly
omits its failures is just silent corruption with better manners.

**The recurring failure in this project is not a crash.** It is a call that
**reports success while doing nothing**, whose return value is then recorded as
a measurement. Every one of these happened:

- `scope_goto` reported "landed" with the arm closed and the mount stationary
- `lock_exposure` returned `manual_exp: False` and sentinel values, treated as
  locked
- the dew heater ignored `set_setting` entirely
- the open-sky reference went stale across twilight (254 → 94.7) and was about
  to mark the whole western sky blocked
- the RTSP stream froze and served one byte-identical frame however the mount
  moved, fabricating ten obstructed azimuths out of open sky

**FLAG (P1) when a PR:**

- Records a value into the mask, a profile, or a fiducial without evidence the
  underlying operation actually happened. Every state-changing scope call must
  verify its readback.
- Turns a failure into a number: substituting a default, reusing the previous
  column's value, or letting a caught exception fall through to a recorded
  result.
- Drops a failure from the record — skipping a column without appending it to
  `skipped`, or discarding a partial sweep on abort.
- Collapses a non-result into a value, in particular conflating "blocked above
  the ceiling" with "open to the search floor".

**The Alpaca driver (app stage 4) is in scope.** Alpaca calls also report
success while doing nothing, so read back `Slewing`, `AtPark` and the position
before recording anything.

**FLAG (P2) when a PR:**

- Makes the run *more* brittle without cause: aborting a whole sweep on a
  transient, removing a retry, or narrowing a recovery path, where the failure
  is one the hardware is known to produce. Say what is lost when it triggers.
- Catches broadly around instrument I/O without re-raising *and* without
  recording the failure.
- Removes an `assert`/raise that pins an invariant, without saying what now
  enforces it.

**Do NOT flag — these are deliberate, and a fail-fast reading would break
them.** The question is never "is it swallowing?" but **"does anything
downstream still get judged on evidence?"**

- the sky-reference seeding and mid-sweep refresh catch `PointingError`:
  seeding is a convenience, nothing is being measured, and a mount that cannot
  point will fail the columns too, where the miss counter judges it on evidence
- the refine loop catches it: refinement is optional work on an already-measured
  mask
- the scan loop skips a single failed column, records it in `skipped`, and only
  abandons the sweep after `MAX_POINTING_MISSES` *consecutive* failures —
  carrying the partial result out on the exception so the night is not thrown
  away
- `capture_rgb` retries a repeated frame rather than raising
- `cmd_sweep` reports a failed `stop_view` and continues, because the
  measurement outranks the cleanup
- a column that yields no edge becomes a bound or a stated non-result, and the
  sweep moves on

---
