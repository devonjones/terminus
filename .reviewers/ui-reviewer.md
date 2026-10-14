# ui-reviewer
model: sonnet
verification: judgement — reads the renderer diff; colour-only encoding and keyboard reachability are checked by reading the markup and handlers, each finding names the element and line.
**Checks:** UI defects in the Electron renderer: chart palettes with no stated light/dark contrast check, colour-only encoding, core actions with no keyboard path.
**Spawn when:** diff touches `app/src/**` (`*.ts`, `index.html`, `style.css`), excluding `app/src/api/generated.ts` and `*.test.ts`-only changes; skip otherwise.

Review `app/src/**`.

**FLAG (P2) when a PR:**

- Adds a chart palette whose colours are not defined for both light AND dark
  (e.g. only one theme's values in `style.css`), or whose series are
  indistinguishable without colour.
- Encodes identity by colour alone (no label, shape or pattern).
- Leaves a core action with no keyboard path (a click handler on a non-focusable
  element, no key handler).

**Do NOT flag** styling taste, generated code, or whether the PR description
carries a screenshot (the reviewer does not see it).
