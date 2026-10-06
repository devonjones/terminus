# terminus desktop app

An Electron window over the Python engine, plus a dev mode an agent can drive.
Stage 1 (terminus-71.1): drop in the photographs of a site, and the app stitches
them, reads the horizon, and shows the panorama and the polar disc, with a
rough spin for a site the telescope has not oriented yet.

## Layout

| Path                      | What                                                                      |
| ------------------------- | ------------------------------------------------------------------------- |
| `main/`                   | Electron main process. `sidecar.ts` starts, checks and stops the engine   |
| `preload/`                | The contextBridge: named calls only (`TerminusApi` in `src/api/types.ts`) |
| `src/`                    | Front end (plain TypeScript, no framework)                                |
| `src/api/generated.ts`    | Payload types, generated from `../src/terminus/server/schema.json`        |
| `scripts/dev-snap.mjs`    | Attach over CDP: click, screenshot, save `/dev/state`                     |
| `scripts/win-dev.sh`      | Run on Windows from a WSL checkout                                        |
| `e2e/`                    | Playwright-Electron smoke tests against the real sidecar                  |
| `../src/terminus/server/` | The sidecar: `python -m terminus.server [--dev]`                          |

## How the pieces talk

The main process spawns `python -m terminus.server`, using the repo's
`.venv` (override with `TERMINUS_PYTHON`). The sidecar binds 127.0.0.1 on a
free port and prints `{"port", "token"}` on stdout. Main then checks `/health`
and refuses a sidecar whose version differs from `package.json`, so bump
`app/package.json` together with `pyproject.toml` and `src/terminus/__init__.py`
(a pytest checks it). The renderer never sees the token or a file path: it calls
named IPC functions, and main checks each argument before forwarding it.

Every payload is defined once, in `src/terminus/server/schema.json`.
`npm run gen:api` writes the TS types from it (CI fails if they are stale), and
`tests/test_server.py` validates every sidecar response against it.

## Sites

A site is a folder the app owns, under `<userData>/sites/<slug>/`. A new site
copies the dropped (or chosen) photos into `photos/`, then the sidecar runs
`terminus mosaic` and `terminus skymask` in-process on a worker thread. These
are the same code and the same files as the CLI (`equirect.png`,
`equirect.coverage.npy`, `photo_mask.yaml`), but no `terminus` command or
Python install is needed. A site that `terminus orient` has written an
`oriented.yaml` into shows that mask instead.

What the app draws comes from the engine: the disc overlays are
`polar.disc_xy` pixels and the disc photo is `polar.project`, so nothing is
re-projected in JavaScript. Rough spin rotates the photo and its lines together,
and the sidecar keeps the angle (`state.spin`) for export.

**Hugin ships with the app.** A packaged build passes `--hugin
<resources>/hugin/bin`, and the engine runs every Hugin tool from there
(`mosaic.HUGIN_BIN`), never from PATH. In dev, main passes `app/vendor/hugin/bin`
if it exists, and otherwise the sidecar falls back to PATH. Bundling the
binaries is terminus-71.6.

Known limit: quitting while a site is building leaves it half-built; nothing
resumes it yet.

Quitting closes the sidecar's stdin, and the sidecar parks the scope and
exits. If the main process crashes, the OS closes the pipe, so the sidecar
still parks. A kill (the fallback after 5 s) skips the park on Windows. The sidecar's stderr goes to `<logs>/sidecar.log`, which is
`%APPDATA%\terminus\logs` on Windows and `~/.config/terminus/logs` on Linux.

## Develop

```sh
uv sync --dev            # repo root: the sidecar's .venv
cd app && npm ci
npm run dev              # build and launch with --dev
npm run lint && npm run typecheck && npm run format:check && npm test
npm run e2e              # launches Electron; needs a display (CI uses xvfb-run)
```

## Dev mode

`--dev` (ignored in a packaged build) does three things:

- opens Chromium's DevTools protocol on a free 127.0.0.1 port;
- enables the sidecar's `GET /dev/state`, the app state plus recent log lines;
- writes `<userData>/dev/session.json` holding `{cdp, sidecar, token}`.
  `<userData>` is `%APPDATA%\terminus` on Windows and `~/.config/terminus` on Linux.

`node scripts/dev-snap.mjs [--click <selector>] [--out <dir>]` attaches to the
running window. It optionally clicks a Playwright selector such as `text=Horizon`,
then writes `screenshot.png` and `state.json` (to the dev directory by default).
For anything more, write a Playwright script with `chromium.connectOverCDP(session.cdp)`.

## Windows app, agent in WSL

Electron, its npm install, and the sidecar's Python must all be Windows builds,
and npm does not work on a `\\wsl$` path. Under WSL2 without mirrored
networking, WSL also cannot reach Windows' 127.0.0.1. So nothing crosses the
network. Everything crosses the process boundary via WSL interop instead:

```sh
app/scripts/win-dev.sh run     # rsync to %LOCALAPPDATA%\terminus\src, install, npm run dev (blocks)
app/scripts/win-dev.sh snap --click text=Orient   # dev-snap.mjs under Windows node.exe
app/scripts/win-dev.sh state   # /dev/state via Windows curl.exe
```

Read the screenshot from WSL at
`/mnt/c/Users/<you>/AppData/Roaming/terminus/dev/screenshot.png`.

`run` excludes `.git`, `.venv`, `node_modules`, `captures/`, `scratch/`, `*.pem`
and `config.toml`, so the interop key never leaves WSL. On first run, and
whenever `pyproject.toml` or `package-lock.json` changes, it builds a Windows
`.venv` with `python -m venv` and `pip install -e .`, and runs `npm ci`. The
Windows host needs Python ≥ 3.11 and Node 22 on `PATH`.
