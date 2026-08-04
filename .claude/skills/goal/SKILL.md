---
name: goal
description: Work autonomously toward a stated goal, choosing the highest-value next action, verifying by measurement, recording durably, and reporting briefly before continuing. Use when the user wants sustained progress without supervising each step.
---

# Goal

Drive toward `$ARGUMENTS` under your own steam. The user is not watching. They
want the goal advanced, not a running commentary, and they want to be able to
trust what they read when they come back.

## Each pass

**1. Re-establish state from evidence, not memory.**
`bd list --status open` for what is known outstanding, `git status` and
`git log --oneline -5` for what is written, and the actual artefacts on disk —
logs, captures, test output. A summary in your context may be stale or wrong;
the repository is not. Never resume from what you believe you did.

**2. Choose exactly one next action: the largest reduction in uncertainty or
risk per unit of effort.** Prefer, in order:

- something that would invalidate other work if it stays broken
- something blocking a decision the user has to make
- something that turns an assumption into a measurement
- something that makes a failure visible rather than silent

Skip work that merely adds volume. Ten more of a thing you already have is
rarely worth as much as one measurement that tells you the thing is wrong.

**3. Do it, then prove it.** A change is not done because it ran. Show the
number that moved, the test that passes, the frame that differs. If you cannot
demonstrate it, say so plainly and treat it as unfinished.

**4. Record durably before moving on.** Findings go in `bd` — including the
things that did *not* work and why, so they are not re-attempted. Code goes in
the working tree with the reasoning in comments. Nothing important should exist
only in the conversation.

**5. Report in a few lines.** What changed, what it means, what is next. No
recap of the plan, no narration of tool calls.

## Standing rules

These were paid for and should not be relearned.

- **Verify every state change against its readback.** Commands that report
  success while changing nothing have appeared repeatedly here: an exposure
  lock, a goto, a dew heater, a cached reference. Set it, read it back, compare.
- **A capture that succeeds is not a measurement.** Data that never varies with
  input is a stuck sensor. Confirm the output actually moves when the input does.
- **One connection to the telescope.** It tolerates a single client. While a
  sweep holds it, read the log, never the scope.
- **Do not tune a global threshold to fix one case.** Detect the outlier, then
  measure it harder. A constant that fixes one column usually breaks four.
- **Flag ambiguity, never average it away.** If a column had two plausible
  answers, record that it did.
- **Distrust an improvement that appears only in the metric.** Ask what the
  metric cannot see; a fit with a free parameter absorbs bias silently.

## Hardware limits

- **No slews within 45 minutes of astronomical twilight.** The Sun guard has
  never been validated with the Sun above the horizon, and a scope pointed at
  the Sun is not a recoverable mistake. Check the Sun's altitude before every
  observing action, and stop entirely if it is above −12°.
- **Stop and hand back** on any `SunGuard`, `PointingError`, or a mount that
  will not move. Do not retry, do not work around it, do not raise a limit.
- **Leave the scope safe** when finishing a session: abort any slew, stop the
  view, close the connection.
- The user is asleep. Nothing you do should require them to intervene.

## When to stop

Stop and wait when the goal is met, when the next step needs a decision only the
user can make, when hardware limits are reached, or when two consecutive passes
produce nothing worth recording. Say which, in one line.
