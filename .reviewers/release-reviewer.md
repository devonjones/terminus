# release-reviewer
**Checks:** Release and packaging risks: committed or logged signing/update credentials, unsigned or non-HTTPS auto-update feeds, bundled GPL binaries or model weights without licence handling, missing win/mac/linux installer jobs, asar integrity off.
**Spawn when:** diff touches `.github/workflows/**`, an electron-builder config (`electron-builder.*` or the `build` key of `app/package.json`), `app/scripts/**`, or packaging/signing/update scripts; skip otherwise.

Review electron-builder config, `.github/workflows/**`, and packaging scripts.

**FLAG (P1):** signing certificates, Apple credentials or update keys committed,
or echoed in CI logs; an auto-update feed that is not HTTPS or not
signature-verified.

**FLAG (P2):** bundled GPL binaries (Hugin, enblend) without the source or a
written offer; segmentation model weights bundled without a licence check; an
installer job missing from the win/mac/linux matrix; asar integrity off.
