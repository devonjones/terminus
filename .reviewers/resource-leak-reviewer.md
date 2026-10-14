# resource-leak-reviewer
**Checks:** Unclosed resources in Python: `open()` without a context manager, temp files not removed on every path incl. retries, `lru_cache` without maxsize on measured-data keys, sockets/subprocesses/RTSP views with no close path (P2); not the single long-lived Seestar socket.
**Spawn when:** diff to `src/**/*.py` that adds `open(`, `tempfile`/`NamedTemporaryFile`/`mkstemp`, `lru_cache`/`cache`, `socket`, `Popen`, or RTSP/ffmpeg view code (`client.py`, `mosaic.py`, `server/views.py`); skip `tests/**`, docs and diffs with none of these.

**Re-enabled.** It was disabled on the grounds that terminus has "no long-lived
resources", and that was simply wrong. It has:

- a long-lived TCP control socket that reconnects mid-run
- one temporary file per captured frame, and a sweep captures thousands
- an RTSP scenery view that must be stopped, and which has already been observed
  to freeze after hours and serve one stale frame while ten azimuths were
  recorded as blocked
- caches on the fit's hot path

**FLAG (P2) when a PR:**

- Opens a file without a context manager. `for line in open(path)` leaves the
  handle to the garbage collector.
- Creates a temporary file whose removal is not guaranteed on every path,
  including the retry paths.
- Adds `functools.lru_cache` with no `maxsize` where keys come from measured
  data rather than a fixed set.
- Leaves a socket, subprocess or view without a defined close path — including
  on the error path, which is where it will actually matter.

**Do NOT flag:** the deliberately long-lived `Seestar` socket. One control
connection is a hard constraint of the device — it tolerates exactly one, and
opening a second has broken live runs — so it is held for the session on purpose
and reconnected rather than reopened per call.

---
