# ui-reviewer
**Checks:** UI defects in the Electron renderer: unvalidated chart palettes, colour-only encoding, core actions with no keyboard path, UI changes with no dev-mode screenshot in the PR description.
**Spawn when:** diff touches `app/src/**` (`*.ts`, `index.html`, `style.css`), excluding `app/src/api/generated.ts` and `*.test.ts`-only changes; skip otherwise.

Review `app/src/**`.

**FLAG (P2) when a PR:**

- Adds a chart palette that has not been run through the dataviz
  `validate_palette` script, in both light AND dark.
- Encodes identity by colour alone (no label, shape or pattern).
- Leaves a core action with no keyboard path.
- Changes the UI and the PR description has no screenshot taken through dev
  mode (`app/scripts/dev-snap.mjs`). We look at images before shipping them.

**Do NOT flag** styling taste.

---
