import { app, BrowserWindow, dialog, ipcMain, session, type IpcMainInvokeEvent } from "electron";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import type { AppState } from "../src/api/types";
import { request, startSidecar, stopSidecar, view, type Sidecar } from "./sidecar";

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

const PHOTO_EXTENSIONS = ["jpg", "jpeg", "png", "tif", "tiff"];

// Every renderer call is checked here: the sender must be our page, and each
// argument must have the shape the sidecar expects before it is forwarded.
function registerIpc(sc: Sidecar, win: () => BrowserWindow | undefined) {
  const handle = (channel: string, fn: (...args: unknown[]) => unknown) =>
    ipcMain.handle(channel, (e, ...args) => (fromOurPage(e), fn(...args)));
  const shortString = (v: unknown, what: string) => {
    if (typeof v !== "string" || v.length === 0 || v.length > 64)
      throw new Error(`${what} must be a short string`);
    return v;
  };
  // Copying hundreds of full-size photos takes longer than an ordinary call.
  const create = (photos: string[]) => request<AppState>(sc, "/sites", { photos }, 10 * 60_000);

  handle("state:get", () => request<AppState>(sc, "/state"));
  handle("state:tab", (tab) =>
    request<AppState>(sc, "/state/tab", { tab: shortString(tab, "tab") }),
  );
  handle("sites:list", () => request(sc, "/sites"));
  handle("site:open", (slug) =>
    request<AppState>(sc, "/site/open", { slug: shortString(slug, "site") }),
  );
  handle("site:spin", (deg) => {
    if (typeof deg !== "number" || !Number.isFinite(deg)) throw new Error("spin must be a number");
    return request<AppState>(sc, "/site/spin", { deg });
  });
  handle("site:horizon", () => view(sc, "/site/horizon"));
  handle("site:disc", () => view(sc, "/site/disc"));
  handle("site:image", (name) => {
    if (name !== "panorama" && name !== "disc") throw new Error("unknown image");
    return view(sc, `/site/${name}.jpg`);
  });
  // The file dialog is opened here, not in the page, so the page never chooses paths.
  handle("site:pick", async () => {
    const w = win();
    const opts = {
      title: "Choose the photographs of this site",
      properties: ["openFile", "multiSelections"] as ("openFile" | "multiSelections")[],
      filters: [{ name: "Photos", extensions: PHOTO_EXTENSIONS }],
    };
    const res = w ? await dialog.showOpenDialog(w, opts) : await dialog.showOpenDialog(opts);
    return res.canceled || res.filePaths.length === 0 ? null : create(res.filePaths);
  });
  // Dropped files: the preload turns each File into its path. The sidecar checks
  // that every one is an existing photo before copying anything.
  handle("site:create", (paths) => {
    if (
      !Array.isArray(paths) ||
      paths.length === 0 ||
      paths.length > 500 ||
      !paths.every(
        (p) =>
          typeof p === "string" &&
          p.length < 4096 &&
          path.isAbsolute(p) &&
          PHOTO_EXTENSIONS.includes(path.extname(p).slice(1).toLowerCase()),
      )
    )
      throw new Error("expected a list of absolute photo paths");
    return create(paths as string[]);
  });
}

// A packaged app will carry its own Hugin (terminus-71.6); in dev, a vendored copy
// if there is one, else PATH.
function huginDir(): string | undefined {
  if (app.isPackaged) return path.join(process.resourcesPath, "hugin", "bin");
  const vendored = path.resolve(__dirname, "..", "vendor", "hugin", "bin");
  return existsSync(vendored) ? vendored : undefined;
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
  return win;
}

async function boot() {
  // ponytail: dev only; the packaged frozen sidecar (PyInstaller) lands with terminus-71.6.
  const repo = path.resolve(__dirname, "..", "..");
  const venvPython = process.platform === "win32" ? ".venv/Scripts/python.exe" : ".venv/bin/python";
  const python = process.env.TERMINUS_PYTHON ?? path.join(repo, venvPython);
  mkdirSync(path.dirname(logPath()), { recursive: true });
  const hugin = huginDir();
  const sc = (sidecar = await startSidecar({
    command: python,
    args: [
      "-m",
      "terminus.server",
      "--sites",
      path.join(app.getPath("userData"), "sites"),
      ...(hugin ? ["--hugin", hugin] : []),
      ...(dev ? ["--dev"] : []),
    ],
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

  // Handlers before the window, so the page's first call finds them.
  const holder: { win?: BrowserWindow } = {};
  registerIpc(sc, () => holder.win);
  holder.win = createWindow();
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
