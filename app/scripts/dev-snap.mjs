// Attach to a running `npm run dev` window over CDP; save a screenshot and /dev/state.
//
//   node scripts/dev-snap.mjs [--click <selector>] [--out <dir>]
//
// Reads the session file the app writes in dev mode (<userData>/dev/session.json)
// and writes screenshot.png and state.json next to it unless --out is given.
// From WSL, run it with Windows node so it reaches Windows 127.0.0.1 (see app/README.md).
import { chromium } from "@playwright/test";
import { readFileSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { parseArgs } from "node:util";

const userData =
  {
    win32: () => path.join(process.env.APPDATA ?? "", "terminus"),
    darwin: () => path.join(os.homedir(), "Library", "Application Support", "terminus"),
  }[process.platform] ??
  (() => path.join(process.env.XDG_CONFIG_HOME ?? path.join(os.homedir(), ".config"), "terminus"));
const devDir = path.join(userData(), "dev");

const { values } = parseArgs({ options: { click: { type: "string" }, out: { type: "string" } } });
const out = values.out ?? devDir;
const session = JSON.parse(readFileSync(path.join(devDir, "session.json"), "utf8"));

const browser = await chromium.connectOverCDP(session.cdp);
try {
  const page = browser.contexts()[0].pages()[0];
  if (values.click) {
    await page.click(values.click);
    await page.waitForLoadState();
  }
  await page.screenshot({ path: path.join(out, "screenshot.png") });
} finally {
  await browser.close(); // disconnects; the app keeps running
}
const res = await fetch(`${session.sidecar}/dev/state`, {
  headers: { Authorization: `Bearer ${session.token}` },
  signal: AbortSignal.timeout(10_000),
});
if (!res.ok) throw new Error(`/dev/state: ${res.status}`);
writeFileSync(path.join(out, "state.json"), JSON.stringify(await res.json(), null, 2) + "\n");
console.log(`wrote ${path.join(out, "screenshot.png")} and state.json`);
