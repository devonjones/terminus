# Configuration

```json
{
  "defaults_version_checked": "1.6.0",
  "disabled": [
    "concurrency-reviewer",
    "dataclass-decorator-reviewer",
    "migration-idempotency-reviewer",
    "homelab-values-reviewer",
    "logging-reviewer",
    "resource-leak-reviewer"
  ],
  "notes": "terminus is a small pure-Python library + CLI that drives a Seestar S50 over TCP and produces horizon files. No threads, no database, no long-lived resources. The pack below replaces the disabled defaults with reviewers tuned to what this project can actually get wrong: pointing the telescope somewhere dangerous, coordinate/azimuth-convention bugs, leaking the interop key, and breaking the exported file formats other tools depend on."
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
| `coordinate-correctness-reviewer` | `src/terminus/sweep.py`, `horizon.py` |
| `secrets-reviewer` | whole repo, `.gitignore`, `pyproject.toml`, `README.md` |
| `export-format-reviewer` | `src/terminus/export.py` |
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
