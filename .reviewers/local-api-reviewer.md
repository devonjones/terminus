# local-api-reviewer
**Checks:** Sidecar HTTP API exposure: state-changing route without the per-launch token, weakened Host check or Origin refusal, non-127.0.0.1 bind, CORS headers, `/dev/*` reachable without --dev (P1); unvalidated request bodies, error/log output leaking a path or the token (P2).
**Spawn when:** diff touches `src/terminus/server/**` or `app/main/**`, or adds a route/handler, `Access-Control`, bind address, token or Host/Origin handling in them; skip `tests/**`, `*.test.ts` and `schema.json`-only edits.

Review `src/terminus/server/**` and `app/main/**`. **The telescope is behind a
localhost port.** Any web page in the user's browser can fire requests at
127.0.0.1, and DNS rebinding lets it read the answers.

**FLAG (P1) when a PR:**

- Adds a route that changes state (slew, unpark, capture, focuser, settings)
  reachable without the per-launch secret token.
- Removes or weakens the `Host` check (127.0.0.1/localhost on the bound port) or
  the refusal of requests that carry an `Origin`.
- Binds anything but 127.0.0.1.
- Adds CORS headers, above all `Access-Control-Allow-Origin: *`.
- Makes a `/dev/*` route reachable without `--dev`.

**FLAG (P2):** a request body that is not validated (type, size, allowed
values) before use; an error response or log line that leaks a filesystem path
or the token.

---
