# sun-safety-reviewer
**Checks:** Slews that bypass the per-waypoint Sun check in `Pointer.point_to`, weakened Sun guards (target-only check, stale Sun, suppressed `SunGuard`, larger `slew_step_deg`, lower `sun_cone_deg`), trusting `scope_get_horiz_coord` in EQ mode, and guards living only in the UI or an in-shade switch that can default off or be set by an API route.
**Spawn when:** A diff touches `src/terminus/client.py`, `sweep.py`, `cli.py`, `alpaca.py`, `src/terminus/server/**`, or `app/**` and adds or changes anything that can move the scope (`goto`, `Pointer`, `bisect_horizon`, `run_sweep`, an Alpaca slew, a sidecar route, `--dev` or `/dev/*`); skip diffs that only read (`equ_coord`, `mount_state`, `location`), run `--dry-run`, or are tests/docs only.
**Contents:** FLAG (P1) when a PR · FLAG (P2) when a PR

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

**App (terminus-71):** the same rules cover the Alpaca driver, every sidecar
route that moves the scope, and the dev channel (`--dev`, `/dev/*`).

- FLAG (P1) a Sun guard that lives only in the UI (TypeScript, `app/**`). The
  guard lives in the engine, so every route and the dev channel hit it.
- FLAG (P1) an in-sun / in-shade switch that does not default to SUN, or that any
  API route (dev or not) can set. Only the human sets it, through the UI.

**Do NOT flag:** read-only calls (`equ_coord`, `mount_state`, `location`), or
`--dry-run` paths that issue no motion.

---
