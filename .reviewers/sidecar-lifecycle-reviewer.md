# sidecar-lifecycle-reviewer
**Checks:** Sidecar process lifecycle holes: window dies while sidecar survives holding the scope, sidecar exits without parking (park must run in `finally` on stdin EOF), or two sidecars possible (P1); no health/version handshake, stderr not logged, no startup timeout, fixed port (P2).
**Spawn when:** diff touches `app/main/**` (`main.ts`, `sidecar.ts`) or `src/terminus/server/**` (`__main__.py`, `scope.py`), or changes spawn/stop/shutdown, stdin handling, single-instance lock, port or park logic; skip `*.test.ts`, `tests/**`, and `server/sites.py`/`views.py`-only diffs that don't touch startup or shutdown.

Review `app/main/**` and `src/terminus/server/**`. The Seestar tolerates **one**
control connection, and an orphaned sidecar can hold it and leave the scope
unparked.

**FLAG (P1) when a PR:**

- Opens a path where the window dies and the sidecar survives holding the scope,
  or where the sidecar exits **without** parking. The design is that stdin EOF
  means shutdown and the park runs in `finally`; a kill on Windows skips
  handlers, so it is only the last resort.
- Makes two sidecars possible: removes the single-instance lock, or starts a
  second sidecar without stopping the first.

**FLAG (P2):** no health/version handshake between UI and sidecar (a stale
sidecar after an update); sidecar stderr not captured to a log; no startup
timeout with a plain-language error; a fixed port instead of an OS-assigned free one.

---
