# coordinate-correctness-reviewer
model: sonnet
verification: mixed — mutation/evidence-query for numeric claims (evaluate the function at a concrete az/RA value, or across the 0/360 wrap, and quote the result); judgement for convention choices, labelled as such.
**Checks:** Azimuth/RA/Dec convention errors (az 0 = true north, increasing east; RA hours vs degrees), sign flips or mirrored/rotated output, 0/360 wrap mishandling in `Horizon` interpolation, and JS projection code that mirrors or rotates the polar disc.
**Spawn when:** a diff touches `src/terminus/sweep.py`, `horizon.py`, `orient.py`, `plan.py`, `polar.py`, or `app/src/**` and changes `altaz_to_radec`, `radec_to_altaz`, `ang_sep`, `wrap180`, `Horizon` interpolation, `orient` residuals, or sin/cos/negation/azimuth arithmetic or projection/rotation code; skip comment-, docs- or test-only diffs and exporter screen-space image conventions.
**Contents:** Ground truth · FLAG (P1) · FLAG (P2) · App · Do NOT flag

Review coordinate math and conventions in `sweep.py` (`Sky`, `ang_sep`,
`wrap180`), `horizon.py`, `orient.py`, `plan.py` and `polar.py`. A sign or
convention error silently produces a horizon that's mirrored or rotated, which
is worse than no horizon.

**Ground truth:**

- Azimuth is **true north = 0, increasing toward east** (astropy AltAz
  convention), everywhere: sweep, mask, and every exporter.
- Altitude is degrees above the horizon. RA is hours, Dec is degrees.
- Pointing is az/alt -> RA/Dec (`altaz_to_radec`) for the *moment of pointing*,
  then goto; verification converts the settled RA/Dec back with `radec_to_altaz`.

**FLAG (P1)** a new sine/cosine sign, angle negation, or azimuth mirror; RA
hours mixed with RA degrees; azimuth measured from south or counterclockwise;
or a `Horizon` interpolation that mishandles the 0/360 wrap.

**FLAG (P2)** hard-coded site constants that should come from config/`Sky`.

**App:** the same ground truth applies to JS projection and rotation code in
`app/src/**`. On the polar disc, north is up and east is right, as in `polar.py`.
FLAG (P1) a mirrored or rotated disc.

**Do NOT flag** the documented EQ-mode fact that `scope_get_horiz_coord` is
untrusted, screen-space image conventions in exporters (handled per format), or
a missing value-pinning test (test-coverage-reviewer).
