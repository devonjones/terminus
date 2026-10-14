# api-contract-reviewer
**Checks:** Sidecar route/payload changes made on one side only, hand-written TS payload types, breaking changes without a version bump, and JS re-implementations of engine math without a shared golden fixture.
**Spawn when:** diff touches `src/terminus/server/**` (incl. `schema.json`), `app/src/api/**` (incl. `generated.ts`), `app/main/**`, or the version fields in `app/package.json`, `pyproject.toml`, `src/terminus/__init__.py`; skip if the diff touches none of these.

Review the sidecar routes (`src/terminus/server/**`), their TypeScript side
(`app/src/api/**`, `app/main/**`), and any shared schema or fixture.

**FLAG (P2) when a PR:**

- Changes a route or payload on one side only.
- Hand-writes TS types for a payload instead of generating them from the
  sidecar's schema. Payloads are defined once in `src/terminus/server/schema.json`;
  `npm run gen:api` writes `app/src/api/generated.ts` (CI fails if it is stale), and
  `tests/test_server.py` validates every response against the same file.
- Makes a breaking change without a version bump. The UI refuses a sidecar of a
  different version, so the versions in `app/package.json`, `pyproject.toml` and
  `src/terminus/__init__.py` move together.
- Re-implements engine math in JS without a shared golden fixture read by both
  the vitest and pytest suites (epic TESTING).

---
