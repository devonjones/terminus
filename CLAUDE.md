# terminus — project conventions

terminus measures the local horizon with a Seestar S50 and exports it for
planning tools (N.I.N.A., Stellarium, Sky Safari) and as a `Horizon` planning
library. Companion to [uranometria](https://github.com/devonjones/uranometria).

## Safety (highest priority)

Pointing a small refractor at the Sun can destroy the camera. **Every commanded
motion must go through `Pointer.point_to`, which steps each slew and re-checks the
Sun (recomputed for the current time) at every waypoint.** Never add a slew that
checks only the goto target, never suppress `SunGuard`, and keep the run-in-shade
guidance in the docs. In EQ mode `scope_get_horiz_coord` is unreliable — point by
RA/Dec goto and trust `scope_get_equ_coord`.

## Secrets

The Seestar interop key (an RSA private key extracted by the owner from the ZWO
app under 17 U.S.C. §1201(f)) is **never** committed. `*.pem` and `config.toml`
are git-ignored; keep it that way, and never print or log key material.

## Beads (issue tracker)

Beads state lives in `.beads/` in **JSONL-only mode** (`no-db: true`, no SQLite,
no Dolt). As with uranometria: **beads-only changes commit and push directly to
`main`**, separate from code commits — never route a `.beads/` change through a
PR branch, and never mix code + beads in one commit. Create issues with
`bd create` (prefix `terminus`).

## Everything else

- Tests, lint, and formatting gate merges (CI runs all three on every PR and push
  to `main`): `uv run ruff check src tests`, `uv run black --check src tests`,
  `uv run pytest -q`.
- uv-managed: `uv sync --dev`; the lockfile (`uv.lock`) is committed.
- Review conventions live in `AGENT-REVIEWERS.md` (consumed by pr-review-loop).
- `PANORAMA-PIPELINE.md` reconstructs how the published photo mosaic and orientation
  fit were actually built — inputs, code state, parameters and traps. Read it before
  attempting to rebuild the mosaic. The source frames are the 19 individual photos in
  `captures/panoramas/2026-08-03-frames/`, **not** the stitched panoramas in
  `captures/panoramas/2026-08-03/` — those are a different and worse capture mode.
- Lessons and deliberate decisions live in `LESSONS.md` — measurement discipline,
  fitting pitfalls, and instrument behaviour that cost real time to learn. Read it
  before re-deriving a rule; add to it when something is learned the hard way. IDs
  are append-only: mark entries superseded rather than renumbering or deleting.
- Versions bump in `pyproject.toml` **and** `src/terminus/__init__.py` together,
  with the change reflected where user-visible. (PyPI name is `terminus-horizon`;
  the import package and CLI are `terminus`.)
- Pure logic (transforms, classifier, horizon, exporters) must stay testable
  without hardware; add a test when you touch it. Live-scope code can't run in CI.
- Coordinate convention everywhere: azimuth 0 = true north, increasing toward
  east; altitude degrees; RA hours, Dec degrees.
- Runs headless (Raspberry Pi is a supported host); keep dependencies ARM-wheel
  friendly and avoid GUI/desktop assumptions.
