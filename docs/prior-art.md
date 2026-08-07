# Prior art

Photographing a skyline to get an obstruction profile is not new.

- **[Georg Zotti's Stellarium landscape tutorial](https://www.archeoastronomy.org/assets/downloads/slides/ast/ast-12_seac2025-stellarium-landscape-course-notes.pdf)**
  (SEAC2025) documents the Hugin sequence terminus automates, and is the
  authoritative reference for `polygonal_horizon_list`, `angle_rotatez` and
  `maptex_top`/`bottom`.
- **`.hrz` generators**: [HRZ-Creator](https://neuronburner.com/hrz-creator/) and
  [panorama-horizon-maker](https://github.com/danngalann/panorama-horizon-maker).
  Both trace the skyline manually and set north by hand.
- **The N.I.N.A. Horizon Creator plugin** — phone frames at horizon inflections,
  angles from a phone alt/az app, skyline read by eye.
- **The solar industry, commercially, twenty years ago.** The
  [Solmetric SunEye 210](https://www.solmetric.com/product/suneye-210-shade-tool/)
  is a calibrated fisheye with compass, tilt sensor and GPS producing horizon
  altitude per degree of azimuth, exported as `.HOR` for PVsyst. Also
  HORIcatcher/Meteonorm and Solar Pathfinder.
- **Ecosystem neighbours**:
  [clear-horizons](https://github.com/njefferson/clear-horizons) solves north from
  the Sun; [seestar-mcp](https://github.com/OrangeAgente/seestar-mcp) infers
  obstruction arcs from where plate solving fails.
- **Closest published work**: Mephisto unobservable-region segmentation and
  LenghuSky-8 — all-sky cameras with star-based calibration, where the static
  obstructions are a *hand-drawn* mask rather than a segmentation output.

What this project has not found elsewhere is the combination: segmentation as the
sky/terrain decision with obstruction **type** carried into the export; a
telescope used as a fiducial source for a three-parameter orientation solve;
information-gain choice of the next column with a stopping rule on parameter
*stability* rather than residual; ceiling-limited columns as one-sided censored
bounds; and a night detector built *on* the skyglow gradient rather than
subtracting it.

That search was not exhaustive — the amateur-forum record was only sampled. If
you know of prior work here, please open an issue.

