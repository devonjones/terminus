# external-process-reviewer
model: sonnet
verification: evidence-query — greps the diff for the subprocess call and its kwargs (`timeout=`, `shell=`, `stderr`) and quotes the line.
**Checks:** Subprocess calls (ffmpeg, Hugin tools) with shell=True and non-literal args (P1), or missing timeout, stderr capture, distinct timeout/non-zero/not-installed handling, Popen without context manager, unchecked binary, unvalidated output (P2). The sidecar spawn is sidecar-lifecycle-reviewer's.
**Spawn when:** diff touches `src/terminus/mosaic.py` or `src/terminus/client.py`, or adds `subprocess`/`Popen`/`child_process`/`spawn(` anywhere under `src/**/*.py` or `app/**/*.ts`; skip when none of those change, `app/main/sidecar.ts`-only diffs, `*.test.ts` and `tests/**`.
**Contents:** FLAG (P1) · FLAG (P2) when a `subprocess` call · Do NOT flag

terminus shells out constantly: **ffmpeg** for every frame
capture, and six **Hugin** binaries (`pto_gen`, `cpfind`, `cpclean`,
`autooptimiser`, `nona`, `pano_modify`) for registration. A hang or
an unhelpful error here costs an observing session that cannot be repeated on
demand. Spawning the sidecar (`app/main/sidecar.ts`) is out of scope here:
sidecar-lifecycle-reviewer owns its startup timeout and stderr.

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
