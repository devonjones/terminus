# error-handling-reviewer
**Checks:** Swallowed or masked exceptions (`except: pass`, default return, `return` in `finally`) as P1; bare `except:` or `except Exception` without re-raise as P2; `raise` in `except` without `from e` as P3; floating promises and swallowing catch in IPC/click handlers in `app/**` as P2.
**Spawn when:** diff adds or changes `try`/`except`/`finally`/`raise`, `.catch`, `catch (` or promise-returning calls in `src/**/*.py` or `app/**/*.ts`; skip tests, docs, and diffs with no error-path changes.

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

**JavaScript/TypeScript (`app/**`):** FLAG (P2) an unhandled promise rejection
(a floating promise with no `.catch`/`await`), or a `catch` in an IPC handler or
click handler that swallows the error without showing or logging it.

**Do NOT flag** the deliberate swallows listed under
`strategic-fragility-reviewer`; they are argued there and are correct.

---
