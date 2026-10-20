# test-coverage-reviewer
model: sonnet
verification: mutation — for a claimed gap, mutates the changed line in a scratch copy and shows the existing suite still passes; for a missing test file, greps `tests/` for the symbol.
**Checks:** Changed logic without tests in the same PR: hardware-free pure code in `export.py`/`horizon.py`/`sweep.py`, new sidecar routes (no in-process pytest incl. refusal paths), `app/main/**` lifecycle without vitest, `app/src/**` components/math without vitest, user flows without e2e (P2); not live-scope code, formatting or docs.
**Spawn when:** diff changes `src/**/*.py` or `app/**/*.ts` non-test files (always when `src/terminus/export.py`, `horizon.py`, `sweep.py`, `server/**`, `app/main/**` or `app/src/**` change), or edits `tests/**`/`*.test.ts`/`app/e2e/**`; skip docs-only, formatting-only and rename-only diffs.
**Contents:** Core rule · FLAG (P2) when a PR · App rules · Do NOT flag

The pure logic (coordinate transforms, classifier, horizon interpolation,
exporters) is testable without hardware — that's the safety net, since the
telescope path can't run in CI.

**Core rule: hardware-free logic touched by a PR must be covered.** Tests live in
`tests/test_*.py` (`test_terminus.py` for the pure logic, `test_server.py` for sidecar routes, `test_alpaca.py`, `test_scope_link.py`), run with `uv run pytest`. Grep all of them before claiming a test is missing.

**FLAG (P2) when a PR:**

- Adds/changes a function in `export.py`, `horizon.py`, or the pure parts of
  `sweep.py` (`classify`, `obstruction_type`, `ang_sep`, `wrap180`, `Sky`
  transforms) without a test.
- Adds a new exporter or classifier mode without a test pinning its output.
- Changes `Horizon` interpolation/visibility logic without a value test.

**App rules: every stage ships its tests in the same PR.**
FLAG (P2) when a PR:

- adds a sidecar route (`src/terminus/server/`) without a pytest that calls it
  in-process, including its refusal paths (no token, wrong Host, an Origin);
- changes `app/main/**` lifecycle (spawn, health, stop, crash) without a vitest
  that mocks `child_process`;
- adds a front-end component or display math in `app/src/**` without a vitest
  (Testing Library for components, value-pinned for math);
- adds a user-visible flow without extending the Playwright-Electron e2e
  (`app/e2e/`); scope flows run against the OmniSim Alpaca simulator, never hardware.

**Do NOT flag** weak, brittle or unfailable existing tests (pr-test-analyzer), or missing tests for code that requires a live Seestar (`client`
socket I/O, `Pointer` motion, `run_sweep`), pure formatting, or docs.
