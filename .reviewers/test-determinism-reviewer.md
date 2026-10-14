# test-determinism-reviewer
model: sonnet
verification: evidence-query — reruns the test with the pinned clock (`sweep._now`, set by the autouse fixture in `tests/conftest.py`) monkeypatched to a different instant, in a scratch copy, and shows the outcome flips; greps for the real-clock/network call.
**Checks:** Tests that pass or fail depending on when, where or in what order CI runs them: wall clock, Sun position, network, hardware, unseeded randomness, shared files or state.
**Spawn when:** diff adds or changes `tests/**/*.py`, `app/**/*.test.ts`, `app/e2e/**` or `tests/conftest.py`; skip diffs with no test-file change.
**Contents:** FLAG (P1) · FLAG (P2) · Do NOT flag

**Your focus:** test reliability, not coverage (test-coverage-reviewer) and not
whether an assertion can fail (pr-test-analyzer). CI once went red because a
refusal test depended on where the Sun was when it ran (`LESSONS.md` E-17);
`tests/conftest.py` now pins `sweep._now` for every test.

**FLAG (P1) when a PR:**

- Adds a test that reads the real clock or Sun (`Time.now()`, `datetime.now`,
  `Sky.sun()` without `when=`) in a path that decides a guard, refusal or
  classification, or that overrides the autouse pinned clock.
- Adds a test that reaches a real network address, a real Seestar, or a real
  `ffmpeg`/Hugin binary without a skip marker that CI honours.

**FLAG (P2) when a PR:**

- Uses unseeded `np.random`/`random`, fixed filenames outside `tmp_path`, or
  state shared between tests (module globals, env vars not set via `monkeypatch`).
- vitest/Playwright: real timers or `sleep`-based waits where fake timers or an
  event wait exist.

**Do NOT flag:** the `PINNED_UTC` constant itself; tests that pass `when=`
explicitly; slow-but-deterministic tests; missing coverage.
