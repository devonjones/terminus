// Start, talk to and stop the Python sidecar (python -m terminus.server).
import { spawn, type ChildProcess } from "node:child_process";
import { createWriteStream } from "node:fs";
import { createInterface } from "node:readline";

export interface Sidecar {
  url: string;
  token: string;
  version: string;
  proc: ChildProcess;
}

export interface StartOptions {
  command: string;
  args: string[];
  cwd?: string;
  logPath: string;
  expectedVersion: string;
  timeoutMs?: number;
}

// The sidecar's first stdout line is {"port", "token"}. Each failure gets its own
// message: not installed (spawn error), refused to start (exit), stuck (timeout).
function hello(proc: ChildProcess, command: string, timeoutMs: number) {
  return new Promise<{ port: number; token: string }>((resolve, reject) => {
    const timer = setTimeout(
      () => reject(new Error(`the engine did not start within ${timeoutMs / 1000} s`)),
      timeoutMs,
    );
    const done = () => clearTimeout(timer);
    proc.once(
      "error",
      (e) => (done(), reject(new Error(`could not run ${command}: ${e.message}`))),
    );
    proc.once(
      "exit",
      (code) => (done(), reject(new Error(`the engine exited (code ${code}) before starting`))),
    );
    createInterface({ input: proc.stdout! }).once("line", (line) => {
      done();
      try {
        resolve(JSON.parse(line));
      } catch {
        reject(new Error("the engine's startup line was not JSON"));
      }
    });
  });
}

export async function request<T = unknown>(
  sc: Pick<Sidecar, "url" | "token">,
  path: string,
  body?: unknown,
): Promise<T> {
  const res = await fetch(sc.url + path, {
    method: body === undefined ? "GET" : "POST",
    headers: { Authorization: `Bearer ${sc.token}`, "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: AbortSignal.timeout(10_000),
  });
  const json = await res.json();
  if (!res.ok) throw new Error(`engine ${path}: ${res.status} ${json.error ?? ""}`.trim());
  return json;
}

export async function startSidecar(o: StartOptions): Promise<Sidecar> {
  const proc = spawn(o.command, o.args, { cwd: o.cwd, stdio: "pipe", windowsHide: true });
  proc.stderr!.pipe(createWriteStream(o.logPath, { flags: "a" }));
  try {
    const { port, token } = await hello(proc, o.command, o.timeoutMs ?? 30_000);
    const url = `http://127.0.0.1:${port}`;
    const { version } = await request<{ version: string }>({ url, token }, "/health");
    if (version !== o.expectedVersion)
      throw new Error(`engine version ${version} does not match app version ${o.expectedVersion}`);
    return { url, token, version, proc };
  } catch (e) {
    proc.kill();
    throw e;
  }
}

// Close stdin: the sidecar parks and exits. Kill only if it has not gone within
// graceMs. On Windows that kill skips the park, so it is the last resort.
export async function stopSidecar(proc: ChildProcess, graceMs = 5_000): Promise<void> {
  if (proc.exitCode !== null || proc.signalCode !== null) return;
  const exited = new Promise((r) => proc.once("exit", r));
  proc.stdin!.end();
  const timer = new Promise((r) => setTimeout(r, graceMs, "timeout"));
  if ((await Promise.race([exited, timer])) === "timeout") {
    console.error(
      `terminus: engine did not exit within ${graceMs} ms of stdin closing; killing it`,
    );
    proc.kill();
  }
}
