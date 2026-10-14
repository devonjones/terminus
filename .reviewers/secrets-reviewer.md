# secrets-reviewer
model: sonnet
verification: evidence-query — `git grep -n "BEGIN .*PRIVATE KEY"` on the diff, `git check-ignore -v x.pem config.toml`, and a grep of log/print calls near key loading; quotes the output.
**Checks:** Interop-key and credential leaks: key material, `.pem` contents or `config.toml` committed, embedded, logged or printed; `*.pem`/`config.toml` dropped from `.gitignore`; test fixtures holding a real key; the README's section 1201(f) framing weakened or the key bundled, and signing certificates or notarization/update keys committed or echoed to CI logs (this reviewer owns all credentials).
**Spawn when:** Any diff that touches `.gitignore`, `pyproject.toml`, `README.md`, `config.example.toml`, `.github/workflows/**`, `src/terminus/config.py`, `client.py`, or `cli.py`, adds a `*.pem`, `*.key`, `*.p12`, `*.toml` or fixture file, or adds logging/printing near config or key loading; skip diffs touching only pure-math modules, tests with no new fixtures, and docs that merely describe how a user extracts their own key.

The Seestar interop key is an RSA **private key**. It must never enter the repo.

**FLAG (P1) when a PR:**

- Commits, embeds, hard-codes, logs, or prints key material, a `.pem` path's
  contents, or `config.toml`.
- Removes `*.pem` / `config.toml` from `.gitignore`, or adds a test fixture
  containing a real key.
- Weakens the README's interoperability framing (§1201(f), key extracted by the
  owner, never shipped) or bundles the key with the package.

**Also FLAG (P1)** code-signing certificates, Apple Developer ID / notarization
credentials, or auto-update signing keys committed, embedded, or echoed into CI
logs. They belong in CI secrets only.

**Do NOT flag** loading the key from a user-configured path at runtime (the
intended design), documentation *describing* how a user extracts their own, or `config.example.toml`, which legitimately names a `.pem` path.
