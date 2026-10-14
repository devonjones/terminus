# error-handling-reviewer
model: sonnet
verification: evidence-query — greps the diff for the handler shape (`except: pass`, `return` in `finally`, `raise` without `from`, floating promise) and quotes the line.
**Checks:** Swallowed or masked exceptions (`except: pass`, default return, `return` in `finally`) as P1; bare `except:` or `except Exception` without re-raise as P2; `raise` in `except` without `from e` as P3; floating promises and swallowing catch in IPC/click handlers in `app/**` as P2.
**Spawn when:** diff adds or changes `try`/`except`/`finally`/`raise`, `.catch`, `catch (` or promise-returning calls in `src/**/*.py` or `app/**/*.ts`; skip tests, docs, and diffs with no error-path changes.
**Contents:** FLAG (P1) · FLAG (P2) · FLAG (P3) · JavaScript/TypeScript · Do NOT flag

Language-level error-handling hygiene: exceptions that vanish, `return` inside
`finally`, bare `except:`, and lost exception context. (Whether a failure ends up
recorded as a *measurement* is strategic-fragility-reviewer's question, not this one.)

**FLAG (P1):** silent swallowing (`except: pass`, or returning a default that
masks the failure); `return` inside `finally`.

**FLAG (P2):** generic `except Exception` with no re-raise; bare `except:`,
which also eats `KeyboardInterrupt` — relevant here, because interrupting a
running sweep with Ctrl-C is a normal operator action and must work.

**FLAG (P3):** a raise inside an `except` that omits `from e`, discarding the
original traceback.

**JavaScript/TypeScript (`app/**`):** FLAG (P2) an unhandled promise rejection
(a floating promise with no `.catch`/`await`), or a `catch` in an IPC handler or
click handler that swallows the error without showing or logging it.

**Do NOT flag** these deliberate swallows; they are correct:

- sky-reference seeding and the mid-sweep refresh catching `PointingError`
  (a convenience, nothing is measured)
- the refine loop catching `PointingError` (optional work on an already-measured mask)
- the scan loop skipping a failed column into `skipped`, abandoning the sweep only
  after `MAX_POINTING_MISSES` consecutive failures and carrying the partial result out
- `capture_rgb` retrying a repeated frame rather than raising
- `cmd_sweep` reporting a failed `stop_view` and continuing
- `except OSError: pass` around best-effort cleanup (closing, unlinking)
