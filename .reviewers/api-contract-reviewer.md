# api-contract-reviewer
model: sonnet
verification: evidence-query — greps both sides of a route/payload for the field or path that changed, and runs `npm run gen:api` in a scratch copy to show `generated.ts` is stale.
**Checks:** Sidecar route/payload changes made on one side only, hand-written TS payload types, and breaking changes without a version bump.
**Spawn when:** diff touches `src/terminus/server/**` (incl. `schema.json`), `app/src/api/**` (incl. `generated.ts`), `app/main/**`, or the version fields in `app/package.json`, `pyproject.toml`, `src/terminus/__init__.py`; skip if the diff touches none of these.

Review the sidecar routes (`src/terminus/server/**`) and their TypeScript side
(`app/src/api/**`, `app/main/**`).

**FLAG (P2) when a PR:**

- Changes a route or payload on one side only.
- Hand-writes TS types for a payload instead of generating them from the
  sidecar's schema. Payloads are defined once in `src/terminus/server/schema.json`;
  `npm run gen:api` writes `app/src/api/generated.ts` (CI fails if it is stale), and
  `tests/test_server.py` validates every response against the same file.
- Makes a breaking change without a version bump. The UI refuses a sidecar of a
  different version, so the versions in `app/package.json`, `pyproject.toml` and
  `src/terminus/__init__.py` move together.

**Do NOT flag:** hand-edits to `generated.ts` only when they match a fresh
`gen:api` output; additive optional fields; route auth, Host or Origin handling
(local-api-reviewer); JS re-implementing engine math (coordinate-correctness-reviewer).
