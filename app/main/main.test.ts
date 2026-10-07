// main.ts wiring: IPC checks, crash and quit handling. Electron and the sidecar are mocked.
import { EventEmitter } from "node:events";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const { EventEmitter } = require("node:events") as typeof import("node:events");
  const app = Object.assign(new EventEmitter(), {
    isPackaged: false,
    commandLine: { appendSwitch: vi.fn(), hasSwitch: vi.fn(() => false) },
    getPath: vi.fn(),
    getVersion: () => "0.1.0",
    requestSingleInstanceLock: () => true,
    whenReady: vi.fn(),
    quit: vi.fn(),
    exit: vi.fn(),
  });
  return {
    app,
    handlers: new Map<string, (...a: unknown[]) => unknown>(),
    showErrorBox: vi.fn(),
    startSidecar: vi.fn(),
    stopSidecar: vi.fn(async () => {}),
    request: vi.fn(async (_sc: unknown, p: string, body?: unknown) => ({ p, body })),
    view: vi.fn(async (_sc: unknown, p: string) => ({ view: p })),
    showOpenDialog: vi.fn(),
  };
});

vi.mock("electron", () => ({
  app: h.app,
  BrowserWindow: class {
    static getAllWindows = () => [];
    loadFile = vi.fn();
  },
  dialog: { showErrorBox: h.showErrorBox, showOpenDialog: h.showOpenDialog },
  ipcMain: { handle: (ch: string, fn: (...a: unknown[]) => unknown) => h.handlers.set(ch, fn) },
  session: { defaultSession: { setPermissionRequestHandler: vi.fn() } },
}));
vi.mock("./sidecar", () => ({
  startSidecar: h.startSidecar,
  stopSidecar: h.stopSidecar,
  request: h.request,
  view: h.view,
}));

let proc: EventEmitter & { exitCode: number | null; signalCode: string | null };

async function boot(start: () => Promise<unknown> = async () => ({ url: "u", token: "t", proc })) {
  vi.resetModules();
  h.handlers.clear();
  h.app.removeAllListeners();
  h.startSidecar.mockImplementation(start);
  let ready!: () => void;
  h.app.whenReady.mockReturnValue(new Promise<void>((r) => (ready = r)));
  await import("./main");
  ready();
  await vi.waitFor(() =>
    expect(h.handlers.size + h.showErrorBox.mock.calls.length).toBeGreaterThan(0),
  );
}

// main.ts lives in dist/ at runtime; under vitest __dirname is main/.
const ourPage = { senderFrame: { url: pathToFileURL(path.join(__dirname, "index.html")).href } };

beforeEach(() => {
  vi.clearAllMocks();
  h.app.getPath.mockReturnValue(path.join(os.tmpdir(), "terminus-main-test"));
  proc = Object.assign(new EventEmitter(), { exitCode: null, signalCode: null });
});

describe("IPC", () => {
  it("forwards calls from our page to the sidecar", async () => {
    await boot();
    expect(await h.handlers.get("state:get")!(ourPage)).toEqual({ p: "/state", body: undefined });
    expect(await h.handlers.get("state:tab")!(ourPage, "fit")).toEqual({
      p: "/state/tab",
      body: { tab: "fit" },
    });
  });

  it("refuses calls from any other frame", async () => {
    await boot();
    const evil = { senderFrame: { url: "https://evil.example/" } };
    expect(() => h.handlers.get("state:get")!(evil)).toThrow("unexpected frame");
    expect(() => h.handlers.get("state:tab")!(evil, "fit")).toThrow("unexpected frame");
    expect(() => h.handlers.get("state:get")!({ senderFrame: null })).toThrow("unexpected frame");
    expect(h.request).not.toHaveBeenCalled();
  });

  it.each([3, null, { tab: "fit" }, "x".repeat(65), ""])("refuses tab %j", async (tab) => {
    await boot();
    expect(() => h.handlers.get("state:tab")!(ourPage, tab)).toThrow("short string");
    expect(h.request).not.toHaveBeenCalled();
  });
});

describe("dev mode gating", () => {
  const argv = process.argv;
  afterEach(() => {
    process.argv = argv;
    h.app.isPackaged = false;
    h.app.commandLine.hasSwitch.mockReturnValue(false);
  });

  it("opens the DevTools port only with --dev in an unpackaged build", async () => {
    process.argv = [...argv, "--dev"];
    await boot();
    expect(h.app.commandLine.appendSwitch).toHaveBeenCalledWith("remote-debugging-port", "0");
  });

  it("ignores --dev in a packaged build", async () => {
    process.argv = [...argv, "--dev"];
    h.app.isPackaged = true;
    await boot();
    expect(h.app.commandLine.appendSwitch).not.toHaveBeenCalled();
    expect(h.app.exit).not.toHaveBeenCalled();
  });

  it("refuses to run a packaged build launched with a DevTools port", async () => {
    h.app.isPackaged = true;
    h.app.commandLine.hasSwitch.mockReturnValue(true);
    await boot();
    expect(h.app.exit).toHaveBeenCalledWith(1);
  });
});

describe("site IPC", () => {
  const call = (ch: string, ...args: unknown[]) => h.handlers.get(ch)!(ourPage, ...args);

  it("forwards the site calls to their sidecar routes", async () => {
    await boot();
    expect(await call("sites:list")).toEqual({ p: "/sites", body: undefined });
    expect(await call("site:open", "site-a")).toEqual({
      p: "/site/open",
      body: { slug: "site-a" },
    });
    expect(await call("site:spin", 120)).toEqual({ p: "/site/spin", body: { deg: 120 } });
    expect(await call("site:horizon")).toEqual({ view: "/site/horizon" });
    expect(await call("site:disc")).toEqual({ view: "/site/disc" });
    expect(await call("site:image", "disc")).toEqual({ view: "/site/disc.jpg" });
    expect(await call("site:create", ["/p/a.jpg"])).toEqual({
      p: "/sites",
      body: { photos: ["/p/a.jpg"] },
    });
    expect(await call("site:image", "disagree")).toEqual({ view: "/site/disagree.png" });
    expect(await call("site:build-image", "layer", "layer0003.tif")).toEqual({
      view: "/site/frame/layer.webp?layer=layer0003.tif",
    });
    expect(await call("site:build-image", "verdict", "layer0003.tif")).toEqual({
      view: "/site/frame/verdict.png?layer=layer0003.tif",
    });
    expect(await call("site:image", "outline")).toEqual({ view: "/site/outline.png" });
    expect(await call("site:image", "progress")).toEqual({ view: "/site/progress.png" });
    expect(await call("site:rename", "site-a", "Back yard")).toEqual({
      p: "/site/rename",
      body: { slug: "site-a", name: "Back yard" },
    });
    expect(await call("site:delete", "site-a")).toEqual({
      p: "/site/delete",
      body: { slug: "site-a" },
    });
    expect(await call("site:frames")).toEqual({ view: "/site/frames" });
    expect(await call("site:frame-image", "thumb", "a b&c.jpg")).toEqual({
      view: "/site/frame/thumb.jpg?name=a%20b%26c.jpg",
    });
    expect(await call("site:frame-image", "footprint", "a.jpg")).toEqual({
      view: "/site/frame/footprint.png?name=a.jpg",
    });
    expect(await call("site:curate", ["a.jpg"], true)).toEqual({
      p: "/site/frames",
      body: { off: ["a.jpg"], restitch: true },
    });
  });

  it.each([
    ["site:open", [3]],
    ["site:open", ["x".repeat(65)]],
    ["site:spin", ["90"]],
    ["site:spin", [Number.NaN]],
    ["site:image", ["../secrets"]],
    ["site:create", ["/p/a.jpg"]],
    ["site:create", [[]]],
    ["site:create", [[3]]],
    ["site:create", [Array(501).fill("/p/a.jpg")]],
    ["site:create", [["p/a.jpg"]]],
    ["site:create", [["/p/notes.txt"]]],
    ["site:frame-image", ["layer", "a.jpg"]],
    ["site:frame-image", ["thumb", "../a.jpg"]],
    ["site:frame-image", ["thumb", "a\\b.jpg"]],
    ["site:frame-image", ["thumb", ""]],
    ["site:curate", ["a.jpg", false]],
    ["site:curate", [["a.jpg"], "yes"]],
    ["site:curate", [["x/a.jpg"], false]],
    ["site:curate", [Array(501).fill("a.jpg"), false]],
    ["site:build-image", ["photo", "layer0003.tif"]],
    ["site:build-image", ["layer", "../layer0003.tif"]],
    ["site:build-image", ["layer", "layer0003.png"]],
    ["site:build-image", ["verdict", "label0003.tif"]],
    ["site:rename", ["site-a", 3]],
    ["site:rename", ["site-a", "x".repeat(201)]],
    ["scope:connect", [3]],
    ["scope:connect", [""]],
    ["scope:connect", ["http://10.5.2.65"]],
    ["scope:connect", ["10.5.2.65:32323"]],
    ["scope:connect", ["10.5.2.65/api"]],
    ["scope:point", ["260", 30]],
    ["scope:point", [260, Number.NaN]],
    ["scope:frame", ["2"]],
    ["scope:frame", [Number.POSITIVE_INFINITY]],
    ["site:column-edit", ["40", {}]],
    ["site:column-edit", [40, { included: "no" }]],
    ["site:column-edit", [40, { tags: ["cloud"] }]],
    ["site:column-frame", [40, "../photo_mask.yaml"]],
    ["site:column-frame", ["40", "az040_alt23.00_sky045.jpg"]],
  ])("%s refuses %j before it reaches the sidecar", async (ch, args) => {
    await boot();
    expect(() => call(ch, ...args)).toThrow();
    expect(h.request).not.toHaveBeenCalled();
    expect(h.view).not.toHaveBeenCalled();
  });

  it("every site channel checks the sender frame", async () => {
    await boot();
    const evil = { senderFrame: { url: "https://evil.example/" } };
    for (const ch of [
      "sites:list",
      "site:open",
      "site:spin",
      "site:horizon",
      "site:disc",
      "site:image",
      "site:pick",
      "site:create",
      "site:frames",
      "site:frame-image",
      "site:curate",
      "site:rename",
      "site:delete",
      "site:build-image",
      "scope:discover",
      "scope:status",
      "scope:connect",
      "scope:park",
      "scope:disconnect",
      "scope:point",
      "scope:frame",
      "scope:frame-preview",
      "site:columns",
      "site:column-edit",
      "site:columns-fit",
      "site:column-frame",
    ])
      expect(() => h.handlers.get(ch)!(evil, "x"), ch).toThrow("unexpected frame");
  });

  it("the scope channels reach their routes; motion gets minutes, not 10 s", async () => {
    await boot();
    expect(await call("scope:connect", "10.5.2.65")).toEqual({
      p: "/scope/connect",
      body: { host: "10.5.2.65" },
    });
    expect(await call("scope:status")).toEqual({ p: "/scope/status" });
    expect(await call("scope:discover")).toEqual({ p: "/scope/discover" });
    expect(await call("scope:frame", 2)).toEqual({ p: "/scope/frame", body: { exposure_ms: 2 } });
    expect(await call("scope:frame-preview")).toEqual({ view: "/scope/frame.jpg" });
    h.request.mockClear();
    expect(await call("scope:park")).toEqual({ p: "/scope/park", body: {} });
    expect(await call("scope:disconnect")).toEqual({ p: "/scope/disconnect", body: {} });
    expect(await call("scope:point", 260, 30)).toEqual({
      p: "/scope/point",
      body: { az: 260, alt: 30 },
    });
    for (const c of h.request.mock.calls) expect((c as unknown[])[3]).toBe(5 * 60_000);
  });

  it("the column channels reach their routes", async () => {
    await boot();
    expect(await call("site:columns")).toEqual({ view: "/site/columns" });
    expect(await call("site:column-edit", 40, { included: false, tags: ["pocket"] })).toEqual({
      p: "/site/columns/edit",
      body: { az: 40, included: false, tags: ["pocket"] },
    });
    expect(await call("site:column-frame", 40, "az040_alt23.00_sky045.jpg")).toEqual({
      view: "/site/column/frame.jpg?az=40&name=az040_alt23.00_sky045.jpg",
    });
    h.request.mockClear();
    expect(await call("site:columns-fit")).toEqual({ p: "/site/columns/fit", body: {} });
    expect((h.request.mock.calls[0] as unknown[])[3]).toBe(15 * 60_000);
  });

  it("copying a drop gets a long timeout, not the ordinary 10 s", async () => {
    await boot();
    await call("site:create", ["/p/a.jpg"]);
    expect((h.request.mock.calls[0] as unknown[])[3]).toBe(10 * 60_000);
  });

  it("the file dialog picks the photos; cancelling creates nothing", async () => {
    await boot();
    h.showOpenDialog.mockResolvedValueOnce({ canceled: true, filePaths: [] });
    expect(await call("site:pick")).toBeNull();
    expect(h.request).not.toHaveBeenCalled();
    h.showOpenDialog.mockResolvedValueOnce({
      canceled: false,
      filePaths: ["/p/a.jpg", "/p/b.jpg"],
    });
    expect(await call("site:pick")).toEqual({
      p: "/sites",
      body: { photos: ["/p/a.jpg", "/p/b.jpg"] },
    });
  });
});

describe("sidecar arguments", () => {
  it("keeps sites under userData and, in dev with no vendored Hugin, passes none", async () => {
    await boot();
    const args: string[] = h.startSidecar.mock.calls[0][0].args;
    const at = args.indexOf("--sites");
    expect(args[at + 1]).toBe(path.join(os.tmpdir(), "terminus-main-test", "sites"));
    expect(args).not.toContain("--hugin");
  });

  it("a packaged build always passes its bundled Hugin", async () => {
    h.app.isPackaged = true;
    const resources = (process as unknown as { resourcesPath?: string }).resourcesPath;
    (process as unknown as { resourcesPath: string }).resourcesPath = "/opt/terminus/resources";
    try {
      await boot();
      const args: string[] = h.startSidecar.mock.calls[0][0].args;
      expect(args[args.indexOf("--hugin") + 1]).toBe(
        path.join("/opt/terminus/resources", "hugin", "bin"),
      );
    } finally {
      h.app.isPackaged = false;
      (process as unknown as { resourcesPath?: string }).resourcesPath = resources;
    }
  });
});

describe("lifecycle", () => {
  it("shows a plain error and quits when the engine will not start", async () => {
    await boot(async () => Promise.reject(new Error("could not run python: ENOENT")));
    await vi.waitFor(() => expect(h.app.quit).toHaveBeenCalled());
    expect(h.showErrorBox.mock.calls[0][1]).toContain("could not run python: ENOENT");
    expect(h.handlers.size).toBe(0);
  });

  it("reports an engine that dies while running, then quits", async () => {
    await boot();
    proc.emit("exit", null, "SIGKILL");
    expect(h.showErrorBox).toHaveBeenCalledWith(
      "terminus engine stopped",
      expect.stringContaining("SIGKILL"),
    );
    expect(h.app.quit).toHaveBeenCalled();
  });

  it("does not report the engine's exit during a quit", async () => {
    await boot();
    h.app.emit("before-quit");
    proc.emit("exit", 0, null);
    expect(h.showErrorBox).not.toHaveBeenCalled();
  });

  it("holds the quit until the sidecar is stopped", async () => {
    await boot();
    const e = { preventDefault: vi.fn() };
    h.app.emit("will-quit", e);
    expect(e.preventDefault).toHaveBeenCalled();
    // Long enough for the park the sidecar runs on its way out, not the 5 s default.
    expect(h.stopSidecar).toHaveBeenCalledWith(proc, 5 * 60_000);
    await vi.waitFor(() => expect(h.app.quit).toHaveBeenCalled());
  });

  it("lets the quit through once the sidecar has exited", async () => {
    await boot();
    proc.exitCode = 0;
    const e = { preventDefault: vi.fn() };
    h.app.emit("will-quit", e);
    expect(e.preventDefault).not.toHaveBeenCalled();
    expect(h.stopSidecar).not.toHaveBeenCalled();
  });
});
