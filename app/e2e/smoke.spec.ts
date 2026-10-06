// Launch the real app (Electron + Python sidecar from the repo's .venv) and drive it.
import { _electron as electron, expect, test } from "@playwright/test";
import { execFile } from "node:child_process";
import { cpSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { promisify } from "node:util";

const appDir = path.resolve(__dirname, "..");

test("shows the sidecar's state, changes tab, and parks the sidecar on quit", async () => {
  const app = await electron.launch({ args: [appDir] });
  const win = await app.firstWindow();
  await expect(win.locator("footer")).toHaveText(
    "engine 0.1.0 · site: none · scope: none · sun mode: SUN",
  );
  await win.getByRole("tab", { name: "Horizon" }).click();
  await expect(win.locator("h1")).toHaveText("Horizon");
  await expect(win.getByRole("tab", { selected: true })).toHaveText("Horizon");

  // The renderer sees named calls only, and nothing of Node or Electron.
  expect(
    await win.evaluate(() => ({
      api: Object.keys(window.terminus).sort(),
      require: typeof (globalThis as { require?: unknown }).require,
      process: typeof (globalThis as { process?: unknown }).process,
    })),
  ).toEqual({
    api: [
      "buildImage",
      "createSite",
      "curate",
      "deleteSite",
      "disc",
      "frameImage",
      "frames",
      "getState",
      "horizon",
      "image",
      "listSites",
      "openSite",
      "pickPhotos",
      "renameSite",
      "setSpin",
      "setTab",
    ],
    require: "undefined",
    process: "undefined",
  });

  const log = path.join(await app.evaluate(({ app }) => app.getPath("logs")), "sidecar.log");
  const before = readFileSync(log, "utf8").length;
  await app.close();
  await expect
    .poll(() => readFileSync(log, "utf8").slice(before))
    .toContain("park: no scope linked");
});

test("--dev: dev-snap attaches over CDP, clicks, and saves a screenshot and /dev/state", async () => {
  const app = await electron.launch({ args: [appDir, "--dev"] });
  try {
    await app.firstWindow();
    const out = mkdtempSync(path.join(os.tmpdir(), "terminus-snap-"));
    await promisify(execFile)(
      process.execPath,
      ["scripts/dev-snap.mjs", "--click", "text=Fit", "--out", out],
      { cwd: appDir, timeout: 30_000 },
    );
    const state = JSON.parse(readFileSync(path.join(out, "state.json"), "utf8"));
    expect(state.tab).toBe("fit");
    expect(state.log.some((l: string) => l.includes("tab -> fit"))).toBe(true);
    expect(readFileSync(path.join(out, "screenshot.png")).subarray(1, 4).toString()).toBe("PNG");
  } finally {
    await app.close();
  }
});

test("stage 1: open a built site, see its panorama and disc, spin it, read the fit tab", async () => {
  const app = await electron.launch({ args: [appDir] });
  const sites = path.join(await app.evaluate(({ app }) => app.getPath("userData")), "sites");
  const site = path.join(sites, "site-fixture");
  rmSync(site, { recursive: true, force: true });
  cpSync(path.join(__dirname, "fixtures", "site-fixture"), site, { recursive: true });
  try {
    const win = await app.firstWindow();
    await win.reload(); // pick up the site copied in after launch
    await win.getByLabel("Site").selectOption("site-fixture");
    await expect(win.locator("footer")).toContainText("site: site-fixture");

    await win.getByRole("tab", { name: "Panorama" }).click();
    await expect(win.locator("img.pano")).toBeVisible();

    await win.getByRole("tab", { name: "Horizon" }).click();
    await expect(win.locator(".disc .rotor img").first()).toBeVisible(); // the reprojected photo
    // The actual horizon is the sky's outline (a raster); planning is one line.
    await expect(win.locator(".disc .rotor img.outline")).toBeVisible();
    expect(await win.locator("polygon.pl").getAttribute("points")).toMatch(
      /^[\d.]+,[\d.]+( [\d.]+,[\d.]+){359}$/,
    );
    await expect(win.locator("line.pk, polygon.hz")).toHaveCount(0);
    await expect(win.locator("text.cd")).toHaveText(["N", "E", "S", "W"]);
    await expect(win.getByText("UNORIENTED")).toBeVisible();

    const slider = win.getByRole("slider");
    await slider.fill("90");
    await slider.dispatchEvent("change");
    await expect(win.locator(".rotor")).toHaveAttribute("style", /rotate\(90deg\)/);
    expect(await win.evaluate(() => window.terminus.getState().then((s) => s.spin))).toBe(90);

    await win.getByRole("tab", { name: "Fit" }).click();
    await expect(win.getByText("No telescope fit for this site yet")).toBeVisible();
  } finally {
    await app.close();
    rmSync(site, { recursive: true, force: true });
  }
});
