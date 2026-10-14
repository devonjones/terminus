# electron-security-reviewer
model: sonnet
verification: evidence-query — greps the window/webPreferences/contextBridge/ipcMain code for the flag or exposure and quotes the line; no behavioural proof is claimed.
**Checks:** Electron hardening regressions: nodeIntegration on or contextIsolation/sandbox off, generic ipcRenderer/invoke exposed via contextBridge, remote URLs loaded, unlocked navigation or window.open, unvalidated shell.openExternal, CDP port outside --dev (P1); unvalidated ipcMain.handle args, no CSP, webSecurity false (P2); not the --dev debug port.
**Spawn when:** diff touches `app/main/**`, `app/preload/**`, or `app/src/index.html`, or adds `BrowserWindow`, `webPreferences`, `contextBridge`, `ipcMain`, `shell.openExternal` or `remote-debugging-port` in `app/**/*.ts`; skip everything else and `*.test.ts`-only diffs.

Review `app/main/**` and `app/preload/**`. The renderer will display files from
a site folder and talk to an engine that moves a telescope; a renderer that can
reach Node or arbitrary IPC is a remote-code path into both.

**FLAG (P1) when a PR:**

- Turns `nodeIntegration` on, or `contextIsolation` or `sandbox` off, for any
  window or webContents.
- Exposes `ipcRenderer`, a generic `invoke(channel, ...)`, or any pass-through
  through `contextBridge`, instead of named, narrow functions.
- Loads remote content (any `http(s)://` URL) into a window.
- Leaves navigation unlocked: no `will-navigate` prevention, or a
  `setWindowOpenHandler` that allows.
- Calls `shell.openExternal` on a URL that is not validated against an allowlist.
- Makes the CDP `--remote-debugging-port` reachable outside `--dev`: in a packaged
  build, or bound to anything but 127.0.0.1.

**FLAG (P2):** an `ipcMain.handle` that does not validate its arguments and
`event.senderFrame`; a page without a Content-Security-Policy; `webSecurity: false`.

**Do NOT flag** the remote-debugging port in `--dev`. It exists so an agent can
drive the window. It is gated on `!app.isPackaged`.
