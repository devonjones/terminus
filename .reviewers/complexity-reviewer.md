# complexity-reviewer
model: sonnet
verification: judgement — the logic-line count is measurable (state it), but whether a function does several things is a judgement call, labelled as such.
**Checks:** New or changed `src/` functions that do several things (fail the one-sentence 'and/or' test), run past ~50-60 logic lines, or hold an extractable named block; counts logic lines, not the incident-citing comments that are house style.
**Spawn when:** A diff adds or changes a function body in `src/**/*.py`; skip anything under `tests/`, comment/docstring-only edits, and non-Python files.

Review **`src/` only
— skip `tests/`**, where long tests and per-test local imports are the house
style.

1. **"And/or" test.** Try to describe each new or changed function in one
   sentence without conjunctions. If it needs several "and"s, it is doing
   several things.
2. **One-screen rule.** Flag functions past ~50–60 lines of *logic*, and suggest
   where they split. Nested `def`s do not count toward the parent.
3. **Extractable blocks.** A block with a nameable purpose usually wants to be a
   module-level `_helper`.

**This project has an unusual comment style and it is deliberate — do NOT flag
it.** Comments and docstrings here record *why*, and frequently cite the
incident and the measured number behind a constant. That is the point: it is
what stops a future change quietly undoing a hard-won fix. Count logic lines,
not comment lines, and never propose deleting a comment that records an
observed failure or a measurement.

**Severity:** P2 for a function past the one-screen rule or doing clearly separate things; P3 for an extractable block or a borderline "and".

**Do NOT flag** the grid-search structure in `orient.fit` for nesting depth
alone; it is a search over four parameters and the shape is inherent. Do flag
it if a *new* concern is threaded through it.

It is acceptable to acknowledge complexity and defer with a beads ticket rather
than refactor inside the PR.
