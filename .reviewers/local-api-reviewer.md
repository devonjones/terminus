# local-api-reviewer
model: sonnet
verification: evidence-query — greps the route table and handler for the token, Host and Origin checks; where possible quotes the request that gets through (e.g. a bad Host) from an in-process call as `tests/test_server.py` does.
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

**Do NOT flag:**

- A read-only route that the existing code already leaves unauthenticated (check
  how neighbouring routes behave before flagging), or a `/dev/*` route that is
  registered only under `--dev`.
- Whether a motion route runs the Sun check (sun-safety-reviewer) or whether the
  TS and Python payloads agree (api-contract-reviewer).
- Test code that builds a server with a fixed token or port.
