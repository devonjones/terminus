import { EventEmitter } from "node:events";
import { PassThrough } from "node:stream";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("node:child_process", () => ({ spawn: vi.fn() }));
vi.mock("node:fs", () => ({ createWriteStream: () => new PassThrough() }));

import { spawn } from "node:child_process";
import { request, startSidecar, stopSidecar } from "./sidecar";

class FakeProc extends EventEmitter {
  stdin = new PassThrough();
  stdout = new PassThrough();
  stderr = new PassThrough();
  exitCode: number | null = null;
  signalCode: string | null = null;
  kill = vi.fn(() => this.exit(null, "SIGTERM"));
  exit(code: number | null, signal: string | null = null) {
    this.exitCode = code;
    this.signalCode = signal;
    this.emit("exit", code, signal);
  }
}

const opts = {
  command: "py",
  args: ["-m", "terminus.server"],
  logPath: "x.log",
  expectedVersion: "0.1.0",
};
let proc: FakeProc;
const fetchMock = vi.fn();

function health(version = "0.1.0") {
  fetchMock.mockResolvedValue({ ok: true, json: async () => ({ ok: true, version }) });
}

beforeEach(() => {
  proc = new FakeProc();
  vi.mocked(spawn).mockReturnValue(proc as never);
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  fetchMock.mockReset();
});

describe("startSidecar", () => {
  it("reads port and token, checks /health with the token", async () => {
    health();
    const p = startSidecar(opts);
    proc.stdout.write('{"port": 4321, "token": "abc"}\n');
    const sc = await p;
    expect(sc).toMatchObject({ url: "http://127.0.0.1:4321", token: "abc", version: "0.1.0" });
    expect(spawn).toHaveBeenCalledWith("py", opts.args, expect.objectContaining({ stdio: "pipe" }));
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://127.0.0.1:4321/health");
    expect(init.headers.Authorization).toBe("Bearer abc");
    expect(proc.kill).not.toHaveBeenCalled();
  });

  it("rejects and kills a sidecar of another version", async () => {
    health("0.0.9");
    const p = startSidecar(opts);
    proc.stdout.write('{"port": 1, "token": "t"}\n');
    await expect(p).rejects.toThrow("engine version 0.0.9 does not match app version 0.1.0");
    expect(proc.kill).toHaveBeenCalled();
  });

  it("says when python cannot be run", async () => {
    const p = startSidecar(opts);
    proc.emit("error", new Error("spawn py ENOENT"));
    await expect(p).rejects.toThrow("could not run py: spawn py ENOENT");
  });

  it("says when the engine exits before starting", async () => {
    const p = startSidecar(opts);
    proc.exit(1);
    await expect(p).rejects.toThrow("the engine exited (code 1) before starting");
  });

  it("times out a stuck engine and kills it", async () => {
    vi.useFakeTimers();
    const p = startSidecar({ ...opts, timeoutMs: 1000 });
    const assertion = expect(p).rejects.toThrow("did not start within 1 s");
    await vi.advanceTimersByTimeAsync(1001);
    await assertion;
    expect(proc.kill).toHaveBeenCalled();
  });

  it("rejects a startup line that is not JSON", async () => {
    const p = startSidecar(opts);
    proc.stdout.write("Traceback (most recent call last):\n");
    await expect(p).rejects.toThrow("startup line was not JSON");
    expect(proc.kill).toHaveBeenCalled();
  });
});

describe("stopSidecar", () => {
  it("closes stdin and waits for the sidecar to park and exit", async () => {
    let ended = false;
    proc.stdin.on("finish", () => {
      ended = true;
      proc.exit(0);
    });
    await stopSidecar(proc as never);
    expect(ended).toBe(true);
    expect(proc.kill).not.toHaveBeenCalled();
  });

  it("kills a sidecar that ignores stdin closing", async () => {
    vi.useFakeTimers();
    vi.spyOn(console, "error").mockImplementation(() => {});
    const p = stopSidecar(proc as never, 500);
    await vi.advanceTimersByTimeAsync(501);
    await p;
    expect(proc.kill).toHaveBeenCalled();
  });

  it("does nothing if it already exited", async () => {
    proc.exit(0);
    await stopSidecar(proc as never);
    expect(proc.kill).not.toHaveBeenCalled();
  });
});

describe("request", () => {
  const sc = { url: "http://127.0.0.1:9", token: "t" };

  it("POSTs JSON bodies", async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ tab: "fit" }) });
    expect(await request(sc, "/state/tab", { tab: "fit" })).toEqual({ tab: "fit" });
    const [, init] = fetchMock.mock.calls[0];
    expect(init.method).toBe("POST");
    expect(init.body).toBe('{"tab":"fit"}');
  });

  it("turns an error response into an error carrying the sidecar's message", async () => {
    fetchMock.mockResolvedValue({
      ok: false,
      status: 400,
      json: async () => ({ error: "unknown tab" }),
    });
    await expect(request(sc, "/state/tab", { tab: "x" })).rejects.toThrow(
      "engine /state/tab: 400 unknown tab",
    );
  });
});
