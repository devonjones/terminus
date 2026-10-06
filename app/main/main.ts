import { app, BrowserWindow, dialog, ipcMain, session, type IpcMainInvokeEvent } from "electron";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import type { AppState } from "../src/api/types";
import { request, startSidecar, stopSidecar, type Sidecar } from "./sidecar";

// Dev mode exposes the Chromium DevTools protocol on 127.0.0.1 (Chromium's
// default bind) so Playwright can drive the real window, and the sidecar's
// /dev/state. Never in a packaged build. Port 0 lets Chromium pick a free port
// (a fixed 9222 collided with a desktop Chrome) and report it in DevToolsActivePort.
const dev = process.argv.includes("--dev") && !app.isPackaged;
if (dev) app.commandLine.appendSwitch("remote-debugging-port", "0");
else if (app.isPackaged && app.commandLine.hasSwitch("remote-debugging-port")) app.exit(1);

const indexHtml = path.join(__dirname, "index.html");
const indexUrl = pathToFileURL(indexHtml).href;
let sidecar: Sidecar | undefined;
let quitting = false;
const logPath = () => path.join(app.getPath("logs"), "sidecar.log");

function fromOurPage(e: IpcMainInvokeEvent) {
  if (e.senderFrame?.url !== indexUrl) throw new Error("IPC from an unexpected frame");
}

function writeDevSession(sc: Sidecar) {
  const active = path.join(app.getPath("userData"), "DevToolsActivePort");
  const cdpPort = readFileSync(active, "utf8").split("\n")[0];
  const dir = path.join(app.getPath("userData"), "dev");
  mkdirSync(dir, { recursive: true });
  const file = path.join(dir, "session.json");
  const info = { cdp: `http://127.0.0.1:${cdpPort}`, sidecar: sc.url, token: sc.token };
  writeFileSync(file, JSON.stringify(info, null, 2) + "\n", { mode: 0o600 });
  console.log(`terminus dev session: ${file}`);
}

function createWindow() {
  const win = new BrowserWindow({
    width: 1100,
    height: 750,
    title: "terminus",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false,
      webSecurity: true,
    },
  });
  win.loadFile(indexHtml);
}

async function boot() {
  // ponytail: dev only; the packaged frozen sidecar (PyInstaller) lands with terminus-71.6.
  const repo = path.resolve(__dirname, "..", "..");
  const venvPython = process.platform === "win32" ? ".venv/Scripts/python.exe" : ".venv/bin/python";
  const python = process.env.TERMINUS_PYTHON ?? path.join(repo, venvPython);
  mkdirSync(path.dirname(logPath()), { recursive: true });
  const sc = (sidecar = await startSidecar({
    command: python,
    args: ["-m", "terminus.server", ...(dev ? ["--dev"] : [])],
    cwd: repo,
    logPath: logPath(),
    expectedVersion: app.getVersion(),
  }));
  sc.proc.on("exit", (code, signal) => {
    if (quitting) return;
    dialog.showErrorBox(
      "terminus engine stopped",
      `It exited (${signal ?? `code ${code}`}).\n\nLog: ${logPath()}`,
    );
    app.quit();
  });
  if (dev) writeDevSession(sc);

  ipcMain.handle("state:get", (e) => (fromOurPage(e), request<AppState>(sc, "/state")));
  ipcMain.handle("state:tab", (e, tab: unknown) => {
    fromOurPage(e);
    if (typeof tab !== "string" || tab.length > 32) throw new Error("tab must be a short string");
    return request<AppState>(sc, "/state/tab", { tab });
  });
  createWindow();
}

app.on("web-contents-created", (_e, wc) => {
  wc.on("will-navigate", (e) => e.preventDefault());
  wc.on("will-attach-webview", (e) => e.preventDefault());
  wc.setWindowOpenHandler(() => ({ action: "deny" }));
});

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => {
    const win = BrowserWindow.getAllWindows()[0];
    if (win?.isMinimized()) win.restore();
    win?.focus();
  });
  app
    .whenReady()
    .then(() => {
      session.defaultSession.setPermissionRequestHandler((_wc, _perm, cb) => cb(false));
      return boot();
    })
    .catch((e: Error) => {
      dialog.showErrorBox("terminus could not start", `${e.message}\n\nLog: ${logPath()}`);
      app.quit();
    });
}

app.on("window-all-closed", () => app.quit());
app.on("before-quit", () => (quitting = true));
// Hold the quit until the sidecar has exited (parked, unless it had to be killed).
app.on("will-quit", (e) => {
  const proc = sidecar?.proc;
  if (!proc || proc.exitCode !== null || proc.signalCode !== null) return;
  e.preventDefault();
  stopSidecar(proc).finally(() => app.quit());
});
