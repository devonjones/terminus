# export-format-reviewer
model: sonnet
verification: mutation — runs the exporter on a small synthetic mask in a scratch copy and parses the output as the target tool would (line shape, ordering, endpoints).
**Checks:** Regressions in the files other tools parse: N.I.N.A. `.hrz`, Stellarium `.txt`, PVsyst `.hor` (ordering, 0/360 endpoints, delimiter, header or comment leakage, duplicate azimuths, non-numeric altitude), the Sky Safari PNG, new target formats with no shape-asserting test, and mask-YAML schema changes that break the `write_mask`/`load_mask` round trip.
**Spawn when:** a diff touches `src/terminus/export.py` (`write_mask`, `load_mask`, `to_nina_hrz`, `to_stellarium_txt`, `to_pvsyst_hor`) or `src/terminus/landscape.py` (`to_skysafari_png`), or adds a new `to_*`/`write_*` exporter or format-shape test in `tests/`; skip diffs that change only comments or altitude rounding.
**Contents:** Rules · Do NOT flag

Review the exporters in `export.py` and `landscape.py`. Other applications parse
these files; a format regression breaks a user's planning silently.

**Rules:**

- **N.I.N.A. `.hrz`**: `az alt` per line, whitespace-delimited, azimuth ascending
  integers, altitude int or decimal, `#` comments allowed, endpoints spanning
  0 and 360. FLAG (P2) a change that reorders, drops the 0/360 wrap, changes the
  delimiter, or emits non-numeric altitude.
- **Stellarium `.txt`**: same `az alt` list, ascending, **no** header/comment
  lines, no duplicate azimuths. FLAG (P2) comments leaking into this file.
- **PVsyst `.hor`** (`to_pvsyst_hor`): azimuth/height per line; text lines are
  comments to PVsyst, so a header is allowed, and it must state that azimuth is
  OUR convention (0 = true north, increasing toward east). FLAG (P2) a change that
  drops that statement, flips the azimuth convention, or breaks ascending order.
- **Sky Safari PNG** (`landscape.py` `to_skysafari_png`): a PNG carries no comment
  line, so it is gated on orientation (`allow_unoriented`). FLAG (P2) a change
  that lets an unoriented panorama through, or alters size or north/east layout
  without a test pinning it.
- New target formats (e.g. KStars, Cartes du Ciel): FLAG (P2) if added without a
  test asserting the format's shape.
- The mask YAML is the durable, hand-editable source of truth; `load_mask` must
  round-trip what `write_mask` emits. FLAG (P2) a schema change that breaks that.

**Do NOT flag** cosmetic comment wording in `.hrz`, or altitude rounding.
