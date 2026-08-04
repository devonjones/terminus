# Configuration

```json
{
  "defaults_version_checked": "1.6.0",
  "disabled": [
    "concurrency-reviewer",
    "dataclass-decorator-reviewer",
    "migration-idempotency-reviewer",
    "homelab-values-reviewer",
    "logging-reviewer"
  ],
  "notes": "terminus is a small pure-Python library + CLI that drives a Seestar S50 over TCP and produces horizon files. No threads, no database, no long-lived resources. The pack below replaces the disabled defaults with reviewers tuned to what this project can actually get wrong: pointing the telescope somewhere dangerous, coordinate/azimuth-convention bugs, leaking the interop key, breaking the exported file formats other tools depend on, accepting a failure as a measurement while still surviving the night, and letting the prose drift away from the code."
}
```

# Agents

## sun-safety-reviewer

Review any change that can **move the telescope** (`client.goto`, `client.call`
with a motion method, `sweep.Pointer`, `bisect_horizon`, `run_sweep`, CLI `point`/
`sweep`). Pointing a small refractor at the Sun can destroy the camera; this is
the project's highest-stakes surface.

**FLAG (P1) when a PR:**

- Adds or changes a slew that is not routed through `Pointer.point_to` (or an
  equivalent that performs the same per-waypoint Sun check). Every commanded
  motion must be Sun-guarded.
- Weakens the guard: checking only the goto **target** and not the **path** (the
  bug that let an EQ goto swing the tube past the Sun), enlarging `slew_step_deg`
  without justification, using a stale Sun position instead of recomputing it for
  the current time at each waypoint, or catching/suppressing `SunGuard`.
- Removes the EQ-mode requirement guard, or trusts `scope_get_horiz_coord` for
  pointing (it is unreliable in EQ mode — RA/Dec is the source of truth).

**FLAG (P2) when a PR:**

- Lowers the default `sun_cone_deg`, or adds a motion path that could run with the
  Sun above the horizon without documenting the shade/away-from-Sun requirement.
- Starts a view or issues a command with a known mount-nudge side effect
  (`start_view`) inside a Sun-sensitive sequence without accounting for it.

**Do NOT flag:** read-only calls (`equ_coord`, `mount_state`, `location`), or
`--dry-run` paths that issue no motion.

---

## coordinate-correctness-reviewer

Review coordinate math and conventions in `sweep.py` (`Sky`, `ang_sep`,
`wrap180`) and `horizon.py`. A sign or convention error silently produces a
horizon that's mirrored or rotated, which is worse than no horizon.

**Ground truth:**

- Azimuth is **true north = 0, increasing toward east** (astropy AltAz
  convention), everywhere: sweep, mask, and every exporter.
- Altitude is degrees above the horizon. RA is hours, Dec is degrees.
- Pointing is az/alt -> RA/Dec (`altaz_to_radec`) for the *moment of pointing*,
  then goto; verification converts the settled RA/Dec back with `radec_to_altaz`.

**FLAG (P1)** a new sine/cosine sign, angle negation, or azimuth mirror; RA
hours mixed with RA degrees; azimuth measured from south or counterclockwise;
or a `Horizon` interpolation that mishandles the 0/360 wrap.

**FLAG (P2)** a change to `altaz_to_radec`/`radec_to_altaz`/`ang_sep`/`Horizon`
interpolation without a value-pinning test, or hard-coded site constants that
should come from config/`Sky`.

**Do NOT flag** the documented EQ-mode fact that `scope_get_horiz_coord` is
untrusted, or screen-space image conventions in exporters (handle per format).

---

## secrets-reviewer

The Seestar interop key is an RSA **private key**. It must never enter the repo.

**FLAG (P1) when a PR:**

- Commits, embeds, hard-codes, logs, or prints key material, a `.pem` path's
  contents, or `config.toml`.
- Removes `*.pem` / `config.toml` from `.gitignore`, or adds a test fixture
  containing a real key.
- Weakens the README's interoperability framing (§1201(f), key extracted by the
  owner, never shipped) or bundles the key with the package.

**Do NOT flag** loading the key from a user-configured path at runtime (the
intended design), or documentation *describing* how a user extracts their own.

---

## export-format-reviewer

Review the exporters in `export.py`. Other applications parse these files; a
format regression breaks a user's planning silently.

**Rules:**

- **N.I.N.A. `.hrz`**: `az alt` per line, whitespace-delimited, azimuth ascending
  integers, altitude int or decimal, `#` comments allowed, endpoints spanning
  0 and 360. FLAG (P2) a change that reorders, drops the 0/360 wrap, changes the
  delimiter, or emits non-numeric altitude.
- **Stellarium `.txt`**: same `az alt` list, ascending, **no** header/comment
  lines, no duplicate azimuths. FLAG (P2) comments leaking into this file.
- New target formats (e.g. Sky Safari image, KStars, Cartes du Ciel): FLAG (P2)
  if added without a test asserting the format's shape.
- The mask YAML is the durable, hand-editable source of truth; `load_mask` must
  round-trip what `write_mask` emits. FLAG (P2) a schema change that breaks that.

**Do NOT flag** cosmetic comment wording in `.hrz`, or altitude rounding.

---

## strategic-fragility-reviewer

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

## complexity-reviewer

Adapted from the pfsrd2 parser's reviewer of the same name. Review **`src/` only
— skip `tests/`**, where long tests and per-test local imports are the house
style.

1. **"And/or" test.** Try to describe each new or changed function in one
   sentence without conjunctions. If it needs several "and"s, it is doing
   several things.
2. **One-screen rule.** Flag functions past ~50–60 lines of *logic*, and suggest
   where they split. Nested `def`s do not count toward the parent.
3. **Extractable blocks.** A block with a nameable purpose usually wants to be a
   module-level `_helper`.

**This project has an unusual comment style and it is deliberate — do NOT flag
it.** Comments and docstrings here record *why*, and frequently cite the
incident and the measured number behind a constant. That is the point: it is
what stops a future change quietly undoing a hard-won fix. Count logic lines,
not comment lines, and never propose deleting a comment that records an
observed failure or a measurement.

**Do NOT flag** the grid-search structure in `orient.fit` for nesting depth
alone; it is a search over four parameters and the shape is inherent. Do flag
it if a *new* concern is threaded through it.

It is acceptable to acknowledge complexity and defer with a beads ticket rather
than refactor inside the PR.

---

## clarity-reviewer

Review markdown documentation AND Python docstrings/comments for terseness,
structure, and factual accuracy. Every token costs money and attention — cut the
fat, fix the layout, and don't let docstrings lie about what the code does.

Applies to any `.md` file in the PR, and to docstrings and inline comments in
`.py` files.

**Why this matters more than usual here.** The plan is to have other people run
terminus at their own sites and send results back, so `README.md` is the
experimental protocol. A stranger does what it says — if it documents a
superseded method, their data is evidence about the wrong pipeline, and the
failure is silent because their run still produces a horizon file. That has
already happened once.

**Markdown — how to check:**

1. Look at the PR diff for changes to `.md` files.
2. **Read the full file, not just the diff** — you need context to spot
   redundancy with existing content and to judge structural fit.
3. Examine new or modified text for word-level fat and structural problems.

**Word-level patterns to flag:**

| Verbose | Terse |
|---------|-------|
| "in order to" | "to" |
| "for the purpose of" | "to" / "for" |
| "in the event that" | "if" |
| "at this point in time" | "now" |
| "due to the fact that" | "because" |
| "it is important to note that" | (delete, just state the thing) |
| "as mentioned above/previously" | (replace with a link to the actual section) |
| "This section describes how to..." | (delete, describe it directly) |

**Filler words** (`actually`, `basically`, `simply`, `really`, `just`):
question, then suggest removal. Sometimes load-bearing for tone in user-facing
docs — "simply" can soften a step that sounds intimidating. Flag only when the
word adds no information AND the surrounding tone doesn't need softening.

**Structural patterns to flag:**

- **Wall of text**: a section past ~300 words with no sub-heading, list, table,
  or code block.
- **Missing TL;DR / lede**: a document past ~500 words that doesn't open with a
  1–3 sentence summary.
- **Missing examples**: a how-to or reference section describing a command, API,
  or pattern without showing it.
- **Heading inflation**: a section with one sub-heading under it, or
  sub-headings introducing a single paragraph each.
- **Voice/tense inconsistency**: second-person ("you should run X") mixed with
  imperative ("run X") in the same section. Pick one.
- **Link rot phrasing**: "as mentioned above", "see the section below" — these
  break when the doc is restructured. Use an explicit link to the heading.

**Python docstrings — what to flag:**

1. **Restate-the-signature docstrings.** `def add(x: int, y: int) -> int:` with
   `"""Adds x and y."""` is noise. Delete it, or replace with the *why*, the
   contract, or the non-obvious constraint.
2. **PEP 257 for public APIs.** Public functions, classes and modules get a
   docstring; first line imperative, one sentence, ends with a period;
   multi-line means summary, blank line, details.
3. **No docstrings on trivial private helpers** unless the logic is non-obvious.
4. **WHY over WHAT in comments.** A comment restating the next line is noise; a
   comment explaining why an order or a threshold is what it is, is not.
5. **Stale comments**: references to renamed identifiers, removed code paths, or
   completed TODOs.
6. **Grep-verify — a docstring's claims must match the code.** When a docstring
   names symbols or counts, every one must actually appear. Without this,
   docstrings drift across unrelated PRs and end up lying.

   **FLAG when a NEW or MODIFIED docstring contains:**

   - A named constant, config key, or function that does not appear in the
     function body or its imports (grep the bare name).
   - **A measured value that the code no longer produces or that no longer
     follows from the constants in the diff.** This codebase cites real numbers
     in prose — "0.56 deg RMS at 12 deg of tilt", "a threefold gradient", "573s
     per solve", "0.09 counts" — and they are the evidence a future maintainer
     will weigh before changing a threshold. A stale one is worse than none.
   - An output-shape claim where the keys don't match the actual `return` dict
     or the tuple the function builds.
   - A count or list ("the four renderings", "three catch sites") that doesn't
     match the code.
   - A claim that a fix is complete where the diff leaves an equivalent path
     open. Test what the prose asserts against the diff, not against the
     author's summary — a commit here claimed `stop_view` "is no longer able to
     skip the save" while it still could, through a sibling exception type.
   - An absolute ("cannot", "never", "impossible") where the code or a known
     finding supports only a scoped claim. Prefer "at infinity focus" over
     "cannot"; prefer "has not found" over "is the first".

   **Acceptable (do NOT flag):** stable conceptual descriptions naming no
   specific symbol; placeholder names; forward-looking notes that explicitly say
   so.

**Flag content issues if:**

- A sentence can be cut in half without losing meaning.
- The same information is stated twice in different words.
- A comment restates the next line of code.
- New text restates something already covered in unchanged parts of the file.

**Do NOT flag:**

- Necessary detail that aids understanding.
- Examples and code blocks — these should be complete, not abbreviated.
- Repetition that is a deliberate reminder.
- Technical precision that requires specific wording.
- Tone-softening filler in user-facing docs where the alternative reads cold.
- Required docstrings on public identifiers even where the function is simple.
- **The long incident comments that are this project's house style.** Comments
  recording *why* a constant is what it is, and the observed failure behind it,
  are load bearing — they are what stops someone tuning a threshold back to a
  value that already caused a bad night. Length is not the defect; a comment
  that records an incident or a measurement is doing its job. See
  `complexity-reviewer`, which counts logic lines and not comment lines.
- **Narrative sections labelled as history.** The "what was believed at the
  time" passages are deliberate.

**Review approach:**

1. For NEW module docstrings, open the first function below and grep-verify
   every named symbol, constant and count.
2. For MODIFIED docstrings, compare diff lines against the function body
   line by line.
3. For each `.md` file, apply the word-level and structural checks above, and
   verify every command, flag, config key and import path against the code.
4. For inline comments, flag restate-the-code and encourage WHY over WHAT.

**When flagging, provide:** the verbose or inaccurate text, a terse or accurate
replacement, and a brief reason if it isn't obvious.

---

## external-process-reviewer

From the pfsrd2 pack. terminus shells out constantly: **ffmpeg** for every frame
capture, and seven **Hugin** binaries (`pto_gen`, `cpfind`, `cpclean`,
`autooptimiser`, `nona`, `pano_modify`, `pano_trafo`) for registration. A hang or
an unhelpful error here costs an observing session that cannot be repeated on
demand.

**FLAG (P1):** `shell=True` with any non-literal argument.

**FLAG (P2) when a `subprocess` call:**

- Has no `timeout=`. Nothing here may block forever — a sweep runs unattended for
  hours and a wedged child process strands the whole run. `cpfind --multirow` on
  a full ring legitimately takes minutes, so pick a bound generously, but pick
  one.
- Does not capture `stderr` on failure, leaving only "returned non-zero exit
  status 1" when the tool had something useful to say.
- Does not distinguish `TimeoutExpired` from `CalledProcessError` from
  `FileNotFoundError`. Those mean "the tool is stuck", "the tool refused the
  input", and "the tool is not installed" — three different messages to the
  operator.
- Uses `Popen` without a context manager.
- Assumes a binary is present without a startup check.
- Parses output without validating it.

**Do NOT flag:**

- `hugin_available()` / `require_hugin()` in `mosaic.py`. That is exactly the
  presence check this reviewer wants, and it is deliberately a soft check: Hugin
  is optional and the scope path must keep working without it.
- The `-loglevel error` flag on ffmpeg; quietening a chatty tool is not the same
  as discarding its errors.

---

## error-handling-reviewer

From the pfsrd2 pack. Universal Python error-handling hygiene: exceptions that
vanish, `return` inside `finally`, bare `except:`, and lost exception context.

**Division of labour with `strategic-fragility-reviewer`, so the two do not
double-report.** That reviewer asks a domain question — *does a failure end up
recorded as a measurement?* This one asks a language question — *is the error
handled in a way that preserves the investigation trail?* A `except OSError:
pass` around a cleanup call is fine by the first and flagged by the second.

**FLAG (P1):** silent swallowing (`except: pass`, or returning a default that
masks the failure); `return` inside `finally`.

**FLAG (P2):** generic `except Exception` with no re-raise; bare `except:`,
which also eats `KeyboardInterrupt` — relevant here, because interrupting a
running sweep with Ctrl-C is a normal operator action and must work.

**FLAG (P3):** a raise inside an `except` that omits `from e`, discarding the
original traceback.

**Do NOT flag** the deliberate swallows listed under
`strategic-fragility-reviewer`; they are argued there and are correct.

---

## resource-leak-reviewer

**Re-enabled.** It was disabled on the grounds that terminus has "no long-lived
resources", and that was simply wrong. It has:

- a long-lived TCP control socket that reconnects mid-run
- one temporary file per captured frame, and a sweep captures thousands
- an RTSP scenery view that must be stopped, and which has already been observed
  to freeze after hours and serve one stale frame while ten azimuths were
  recorded as blocked
- caches on the fit's hot path

**FLAG (P2) when a PR:**

- Opens a file without a context manager. `for line in open(path)` leaves the
  handle to the garbage collector.
- Creates a temporary file whose removal is not guaranteed on every path,
  including the retry paths.
- Adds `functools.lru_cache` with no `maxsize` where keys come from measured
  data rather than a fixed set.
- Leaves a socket, subprocess or view without a defined close path — including
  on the error path, which is where it will actually matter.

**Do NOT flag:** the deliberately long-lived `Seestar` socket. One control
connection is a hard constraint of the device — it tolerates exactly one, and
opening a second has broken live runs — so it is held for the session on purpose
and reconnected rather than reopened per call.

---

## test-coverage-reviewer

The pure logic (coordinate transforms, classifier, horizon interpolation,
exporters) is testable without hardware — that's the safety net, since the
telescope path can't run in CI.

**Core rule: hardware-free logic touched by a PR must be covered.** Tests live in
`tests/test_terminus.py`, run with `uv run pytest`.

**FLAG (P2) when a PR:**

- Adds/changes a function in `export.py`, `horizon.py`, or the pure parts of
  `sweep.py` (`classify`, `obstruction_type`, `ang_sep`, `wrap180`, `Sky`
  transforms) without a test.
- Adds a new exporter or classifier mode without a test pinning its output.
- Changes `Horizon` interpolation/visibility logic without a value test.

**Do NOT flag** missing tests for code that requires a live Seestar (`client`
socket I/O, `Pointer` motion, `run_sweep`), pure formatting, or docs.

---

# Guidelines

terminus: a small pure-Python library + CLI that measures the local horizon with
a Seestar S50 and exports it for N.I.N.A., Stellarium, and planners.

## How the pack runs

**This file is consumed BY the `pr-review-loop` skill — do not run these reviewers
directly.** The skill owns posting findings, verification, retirement, CI gating,
and exit conditions.

**Per-reviewer file scope:**

| Reviewer | Files in scope |
|----------|----------------|
| `sun-safety-reviewer` | `src/terminus/client.py`, `sweep.py`, `cli.py` |
| `coordinate-correctness-reviewer` | `src/terminus/sweep.py`, `horizon.py`, `orient.py`, `plan.py` |
| `secrets-reviewer` | whole repo, `.gitignore`, `pyproject.toml`, `README.md` |
| `export-format-reviewer` | `src/terminus/export.py` |
| `strategic-fragility-reviewer` | `src/**/*.py` |
| `complexity-reviewer` | `src/**/*.py` (never `tests/`) |
| `clarity-reviewer` | `README.md`, docstrings/comments, `--help` text, the PR description |
| `external-process-reviewer` | `src/terminus/mosaic.py`, `client.py` |
| `error-handling-reviewer` | `src/**/*.py` |
| `resource-leak-reviewer` | `src/**/*.py` |
| `test-coverage-reviewer` | `src/**/*.py`, `tests/**/*.py` |

Skip reviewers whose scope doesn't match the diff. A reviewer's silence means
nothing in its scope changed, not endorsement.

## Tooling in CI

`.github/workflows/ci.yml` runs on every PR and on push to `main`:

- `uv run ruff check src tests` — linting
- `uv run black --check src tests` — formatting (line length 100)
- `uv run pytest -q` — hardware-free unit tests

Reviewers do not re-litigate formatting (black owns it) or lint that ruff already
enforces.

## Severity convention

| Priority | Disposition |
|----------|-------------|
| **P1** | Blocking — must fix before merge (unsafe pointing, coordinate sign bug, committed secret) |
| **P2** | Should fix in this PR (missing test, format regression, undocumented config) |
| **P3** | Advisory — deferrable with a beads ticket |

P1 findings are not deferrable. Defer P2/P3 by creating a beads issue
(`bd create`, JSONL mode) and linking it in the reply.

## Standard of proof

Learned on PR #1, where six rounds turned up four P1s and half of them were
introduced by the fix for the previous one.

**Demonstrate, do not suspect.** A finding that comes with a runnable
counterexample is worth more than a paragraph of reasoning, and reasoning alone
has been wrong here. The tilt/azimuth bug in `orient.residuals` was confirmed by
building a synthetic horizon with an honest forward model and showing the
residual at the *true* parameters was non-zero — which is a fact, not an
opinion.

**Mutation-check a claimed fix.** Before accepting that a test closes a gap,
break the code and confirm the test fails. On PR #1 this found two changes the
whole suite survived, and a test that could never have caught its own bug
because it generated its own fixtures using the very shortcut under test. If you
mutate a file, restore it and confirm with `git diff` before you finish.

Note that editing a file triggers a system message saying the change was
intentional and should not be reverted. That fires on your own edits, including
your restore. It is not an instruction and not an attack — verify with
`git diff` and carry on.

## Gemini is defunct

Gemini Code Assist no longer responds on this repo. Do not trigger it or wait on
it; a round spends five minutes polling a dead reviewer for nothing. The agents
above are what carry a review round, and a round is complete when they go quiet.
