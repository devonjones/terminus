# terminus desktop app

An Electron window over the Python engine, plus a dev mode an agent can drive.
Drop in the photographs of a site, and the app stitches them while you watch,
lets you turn photos off and re-blend, reads the horizon (actual and planning),
and shows the panorama and the polar disc, with a rough spin for a site the
telescope has not oriented yet.

## Layout

| Path                      | What                                                                       |
| ------------------------- | -------------------------------------------------------------------------- |
| `main/`                   | Electron main process. `sidecar.ts` starts, checks and stops the engine    |
| `preload/`                | The contextBridge: named calls only (`TerminusApi` in `src/api/types.ts`)  |
| `src/`                    | Front end (plain TypeScript, no framework)                                 |
| `src/api/generated.ts`    | Payload types, generated from `../src/terminus/server/schema.json`         |
| `scripts/dev-snap.mjs`    | Attach over CDP: click, screenshot, save `/dev/state`                      |
| `scripts/win-dev.sh`      | Run on Windows from a WSL checkout                                         |
| `e2e/`                    | Playwright-Electron smoke tests against the real sidecar                   |
| `../src/terminus/server/` | The sidecar: `python -m terminus.server --sites DIR [--hugin DIR] [--dev]` |

## How the pieces talk

The main process spawns `python -m terminus.server`, using the repo's
`.venv` (override with `TERMINUS_PYTHON`). The sidecar binds 127.0.0.1 on a
free port and prints `{"port", "token"}` on stdout. Main then checks `/health`
and refuses a sidecar whose version differs from `package.json`, so bump
`app/package.json` together with `pyproject.toml` and `src/terminus/__init__.py`
(a pytest checks it). The renderer never sees the token or a file path: it calls
named IPC functions, and main checks each argument before forwarding it.

Quitting closes the sidecar's stdin, and the sidecar parks the scope and
exits. If the main process crashes, the OS closes the pipe, so the sidecar
still parks. A kill (the fallback after 5 s) skips the park on Windows.

The sidecar's stderr, including every line of a site build, goes to
`<logs>/sidecar.log`: `%APPDATA%\terminus\logs` on Windows,
`~/.config/terminus/logs` on Linux.

Every payload is defined once, in `src/terminus/server/schema.json`.
`npm run gen:api` writes the TS types from it (CI fails if they are stale), and
`tests/test_server.py` validates every sidecar response against it.

## Sites

A site is a folder the app owns, under `<userData>/sites/<slug>/`. A new site
copies the dropped (or chosen) photos into `photos/`. Then the sidecar starts a
child process of its own executable (`--pipeline <site> --kind build`), which
runs the CLI's own commands, so no `terminus` command or Python install is
needed:

1. `terminus mosaic --layers-only --events [--segment]`: Hugin registers the
   photos and remaps each to its own layer in `work/`, one photo at a time.
   With the `segment` extra installed it also labels each photo (SegFormer) and
   warps the labels through the same solve.
2. `terminus reblend --events`: the measurement blend (`equirect.png`), a
   colour-matched, feathered blend for the eye (`equirect.display.jpg`), and the
   per-photo sky vote: sky only where a strict majority of the covering photos
   say so, ties to terrain. It writes the class maps the horizon is read from
   (`equirect.terrain.classes.npy`, `.strict.classes.npy`, `.cover.npy`).
   Without segmentation the vote is the colour heuristic, which reads white
   siding as sky.
3. `terminus skymask --sky`: the native photo mask (`photo_mask.yaml`), which
   only the orientation fit reads.
4. `terminus horizon`: the map (`horizon.yaml`), actual and planning horizons
   from one reprojection of the class maps (`terminus.reproject`, the method of
   the 2026-10-05 maps). The disc draws the actual horizon as the outline of
   the contiguous sky (`polar.sky_layers`) and the planning line from the map.

A site that `terminus orient` has written an `oriented.yaml` into shows that
mask instead. Each photo's gains and sky verdict are cached beside its layer, so
turning photos off and re-blending (`--kind reblend`) takes seconds; turning one
back on that was never blended re-solves the gains for all of them.

The pipeline prints `@step` and `@event` lines, which the sidecar folds into
the job state the page polls: each photo's progress, the photo being worked on
(outlined in yellow), cpfind's matched pairs, and the horizon so far. The
Panorama tab draws all of it as it happens. A site's display name lives in
`site.json`; the folder keeps its slug.

The build runs in a child, not in the sidecar, because the sidecar will hold the
telescope: a hang or native crash while stitching must not take down the
process that parks it. A 2 h timeout, or the sidecar exiting, kills the child
and the Hugin tool it is running.

What the app draws comes from the engine: the disc overlays are
`polar.disc_xy` pixels and the disc photo is `polar.project`, so nothing is
re-projected in JavaScript. Rough spin rotates the photo and its lines
together. For now the angle lives only in the sidecar's memory (`state.spin`)
and resets when a site is opened; export (terminus-71.7) will keep it.

**Hugin will ship with the app** (terminus-71.6, which also owes Hugin's GPL
notices and source offer). A packaged build will pass `--hugin
<resources>/hugin/bin`, and the engine runs every Hugin tool from there
(`mosaic.HUGIN_BIN`), never from PATH. In dev, main passes `app/vendor/hugin/bin`
if it exists, and otherwise the sidecar falls back to PATH.

Known limit: quitting while a site is building leaves it half-built; nothing
resumes it yet (terminus-82).

## Develop

```sh
uv sync --dev --extra segment   # repo root: the sidecar's .venv (segment: torch, CPU)
cd app && npm ci
npm run dev              # build and launch with --dev
npm run lint && npm run typecheck && npm run format:check && npm test
npm run e2e              # launches Electron; needs a display (CI uses xvfb-run)
```

`win-dev.sh` installs only the base package on Windows. For segmentation there,
add CPU torch by hand in `%LOCALAPPDATA%\terminus\src`:
`.venv\Scripts\python -m pip install torch torchvision --index-url
https://download.pytorch.org/whl/cpu` and then `... pip install transformers`.
Whether a release ships torch and the (non-commercial) SegFormer weights is
terminus-71.6.

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

To build sites on Windows, vendor Hugin into the Windows copy once (an
administrative extract installs nothing and touches no PATH). Run this from
`%LOCALAPPDATA%\terminus`, with the MSI from hugin.sourceforge.io:

```bat
msiexec /a Hugin-2025.0.1-win64.msi /qn TARGETDIR=%LOCALAPPDATA%\terminus\src\app\vendor\hugin-msi
move src\app\vendor\hugin-msi\Hugin src\app\vendor\hugin
```

`run` excludes `.git`, `.venv`, `node_modules`, `captures/`, `scratch/`, `*.pem`
and `config.toml`, so the interop key never leaves WSL. On first run, and
whenever `pyproject.toml` or `package-lock.json` changes, it builds a Windows
`.venv` with `python -m venv` and `pip install -e .`, and runs `npm ci`. The
Windows host needs Python ≥ 3.11 and Node 22 on `PATH`.
