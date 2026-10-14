# coordinate-correctness-reviewer
**Checks:** Azimuth/RA/Dec convention errors (az 0 = true north, increasing east; RA hours vs degrees), sign flips or mirrored/rotated output, 0/360 wrap mishandling in `Horizon` interpolation, unpinned changes to coordinate transforms, and JS projection code that re-implements engine math or mirrors the polar disc.
**Spawn when:** A diff touches `src/terminus/sweep.py`, `horizon.py`, `orient.py`, `plan.py`, `polar.py`, or `app/src/**` and changes `altaz_to_radec`, `radec_to_altaz`, `ang_sep`, `wrap180`, `Horizon` interpolation, `orient` residuals, or sin/cos/negation/azimuth arithmetic or projection/rotation code; skip comment-, docs- or test-only diffs and exporter screen-space image conventions.

Review coordinate math and conventions in `sweep.py` (`Sky`, `ang_sep`,
`wrap180`) and `horizon.py`. A sign or convention error silently produces a
horizon that's mirrored or rotated, which is worse than no horizon.

**Ground truth:**

- Azimuth is **true north = 0, increasing toward east** (astropy AltAz
  convention), everywhere: sweep, mask, and every exporter.
- Altitude is degrees above the horizon. RA is hours, Dec is degrees.
- Pointing is az/alt -> RA/Dec (`altaz_to_radec`) for the *moment of pointing*,
  then goto; verification converts the settled RA/Dec back with `radec_to_altaz`.

**FLAG (P1)** a new sine/cosine sign, angle negation, or azimuth mirror; RA
hours mixed with RA degrees; azimuth measured from south or counterclockwise;
or a `Horizon` interpolation that mishandles the 0/360 wrap.

**FLAG (P2)** a change to `altaz_to_radec`/`radec_to_altaz`/`ang_sep`/`Horizon`
interpolation without a value-pinning test, or hard-coded site constants that
should come from config/`Sky`.

**App:** the same ground truth applies to JS projection and rotation code in
`app/src/**`. On the polar disc, north is up and east is right, as in `polar.py`.
FLAG (P1) a mirrored or rotated disc. FLAG (P2) JS that re-implements engine
math without reading the shared golden fixture the Python test also reads.

**Do NOT flag** the documented EQ-mode fact that `scope_get_horiz_coord` is
untrusted, or screen-space image conventions in exporters (handle per format).

---
