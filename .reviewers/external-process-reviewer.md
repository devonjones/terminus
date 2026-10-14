# external-process-reviewer
**Checks:** Subprocess/child_process calls (ffmpeg, Hugin tools, sidecar spawn) with shell=True and non-literal args (P1), or missing timeout, stderr capture, distinct timeout/non-zero/not-installed handling, Popen without context manager, unchecked binary, unvalidated output (P2).
**Spawn when:** diff touches `src/terminus/mosaic.py`, `src/terminus/client.py`, or `app/main/**` (incl. `sidecar.ts`), or adds `subprocess`/`Popen`/`child_process`/`spawn(` anywhere under `src/**/*.py` or `app/**/*.ts`; skip when none of those change, and skip `*.test.ts` and `tests/**`.
**Contents:** FLAG (P2) when a `subprocess` call · Do NOT flag

From the pfsrd2 pack. terminus shells out constantly: **ffmpeg** for every frame
capture, and six **Hugin** binaries (`pto_gen`, `cpfind`, `cpclean`,
`autooptimiser`, `nona`, `pano_modify`) for registration. A hang or
an unhelpful error here costs an observing session that cannot be repeated on
demand.

**The Electron main process spawning the sidecar (`app/main/sidecar.ts`) is in
scope.** The same rules apply: a startup timeout, stderr captured (to
`sidecar.log`), and spawn errors, early exits and timeouts reported as distinct
messages.

**FLAG (P1):** `shell=True` (or `spawn(..., {shell: true})`) with any non-literal argument.

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
