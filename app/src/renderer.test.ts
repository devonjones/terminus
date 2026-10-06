// @vitest-environment jsdom
import { findByRole, findByText, getByRole, waitFor } from "@testing-library/dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AppState, Disc, Horizon, Job, TerminusApi } from "./api/types";
import { mount } from "./renderer";

const site = { slug: "site-a", photos: 3, panorama: true, mask: true };
const state = (over: Partial<AppState> = {}): AppState => ({
  version: "0.1.0",
  site: null,
  tab: "connect",
  tabs: ["connect", "panorama", "horizon", "orient", "fit", "export"],
  scope: { link: "none" },
  sun_mode: "sun",
  spin: 0,
  job: null,
  ...over,
});
const horizon = (over: Partial<Horizon> = {}): Horizon => ({
  mask: "photo_mask.yaml",
  oriented: false,
  solution: { yaw: 0, pitch: 0, tilt_mag: 0, tilt_dir: 0 },
  columns: [],
  fit: [],
  settled: null,
  backend: null,
  ...over,
});
const disc: Disc = {
  size: 1200,
  floor: -20,
  centre: 600,
  rings: [{ alt: 0, r: 457.6 }],
  cardinals: [
    { label: "N", xy: [600, 70] },
    { label: "E", xy: [1130, 600] },
  ],
  actual: [
    [600, 300],
    [900, 600],
    [600, 900],
  ],
  planning: [],
  pockets: [
    [
      [600, 320],
      [600, 400],
    ],
  ],
  fiducials: [{ az: 90, alt: 10, bound: false, xy: [850, 600] }],
};

function fakeApi(over: Partial<TerminusApi> = {}): TerminusApi {
  let st = state();
  return {
    getState: vi.fn(async () => st),
    setTab: vi.fn(async (tab: string) => (st = { ...st, tab })),
    listSites: vi.fn(async () => ({ sites: [site] })),
    openSite: vi.fn(async () => (st = { ...st, site })),
    pickPhotos: vi.fn(async () => null),
    createSite: vi.fn(async () => (st = { ...st, site })),
    setSpin: vi.fn(async (deg: number) => (st = { ...st, spin: deg })),
    horizon: vi.fn(async () => horizon()),
    disc: vi.fn(async () => disc),
    image: vi.fn(async () => null),
    ...over,
  };
}

let root: HTMLElement;
beforeEach(() => {
  root = document.body.appendChild(document.createElement("div"));
  URL.createObjectURL = vi.fn(() => "blob:fake");
  URL.revokeObjectURL = vi.fn();
});
afterEach(() => {
  root.remove();
  vi.useRealTimers();
});

const openTab = async (name: string) => {
  getByRole(root, "tab", { name }).click();
  await waitFor(() => expect(root.querySelector("h1")!.textContent).toBe(name));
};

describe("shell", () => {
  it("lists the sites, the tabs and a status line", async () => {
    await mount(root, fakeApi());
    const options = [...root.querySelectorAll("select option")].map((o) => o.textContent);
    expect(options).toEqual(["Choose a site…", "site-a (3 photos)"]);
    expect(root.querySelectorAll('[role="tab"]')).toHaveLength(6);
    expect(root.querySelector("footer")!.textContent).toBe(
      "engine 0.1.0 · site: none · scope: none · sun mode: SUN",
    );
  });

  it("opens the chosen site and loads its views", async () => {
    const api = fakeApi();
    await mount(root, api);
    const select = root.querySelector("select")!;
    select.value = "site-a";
    select.dispatchEvent(new Event("change"));
    await waitFor(() =>
      expect(root.querySelector("footer")!.textContent).toContain("site: site-a"),
    );
    expect(api.openSite).toHaveBeenCalledWith("site-a");
    expect(api.horizon).toHaveBeenCalled();
    expect(api.image).toHaveBeenCalledWith("panorama");
  });

  it("keeps keyboard focus on the selected tab after a change", async () => {
    await mount(root, fakeApi());
    const tab = getByRole(root, "tab", { name: "Horizon" });
    tab.focus();
    await openTab("Horizon");
    expect(document.activeElement).toBe(getByRole(root, "tab", { selected: true }));
  });

  it("shows a refused call instead of dropping it", async () => {
    await mount(root, fakeApi({ setTab: () => Promise.reject(new Error("engine down")) }));
    getByRole(root, "tab", { name: "Fit" }).click();
    expect((await findByText(root, "engine down")).getAttribute("role")).toBe("alert");
  });
});

describe("new site", () => {
  it("dropped photos start a site; other files are ignored", async () => {
    const api = fakeApi();
    await mount(root, api);
    const files = [
      new File(["x"], "a.JPG"),
      new File(["x"], "notes.txt"),
      new File(["x"], "b.tif"),
    ];
    const drop = new Event("drop", { cancelable: true }) as Event & { dataTransfer: unknown };
    drop.dataTransfer = { files };
    document.dispatchEvent(drop);
    await waitFor(() => expect(api.createSite).toHaveBeenCalled());
    expect(vi.mocked(api.createSite).mock.calls[0][0].map((f) => f.name)).toEqual([
      "a.JPG",
      "b.tif",
    ]);
    expect(drop.defaultPrevented).toBe(true);
  });

  it("a drop with no photos says so", async () => {
    const api = fakeApi();
    await mount(root, api);
    const drop = new Event("drop", { cancelable: true }) as Event & { dataTransfer: unknown };
    drop.dataTransfer = { files: [new File(["x"], "notes.txt")] };
    document.dispatchEvent(drop);
    expect((await findByRole(root, "alert")).textContent).toContain(".jpg");
    expect(api.createSite).not.toHaveBeenCalled();
  });

  it("follows the build until it is done, then loads the site", async () => {
    vi.useFakeTimers();
    const running: Job = {
      site: "site-a",
      step: "mosaic",
      status: "running",
      log: ["registered 18 frames"],
      error: null,
    };
    let built = false;
    const api = fakeApi({
      pickPhotos: vi.fn(async () => state({ site, job: running, tab: "panorama" })),
      getState: vi.fn(async () =>
        built
          ? state({ site, job: { ...running, step: "skymask", status: "done" }, tab: "panorama" })
          : state({ tab: "panorama" }),
      ),
    });
    await mount(root, api);
    getByRole(root, "button", { name: "Choose photos…" }).click();
    await vi.waitFor(() =>
      expect(root.textContent).toContain("Stitching the photos into a panorama"),
    );
    expect(root.querySelector("pre")!.textContent).toBe("registered 18 frames");
    vi.mocked(api.horizon).mockClear();
    built = true;
    await vi.advanceTimersByTimeAsync(1100);
    await vi.waitFor(() => expect(api.horizon).toHaveBeenCalled());
    expect(root.textContent).not.toContain("Stitching");
  });

  it("a failed build is an alert carrying the engine's message", async () => {
    const failed: Job = {
      site: "site-a",
      step: "mosaic",
      status: "failed",
      log: [],
      error: "error: no frame could be constrained",
    };
    await mount(
      root,
      fakeApi({ getState: async () => state({ site, job: failed, tab: "panorama" }) }),
    );
    expect(getByRole(root, "alert").textContent).toContain("no frame could be constrained");
  });
});

describe("horizon", () => {
  async function horizonTab(over: Partial<TerminusApi> = {}) {
    const api = fakeApi({
      getState: async () => state({ site, tab: "horizon", spin: 30 }),
      ...over,
    });
    await mount(root, api);
    return api;
  }

  it("draws the sidecar's disc pixels exactly as given", async () => {
    await horizonTab();
    expect(root.querySelector("polygon.hz")!.getAttribute("points")).toBe(
      "600,300 900,600 600,900",
    );
    const pk = root.querySelector("line.pk")!;
    expect([pk.getAttribute("x1"), pk.getAttribute("y1"), pk.getAttribute("y2")]).toEqual([
      "600",
      "320",
      "400",
    ]);
    expect(root.querySelector("circle.edg title")!.textContent).toBe("az 90: telescope edge 10.0°");
    expect([...root.querySelectorAll("text.cd")].map((t) => t.textContent)).toEqual(["N", "E"]);
  });

  it("offers toggles only for layers the site has, and they hide the layer", async () => {
    await horizonTab();
    const names = [...root.querySelectorAll(".bar button")].map((b) => b.textContent);
    expect(names).toEqual(["Horizon", "Sky pockets", "Telescope columns", "Altitude grid"]);
    getByRole(root, "button", { name: "Sky pockets" }).click();
    expect(root.querySelector("line.pk")).toBeNull();
    expect(getByRole(root, "button", { name: "Sky pockets" }).getAttribute("aria-pressed")).toBe(
      "false",
    );
  });

  it("an unoriented site spins photo and lines together; the compass stays put", async () => {
    const api = await horizonTab();
    const rotor = root.querySelector<HTMLElement>(".rotor")!;
    expect(rotor.style.transform).toBe("rotate(30deg)");
    expect(rotor.querySelector("polygon.hz")).not.toBeNull();
    expect(rotor.querySelector("text.cd")).toBeNull();
    const slider = getByRole(root, "slider") as HTMLInputElement;
    slider.value = "120";
    slider.dispatchEvent(new Event("input"));
    expect(rotor.style.transform).toBe("rotate(120deg)");
    slider.dispatchEvent(new Event("change"));
    expect(api.setSpin).toHaveBeenCalledWith(120);
  });

  it("an oriented site does not spin and states its solution", async () => {
    await horizonTab({
      horizon: async () =>
        horizon({
          oriented: true,
          solution: { yaw: 186.19, pitch: -1.51, tilt_mag: 7.13, tilt_dir: 300 },
        }),
    });
    expect(root.querySelector("input[type=range]")).toBeNull();
    expect(root.querySelector<HTMLElement>(".rotor")!.style.transform).toBe("");
    expect(root.textContent).toContain("yaw 186.19°");
  });
});

describe("fit", () => {
  it("lists the fit's columns by azimuth, used ones and the rest", async () => {
    const fit = [
      { az: 200, alt: 40, bound: true, used: true },
      { az: 90, alt: 5.4, used: true, residual: 0.4 },
      { az: 300, used: false, reason: "no edge" },
    ];
    await mount(
      root,
      fakeApi({
        getState: async () => state({ site, tab: "fit" }),
        horizon: async () => horizon({ fit, settled: false }),
      }),
    );
    const rows = [...root.querySelectorAll("tbody tr")].map((r) =>
      [...r.querySelectorAll("td")].map((c) => c.textContent),
    );
    expect(rows).toEqual([
      ["90", "5.4", "edge", "used", "+0.40°"],
      ["200", "40.0", "at least (ceiling)", "used", "—"],
      ["300", "—", "—", "not used", "no edge"],
    ]);
    expect(root.textContent).toContain("2 of 3 telescope columns used");
    expect(getByRole(root, "alert").textContent).toContain("did not settle");
  });

  it("says so when there is no fit", async () => {
    await mount(root, fakeApi({ getState: async () => state({ site, tab: "fit" }) }));
    expect(root.textContent).toContain("No telescope fit for this site yet");
  });
});

describe("round-1 review", () => {
  it("an overlapping, stale site load is dropped and its photos released", async () => {
    let release!: () => void;
    let holdNext = false;
    const api = fakeApi({
      getState: async () => state({ site, tab: "horizon" }),
      image: vi.fn(async () => {
        if (holdNext) {
          holdNext = false;
          await new Promise<void>((r) => (release = r)); // the first open's photo is slow
        }
        return new Uint8Array([1]);
      }),
    });
    await mount(root, api);
    vi.mocked(URL.createObjectURL).mockClear();
    const select = root.querySelector("select")!;
    select.value = "site-a";
    holdNext = true;
    select.dispatchEvent(new Event("change")); // load A: stalls on its photo
    await vi.waitFor(() => expect(release).toBeDefined());
    select.dispatchEvent(new Event("change")); // load B: completes first
    await vi.waitFor(() => expect(URL.createObjectURL).toHaveBeenCalledTimes(2));
    release(); // A finishes last and must not land
    await new Promise((r) => setTimeout(r, 20));
    expect(URL.createObjectURL).toHaveBeenCalledTimes(2);
    // B replaced the photos the mount had loaded, and released both.
    expect(URL.revokeObjectURL).toHaveBeenCalledTimes(2);
  });

  it("keeps polling a build after one failed progress read", async () => {
    vi.useFakeTimers();
    const running: Job = {
      site: "site-a",
      step: "mosaic",
      status: "running",
      log: [],
      error: null,
    };
    let reads = 0;
    const api = fakeApi({
      getState: vi.fn(async () => {
        reads++;
        if (reads === 2) throw new Error("engine busy");
        return state({ site, job: running, tab: "panorama" });
      }),
    });
    await mount(root, api);
    await vi.advanceTimersByTimeAsync(1100);
    await vi.advanceTimersByTimeAsync(1100);
    expect(reads).toBeGreaterThanOrEqual(3);
  });

  it("an error inside adopt reaches the banner instead of vanishing", async () => {
    let lists = 0;
    const api = fakeApi({
      listSites: async () => {
        if (lists++ > 0) throw new Error("sites unreadable");
        return { sites: [site] };
      },
    });
    await mount(root, api);
    const select = root.querySelector("select")!;
    select.value = "site-a";
    select.dispatchEvent(new Event("change"));
    expect((await findByRole(root, "alert")).textContent).toBe("sites unreadable");
  });

  it("focus survives a layer toggle and a spin change", async () => {
    await mount(root, fakeApi({ getState: async () => state({ site, tab: "horizon" }) }));
    const toggle = getByRole(root, "button", { name: /Sky pockets/ });
    toggle.focus();
    toggle.click();
    expect((document.activeElement as HTMLElement).dataset.focus).toBe("layer:pockets");
    const slider = getByRole(root, "slider") as HTMLInputElement;
    slider.focus();
    slider.value = "40";
    slider.dispatchEvent(new Event("input"));
    expect(root.querySelector(".spin-value")!.textContent).toBe(" 40°");
    slider.dispatchEvent(new Event("change"));
    await waitFor(() => expect((document.activeElement as HTMLElement).dataset.focus).toBe("spin"));
  });

  it("says when the heuristic detector read the horizon", async () => {
    await mount(
      root,
      fakeApi({
        getState: async () => state({ site, tab: "horizon" }),
        horizon: async () => horizon({ backend: "heuristic" }),
      }),
    );
    expect(root.textContent).toContain("Read with the heuristic detector");
  });
});

describe("view details", () => {
  it("a ceiling-bound column is a chevron that says 'at least'", async () => {
    const bound: Disc = { ...disc, fiducials: [{ az: 330, alt: 60, bound: true, xy: [400, 300] }] };
    await mount(
      root,
      fakeApi({ getState: async () => state({ site, tab: "horizon" }), disc: async () => bound }),
    );
    expect(root.querySelector("circle.edg")).toBeNull();
    expect(root.querySelector("path.bnd title")!.textContent).toBe(
      "az 330: telescope edge at least 60.0°",
    );
  });

  it("a column the mask excluded says so in the fit table", async () => {
    const fit = [
      { az: 190, alt: 32.5, used: false, excluded_by: "mask", reason: "dawn transition" },
    ];
    await mount(
      root,
      fakeApi({
        getState: async () => state({ site, tab: "fit" }),
        horizon: async () => horizon({ fit }),
      }),
    );
    const cells = [...root.querySelectorAll("tbody td")].map((c) => c.textContent);
    expect(cells).toEqual(["190", "32.5", "edge", "excluded", "dawn transition"]);
  });

  it("dragging the disc a quarter turn clockwise commits +90 to the spin", async () => {
    const api = fakeApi({ getState: async () => state({ site, tab: "horizon", spin: 10 }) });
    await mount(root, api);
    const stage = root.querySelector<HTMLElement>(".disc")!;
    stage.getBoundingClientRect = () => ({ left: 0, top: 0, width: 200, height: 200 }) as DOMRect;
    const at = (type: string, x: number, y: number) =>
      stage.dispatchEvent(new MouseEvent(type, { clientX: x, clientY: y }));
    at("pointerdown", 100, 0); // top: north
    at("pointermove", 200, 100); // right: east
    expect(root.querySelector<HTMLElement>(".rotor")!.style.transform).toBe("rotate(100deg)");
    at("pointerup", 200, 100);
    await waitFor(() => expect(api.setSpin).toHaveBeenCalledWith(100));
  });

  it("a cancelled pointer ends the drag: later moves do not turn the disc", async () => {
    const api = fakeApi({
      getState: async () => state({ site, tab: "horizon", spin: 0 }),
      setSpin: vi.fn(async (deg: number) => state({ site, tab: "horizon", spin: deg })),
    });
    await mount(root, api);
    const stage = root.querySelector<HTMLElement>(".disc")!;
    stage.getBoundingClientRect = () => ({ left: 0, top: 0, width: 200, height: 200 }) as DOMRect;
    const at = (type: string, x: number, y: number) =>
      stage.dispatchEvent(new MouseEvent(type, { clientX: x, clientY: y }));
    at("pointerdown", 100, 0);
    at("pointermove", 200, 100);
    at("pointercancel", 200, 100);
    await waitFor(() => expect(api.setSpin).toHaveBeenCalledWith(90));
    const rotor = () => root.querySelector<HTMLElement>(".rotor")!.style.transform;
    const before = rotor();
    at("pointermove", 100, 200); // no button held: nothing turns
    expect(rotor()).toBe(before);
  });

  it("a site with no panorama says so; another site's build is not shown here", async () => {
    const other: Job = { site: "site-b", step: "mosaic", status: "running", log: [], error: null };
    await mount(
      root,
      fakeApi({
        getState: async () =>
          state({ site: { ...site, panorama: false }, tab: "panorama", job: other }),
      }),
    );
    expect(root.textContent).not.toContain("Stitching");
    expect(root.textContent).toContain("This site has no panorama yet.");
  });
});

describe("round-2 review", () => {
  it("this site's running build shows progress, not 'no panorama yet'", async () => {
    const running: Job = {
      site: "site-a",
      step: "mosaic",
      status: "running",
      log: [],
      error: null,
    };
    await mount(
      root,
      fakeApi({
        getState: async () =>
          state({ site: { ...site, panorama: false }, tab: "panorama", job: running }),
      }),
    );
    expect(root.textContent).toContain("Stitching");
    expect(root.textContent).not.toContain("no panorama yet");
  });

  it("a site whose views fail to load does not keep showing the previous site", async () => {
    let failing = false;
    const api = fakeApi({
      getState: async () => state({ site, tab: "horizon" }),
      horizon: vi.fn(async () => {
        if (failing)
          throw new Error("engine /site/horizon: 422 this site's files could not be read");
        return horizon();
      }),
      openSite: vi.fn(async () => state({ site, tab: "horizon" })),
    });
    await mount(root, api);
    expect(root.querySelector("polygon.hz")).not.toBeNull(); // site A on screen
    failing = true;
    const select = root.querySelector("select")!;
    select.value = "site-a";
    select.dispatchEvent(new Event("change"));
    expect((await findByRole(root, "alert")).textContent).toContain("could not be read");
    expect(root.querySelector("polygon.hz")).toBeNull();
    expect(root.textContent).toContain("This site has no horizon yet.");
  });
});
