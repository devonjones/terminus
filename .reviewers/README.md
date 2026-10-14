# Guidelines

terminus: a Python library + CLI that measures the local horizon with a Seestar
S50 and exports it for N.I.N.A., Stellarium, and planners, and an Electron
desktop app over a Python sidecar.

## How the pack runs

**This file is consumed BY the `pr-review-loop` skill — do not run these reviewers
directly.** The skill owns posting findings, verification, retirement, CI gating,
and exit conditions.

**Per-reviewer file scope:**

| Reviewer | Files in scope |
|----------|----------------|
| `sun-safety-reviewer` | `src/terminus/client.py`, `sweep.py`, `cli.py`, `server/**`, the Alpaca driver, `app/**` |
| `coordinate-correctness-reviewer` | `src/terminus/sweep.py`, `horizon.py`, `orient.py`, `plan.py`, `app/src/**` |
| `secrets-reviewer` | whole repo, `.gitignore`, `pyproject.toml`, `README.md`, `.github/workflows/**` |
| `export-format-reviewer` | `src/terminus/export.py` |
| `strategic-fragility-reviewer` | `src/**/*.py` (incl. the Alpaca driver) |
| `complexity-reviewer` | `src/**/*.py` (never `tests/`) |
| `clarity-reviewer` | `README.md`, docstrings/comments, `--help` text, the PR description |
| `external-process-reviewer` | `src/terminus/mosaic.py`, `client.py`, `app/main/**` |
| `error-handling-reviewer` | `src/**/*.py`, `app/**/*.ts` |
| `resource-leak-reviewer` | `src/**/*.py` |
| `test-coverage-reviewer` | `src/**/*.py`, `tests/**/*.py`, `app/**/*.ts` |
| `electron-security-reviewer` | `app/main/**`, `app/preload/**`, `app/src/index.html` |
| `local-api-reviewer` | `src/terminus/server/**`, `app/main/**` |
| `sidecar-lifecycle-reviewer` | `app/main/**`, `src/terminus/server/**` |
| `api-contract-reviewer` | `src/terminus/server/**`, `app/src/api/**`, `app/main/**` |
| `ui-reviewer` | `app/src/**` |
| `release-reviewer` | electron-builder config, `.github/workflows/**`, packaging scripts |

Skip reviewers whose scope doesn't match the diff. A reviewer's silence means
nothing in its scope changed, not endorsement.

## Tooling in CI

`.github/workflows/ci.yml` runs on every PR and on push to `main`:

- `uv run ruff check src tests` — linting
- `uv run black --check src tests` — formatting (line length 100)
- `uv run pytest -q` — hardware-free unit tests

The `app` job, in `app/`:

- `npm run lint` — eslint (typescript-eslint recommended)
- `npm run typecheck` — `tsc --noEmit`, strict
- `npm run format:check` — prettier (print width 100)
- `npm test` — vitest (main-process lifecycle with `child_process` mocked, front end in jsdom)
- `npm run e2e` — Playwright-Electron smoke against the real sidecar, under xvfb

The win/mac/linux packaging matrix lands with packaging (terminus-71.6).

Reviewers do not re-litigate formatting (black, prettier own it), lint that ruff
or eslint already enforces, or type errors tsc catches.

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

**Mutate in your own worktree, and check which source it actually imports.**
`git worktree add /tmp/<name> origin/<branch>` keeps your mutations off the
shared checkout — which matters, because a `commit-and-push.sh` running there
uses `git add -A` and has already swept a reviewer's live mutation into a real
commit.

But the isolation is not free. The project is installed editable, so
`.venv/.../*.pth` points at `/home/devon/Projects/terminus/src` — the ORIGINAL
checkout. Run that interpreter from a worktree and it imports the original
source, so a mutation "passes" because the mutated file was never loaded. This
has already happened. Either build a venv in the worktree (`uv sync --dev`) or
export `PYTHONPATH=/tmp/<name>/src`, and confirm before trusting a green run:

    python -c "import terminus, sys; print(terminus.__file__)"

**Say which interpreter you used.** `uv sync --dev` installs only the declared
dependencies, so torch, transformers and the Hugin binaries are absent there and
present in the author's `.venv`. A conclusion about optional-dependency
behaviour is meaningless without naming the environment it came from — one
reviewer reported that torch "is not installed" when it was, in the venv that
mattered.

## Gemini is defunct

Gemini Code Assist no longer responds on this repo. Do not trigger it or wait on
it; a round spends five minutes polling a dead reviewer for nothing. The agents
above are what carry a review round, and a round is complete when they go quiet.
