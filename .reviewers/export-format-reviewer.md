# export-format-reviewer
**Checks:** Regressions in the files other tools parse: N.I.N.A. `.hrz` and Stellarium `.txt` (ordering, 0/360 endpoints, delimiter, header or comment leakage, duplicate azimuths, non-numeric altitude), new target formats with no shape-asserting test, and mask-YAML schema changes that break the `write_mask`/`load_mask` round trip.
**Spawn when:** A diff touches `src/terminus/export.py` (`write_mask`, `load_mask`, `to_nina_hrz`, `to_stellarium_txt`, `to_pvsyst_hor`) or adds a new `to_*`/`write_*` exporter or format-shape test in `tests/`; skip diffs that change only comments or altitude rounding.

Review the exporters in `export.py`. Other applications parse these files; a
format regression breaks a user's planning silently.

**Rules:**

- **N.I.N.A. `.hrz`**: `az alt` per line, whitespace-delimited, azimuth ascending
  integers, altitude int or decimal, `#` comments allowed, endpoints spanning
  0 and 360. FLAG (P2) a change that reorders, drops the 0/360 wrap, changes the
  delimiter, or emits non-numeric altitude.
- **Stellarium `.txt`**: same `az alt` list, ascending, **no** header/comment
  lines, no duplicate azimuths. FLAG (P2) comments leaking into this file.
- New target formats (e.g. Sky Safari image, KStars, Cartes du Ciel): FLAG (P2)
  if added without a test asserting the format's shape.
- The mask YAML is the durable, hand-editable source of truth; `load_mask` must
  round-trip what `write_mask` emits. FLAG (P2) a schema change that breaks that.

**Do NOT flag** cosmetic comment wording in `.hrz`, or altitude rounding.

---
