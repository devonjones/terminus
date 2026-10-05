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
  };
});

vi.mock("electron", () => ({
  app: h.app,
  BrowserWindow: class {
    static getAllWindows = () => [];
    loadFile = vi.fn();
  },
  dialog: { showErrorBox: h.showErrorBox },
  ipcMain: { handle: (ch: string, fn: (...a: unknown[]) => unknown) => h.handlers.set(ch, fn) },
  session: { defaultSession: { setPermissionRequestHandler: vi.fn() } },
}));
vi.mock("./sidecar", () => ({
  startSidecar: h.startSidecar,
  stopSidecar: h.stopSidecar,
  request: h.request,
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

  it.each([3, null, { tab: "fit" }, "x".repeat(33)])("refuses tab %j", async (tab) => {
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
    expect(h.stopSidecar).toHaveBeenCalledWith(proc);
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
