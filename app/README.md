# terminus desktop app

Stage 0 (terminus-71.9): an Electron window over the Python engine, plus a dev
mode an agent can drive. There are no real tabs yet.

## Layout

| Path                      | What                                                                             |
| ------------------------- | -------------------------------------------------------------------------------- |
| `main/`                   | Electron main process. `sidecar.ts` starts, checks and stops the engine          |
| `preload/`                | The contextBridge: `window.terminus.getState()` and `setTab(tab)`, nothing else  |
| `src/`                    | Front end (plain TypeScript, no framework). `api/types.ts` is the `/state` shape |
| `scripts/dev-snap.mjs`    | Attach over CDP: click, screenshot, save `/dev/state`                            |
| `scripts/win-dev.sh`      | Run on Windows from a WSL checkout                                               |
| `e2e/`                    | Playwright-Electron smoke tests against the real sidecar                         |
| `../src/terminus/server/` | The sidecar: `python -m terminus.server [--dev]`                                 |

## How the pieces talk

The main process spawns `python -m terminus.server`, using the repo's
`.venv` (override with `TERMINUS_PYTHON`). The sidecar binds 127.0.0.1 on a
free port and prints `{"port", "token"}` on stdout. Main then checks `/health`
and refuses a sidecar whose version differs from `package.json`, so bump
`app/package.json` together with `pyproject.toml` and `src/terminus/__init__.py`
(a pytest checks it). The renderer never sees the token: it calls two named IPC functions and
main forwards them.

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
