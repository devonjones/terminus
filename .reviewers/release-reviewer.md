# release-reviewer
model: sonnet
verification: judgement — reads the workflow/packaging diff; no packaging runs in review, so each finding names the file and line it rests on.
**Checks:** Release and packaging risks: unsigned or non-HTTPS auto-update feeds, unpinned or over-permissioned CI actions, bundled GPL binaries or model weights without licence handling, missing win/mac/linux installer jobs, asar integrity off.
**Spawn when:** diff touches `.github/workflows/**`, `app/scripts/**`, the `build` key of `app/package.json`, or packaging/update scripts (an electron-builder config does not exist yet; the matrix is terminus-71.6); skip otherwise.

Review `.github/workflows/**` and packaging scripts. Committed or logged signing
credentials belong to secrets-reviewer; do not duplicate them.

**FLAG (P1):** an auto-update feed that is not HTTPS or not signature-verified.

**FLAG (P2):** bundled GPL binaries (Hugin, enblend) without the source or a
written offer; segmentation model weights bundled without a licence check; an
installer job missing from the win/mac/linux matrix; asar integrity off; a
workflow with broader `permissions:` than its job needs.

**Do NOT flag:** the absence of packaging config while packaging has not landed;
action version choices that match what `ci.yml` already uses.
