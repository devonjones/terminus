// @vitest-environment jsdom
import { findByRole, findByText, getByRole, waitFor } from "@testing-library/dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type {
  AppState,
  Columns,
  Disc,
  Frame,
  Frames,
  Horizon,
  Job,
  ScopeStatus,
  TerminusApi,
} from "./api/types";
import { mount } from "./renderer";

const site = {
  slug: "site-a",
  name: "site-a",
  updated: "2026-10-06T16:00",
  photos: 3,
  panorama: true,
  mask: true,
};
const state = (over: Partial<AppState> = {}): AppState => ({
  version: "0.1.0",
  site: null,
  tab: "connect",
  tabs: ["connect", "panorama", "horizon", "orient", "fit", "export"],
  scope: { link: "none", host: null },
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
  planning: [
    [600, 280],
    [920, 600],
    [600, 920],
  ],
  fiducials: [{ az: 90, alt: 10, bound: false, xy: [850, 600] }],
};

const linked = (over: Partial<ScopeStatus> = {}): ScopeStatus => ({
  link: "alpaca",
  host: "10.5.2.65",
  eq: true,
  az: 180,
  alt: 30,
  stowed: false,
  moving: false,
  sun: { az: 290, alt: -30 },
  cone: 30,
  ...over,
});

const cols = (over: Partial<Columns> = {}): Columns => ({
  note: "native link: 0.3 deg JNow bias (terminus-85)",
  columns: [
    {
      az: 40,
      alt: 23.09,
      uncertainty: 0.5,
      method: "focused",
      included: true,
      note: "",
      tags: [],
      residual: -0.4,
      frames: [
        { alt: 23.25, sky: 1, name: "az040_alt23.25_sky100.jpg" },
        { alt: 23.0, sky: 0.45, name: "az040_alt23.00_sky045.jpg" },
        { alt: 22.75, sky: 0, name: "az040_alt22.75_sky000.jpg" },
      ],
    },
    {
      az: 270,
      alt: 19.41,
      uncertainty: 0.5,
      method: "coarse",
      included: false,
      note: "dawn",
      tags: ["false edge"],
      residual: null,
      frames: [],
    },
  ],
  fit: {
    solution: { yaw: 186.2, pitch: -1.5, tilt_mag: 7.1, tilt_dir: 300 },
    yaw_pm: 4,
    summary: { n: 9, rms: 0.62, median: 0.4, max: 1.3, within_2: 9 },
  },
  ...over,
});

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
    renameSite: vi.fn(async () => st),
    deleteSite: vi.fn(async () => (st = { ...st, site: null })),
    horizon: vi.fn(async () => horizon()),
    disc: vi.fn(async () => disc),
    image: vi.fn(async () => null),
    frames: vi.fn(async () => null),
    frameImage: vi.fn(async () => null),
    buildImage: vi.fn(async () => null),
    curate: vi.fn(async () => st),
    discoverScopes: vi.fn(async () => ({ scopes: [{ host: "10.5.2.65", port: 32323 }] })),
    connectScope: vi.fn(
      async (host: string) => (st = { ...st, scope: { link: "alpaca" as const, host } }),
    ),
    scopeStatus: vi.fn(async () =>
      st.scope.link === "none" ? { link: "none" as const } : linked(),
    ),
    parkScope: vi.fn(async () => linked({ stowed: true })),
    disconnectScope: vi.fn(async () => (st = { ...st, scope: { link: "none", host: null } })),
    pointScope: vi.fn(async (az: number, alt: number) => linked({ az, alt })),
    takeFrame: vi.fn(async (ms: number) => ({ exposure_ms: ms, median: 19360, saturated: 0 })),
    framePreview: vi.fn(async () => new Uint8Array([0xff, 0xd8])),
    columns: vi.fn(async () => null),
    editColumn: vi.fn(async () => cols()),
    fitColumns: vi.fn(async () => cols()),
    columnFrame: vi.fn(async () => new Uint8Array([0xff, 0xd8])),
    clickEdge: vi.fn(async () => cols()),
    applyOrientation: vi.fn(async () => st),
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
      phase: null,
      canvas: null,
      frames: [],
      active: [],
      pairs: [],
      compared: 0,
      detail: null,
      outline: 0,
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
      phase: null,
      canvas: null,
      frames: [],
      active: [],
      pairs: [],
      compared: 0,
      detail: null,
      outline: 0,
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
      image: async (name: string) => (name === "outline" ? new Uint8Array([1]) : null),
      ...over,
    });
    await mount(root, api);
    return api;
  }

  it("draws the sidecar's disc pixels exactly as given", async () => {
    await horizonTab();
    // The actual horizon is the sky's outline, a raster; planning is a line.
    expect(root.querySelector(".rotor img.outline")).not.toBeNull();
    expect(root.querySelector("polygon.pl")!.getAttribute("points")).toBe(
      "600,280 920,600 600,920",
    );
    expect(root.querySelector("line.pk, polygon.hz")).toBeNull(); // no pockets, no column line
    expect(root.querySelector("circle.edg title")!.textContent).toBe("az 90: telescope edge 10.0°");
    expect([...root.querySelectorAll("text.cd")].map((t) => t.textContent)).toEqual(["N", "E"]);
  });

  it("offers toggles only for layers the site has, and they hide the layer", async () => {
    await horizonTab();
    const names = [...root.querySelectorAll(".bar button")].map((b) => b.textContent);
    expect(names).toEqual(["Horizon", "Planning horizon", "Telescope columns", "Altitude grid"]);
    getByRole(root, "button", { name: "Horizon" }).click();
    expect(root.querySelector("img.outline")).toBeNull();
    expect(getByRole(root, "button", { name: "Horizon" }).getAttribute("aria-pressed")).toBe(
      "false",
    );
  });

  it("an unoriented site spins photo and lines together; the compass stays put", async () => {
    const api = await horizonTab();
    const rotor = root.querySelector<HTMLElement>(".rotor")!;
    expect(rotor.style.transform).toBe("rotate(30deg)");
    expect(rotor.querySelector("img.outline")).not.toBeNull(); // turns with the photo
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

describe("overlapping loads, polling and failures", () => {
  it("an overlapping, stale site load is dropped and its photos released", async () => {
    let release!: () => void;
    let holdNext = false;
    const api = fakeApi({
      getState: async () => state({ site, tab: "horizon" }),
      // The two photographs; a site with no disagreement map yet.
      image: vi.fn(async (name: string) => {
        if (name === "disagree" || name === "outline") return null;
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

  it("a stale load that fails says nothing and leaves the newer site's photos", async () => {
    let reject!: (e: Error) => void;
    let holdNext = false;
    const api = fakeApi({
      getState: async () => state({ site, tab: "horizon" }),
      // The two photographs; a site with no disagreement map yet.
      image: vi.fn(async (name: string) => {
        if (name === "disagree" || name === "outline") return null;
        if (holdNext) {
          holdNext = false;
          await new Promise<void>((_, r) => (reject = r)); // the first open fails, late
        }
        return new Uint8Array([1]);
      }),
    });
    await mount(root, api);
    const select = root.querySelector("select")!;
    select.value = "site-a";
    holdNext = true;
    select.dispatchEvent(new Event("change")); // load A: stalls, then fails
    await vi.waitFor(() => expect(reject).toBeDefined());
    select.dispatchEvent(new Event("change")); // load B: lands
    await vi.waitFor(() => expect(URL.revokeObjectURL).toHaveBeenCalledTimes(2));
    reject(new Error("engine /site/disc.jpg: 500"));
    await new Promise((r) => setTimeout(r, 20));
    expect(root.querySelector('[role="alert"]')).toBeNull();
    expect(URL.revokeObjectURL).toHaveBeenCalledTimes(2); // B's photos are still in use
  });

  it("a progress read that works again clears the earlier error", async () => {
    vi.useFakeTimers();
    const running: Job = {
      site: "site-a",
      step: "mosaic",
      status: "running",
      log: [],
      error: null,
      phase: null,
      detail: null,
      outline: 0,
      canvas: null,
      frames: [],
      active: [],
      pairs: [],
      compared: 0,
    };
    let reads = 0;
    await mount(
      root,
      fakeApi({
        getState: vi.fn(async () => {
          reads += 1;
          if (reads === 2) throw new Error("engine /state: 502");
          return state({ site, tab: "panorama", job: running });
        }),
      }),
    );
    await vi.advanceTimersByTimeAsync(1100);
    expect(root.querySelector("[role=alert]")?.textContent).toContain("502");
    await vi.advanceTimersByTimeAsync(1100);
    expect(root.querySelector("[role=alert]")).toBeNull();
  });

  it("keeps polling a build after one failed progress read", async () => {
    vi.useFakeTimers();
    const running: Job = {
      site: "site-a",
      step: "mosaic",
      status: "running",
      log: [],
      error: null,
      phase: null,
      canvas: null,
      frames: [],
      active: [],
      pairs: [],
      compared: 0,
      detail: null,
      outline: 0,
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
    const toggle = getByRole(root, "button", { name: /Planning horizon/ });
    toggle.focus();
    toggle.click();
    expect((document.activeElement as HTMLElement).dataset.focus).toBe("layer:planning");
    const slider = getByRole(root, "slider") as HTMLInputElement;
    slider.focus();
    slider.value = "40";
    slider.dispatchEvent(new Event("input"));
    expect(root.querySelector(".spin-value")!.textContent).toBe(" 40°");
    slider.dispatchEvent(new Event("change"));
    await waitFor(() => expect((document.activeElement as HTMLElement).dataset.focus).toBe("spin"));
  });

  it("says when the horizon was read by colour, and not when it was segmented", async () => {
    const shown = async (backend: string) => {
      root.replaceChildren();
      await mount(
        root,
        fakeApi({
          getState: async () => state({ site, tab: "horizon" }),
          horizon: async () => horizon({ backend }),
        }),
      );
      return root.textContent ?? "";
    };
    expect(await shown("heuristic (per-frame vote)")).toContain(
      "Read by colour, not by segmentation",
    );
    expect(await shown("segment (per-frame vote)")).not.toContain("Read by colour");
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
    const other: Job = {
      site: "site-b",
      step: "mosaic",
      status: "running",
      log: [],
      error: null,
      phase: null,
      canvas: null,
      frames: [],
      active: [],
      pairs: [],
      compared: 0,
      detail: null,
      outline: 0,
    };
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

describe("stale views, focus and spin", () => {
  it("this site's running build shows progress, not 'no panorama yet'", async () => {
    const running: Job = {
      site: "site-a",
      step: "mosaic",
      status: "running",
      log: [],
      error: null,
      phase: null,
      canvas: null,
      frames: [],
      active: [],
      pairs: [],
      compared: 0,
      detail: null,
      outline: 0,
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
      image: async (name: string) => (name === "disagree" ? null : new Uint8Array([1])),
      openSite: vi.fn(async () => state({ site, tab: "horizon" })),
    });
    await mount(root, api);
    expect(root.querySelector("img.outline")).not.toBeNull(); // site A on screen
    vi.mocked(URL.revokeObjectURL).mockClear();
    failing = true;
    const select = root.querySelector("select")!;
    select.value = "site-a";
    select.dispatchEvent(new Event("change"));
    expect((await findByRole(root, "alert")).textContent).toContain("could not be read");
    expect(root.querySelector("img.outline")).toBeNull();
    expect(root.textContent).toContain("This site has no horizon yet.");
    expect(URL.revokeObjectURL).toHaveBeenCalledTimes(3); // site A's photos and outline released
  });
});

describe("curating the photos", () => {
  const frames = (over: Partial<Frame>[] = []): Frames => ({
    width: 100,
    height: 50,
    frames: (
      [
        { name: "a.jpg", layer: "layer0000.tif", box: [0, 0, 60, 50], off: false, dropped: false },
        { name: "b.jpg", layer: "layer0001.tif", box: [40, 0, 60, 50], off: false, dropped: false },
        { name: "c.jpg", layer: null, box: null, off: false, dropped: true },
      ] as Frame[]
    ).map((f, i) => ({ ...f, ...over[i] })),
  });
  const curating = (f = frames(), over: Partial<TerminusApi> = {}) =>
    fakeApi({
      getState: async () => state({ site, tab: "panorama" }),
      image: async (name: string) => new Uint8Array([name === "disagree" ? 2 : 1]),
      frames: async () => f,
      frameImage: async () => new Uint8Array([1]),
      ...over,
    });
  const photo = (name: string) => getByRole(root, "button", { name: new RegExp(name) });

  it("lists every photo, and says which the stitch could not place", async () => {
    await mount(root, curating());
    await waitFor(() => expect(root.querySelectorAll(".strip button")).toHaveLength(3));
    expect(photo("c.jpg").textContent).toContain("the stitch could not place it");
    expect(photo("c.jpg").getAttribute("aria-pressed")).toBe("false"); // not in the panorama
    expect(photo("c.jpg").classList.contains("off")).toBe(true);
    expect(photo("a.jpg").getAttribute("aria-pressed")).toBe("true");
    expect(root.textContent).toContain("0 of 3 photos off · 1 the stitch could not place");
  });

  it("turning a photo off is pending until re-blended, then sent", async () => {
    const api = curating();
    await mount(root, api);
    const reblend = await findByRole(root, "button", { name: "Re-blend" });
    expect((reblend as HTMLButtonElement).disabled).toBe(true); // nothing changed yet
    photo("b.jpg").click();
    expect(photo("b.jpg").getAttribute("aria-pressed")).toBe("false");
    expect(root.textContent).toContain("1 of 3 photos off");
    expect(api.curate).not.toHaveBeenCalled();
    getByRole(root, "button", { name: "Re-blend" }).click();
    expect(api.curate).toHaveBeenCalledWith(["b.jpg"], false);
  });

  it("bringing back a photo the stitch left out takes a re-stitch", async () => {
    const api = curating(frames([{}, { off: true, layer: null, box: null }]));
    await mount(root, api);
    await findByRole(root, "button", { name: /b\.jpg/ });
    expect(photo("b.jpg").textContent).not.toContain("re-stitch"); // still off: nothing to bring back
    photo("b.jpg").click();
    expect(photo("b.jpg").textContent).toContain("re-stitch to bring it back");
    expect((getByRole(root, "button", { name: "Re-blend" }) as HTMLButtonElement).disabled).toBe(
      true,
    );
    getByRole(root, "button", { name: "Re-stitch" }).click();
    expect(api.curate).toHaveBeenCalledWith([], true);
  });

  it("hovering a photo in the strip highlights where it sits", async () => {
    await mount(root, curating());
    await waitFor(() =>
      expect(root.querySelectorAll(".strip img[src='blob:fake']")).toHaveLength(3),
    );
    const hl = root.querySelector<HTMLElement>(".hl")!;
    expect(hl.hidden).toBe(true);
    photo("b.jpg").dispatchEvent(new Event("mouseenter"));
    expect(hl.hidden).toBe(false);
    expect([hl.style.left, hl.style.width]).toEqual(["40%", "60%"]);
    expect(root.querySelector(".here")!.textContent).toBe("b.jpg: click to turn it off.");
    photo("b.jpg").dispatchEvent(new Event("mouseleave"));
    expect(hl.hidden).toBe(true);
  });

  it("the disagreement map is a layer the user turns on", async () => {
    await mount(root, curating());
    const box = (await findByRole(root, "checkbox", {
      name: /where the photos disagree/,
    })) as HTMLInputElement;
    expect(root.querySelector<HTMLImageElement>(".veil")!.hidden).toBe(true);
    box.click();
    expect(root.querySelector<HTMLImageElement>(".veil")!.hidden).toBe(false);
  });

  it("controls are disabled while a build runs", async () => {
    const running: Job = {
      site: "site-a",
      step: "reblend",
      status: "running",
      log: [],
      error: null,
      phase: null,
      canvas: null,
      frames: [],
      active: [],
      pairs: [],
      compared: 0,
      detail: null,
      outline: 0,
    };
    await mount(
      root,
      curating(frames(), { getState: async () => state({ site, tab: "panorama", job: running }) }),
    );
    expect((await findByText(root, /Blending the photos/)).textContent).toContain(
      "voting on the sky",
    );
    expect((getByRole(root, "button", { name: "Re-stitch" }) as HTMLButtonElement).disabled).toBe(
      true,
    );
    expect((photo("a.jpg") as HTMLButtonElement).disabled).toBe(true);
  });

  it("fetches a site's photos a few at a time, not all at once", async () => {
    let inFlight = 0;
    let most = 0;
    const many = frames();
    many.frames = Array.from({ length: 12 }, (_, i) => ({
      name: `p${i}.jpg`,
      layer: null,
      box: null,
      off: false,
      dropped: false,
    }));
    await mount(
      root,
      curating(many, {
        frameImage: vi.fn(async () => {
          most = Math.max(most, ++inFlight);
          await new Promise((r) => setTimeout(r, 5));
          inFlight--;
          return new Uint8Array([1]);
        }),
      }),
    );
    await waitFor(() =>
      expect(root.querySelectorAll(".strip img[src='blob:fake']")).toHaveLength(12),
    );
    expect(most).toBe(4);
  });

  it("a site built before curation offers to prepare its photos", async () => {
    const api = curating(frames(), { frames: async () => null });
    await mount(root, api);
    (await findByRole(root, "button", { name: "Prepare the photos for curation" })).click();
    expect(api.curate).toHaveBeenCalledWith([], false);
  });
});

describe("watching a build", () => {
  const job = (over: Partial<Job> = {}): Job => ({
    site: "site-a",
    step: "mosaic",
    status: "running",
    log: [],
    error: null,
    phase: "matching",
    canvas: [100, 50],
    frames: [
      { name: "a.jpg", state: "matched", points: 40, links: ["b.jpg"] },
      { name: "b.jpg", state: "matched", points: 40, links: ["a.jpg"] },
      { name: "c.jpg", state: "dropped", points: 0, reason: "0 control points (needs 12)" },
    ],
    active: ["a.jpg", "b.jpg"],
    pairs: [{ a: "a.jpg", b: "b.jpg", matches: 40 }],
    compared: 3,
    detail: null,
    outline: 0,
    ...over,
  });
  const watching = (j: Job) =>
    fakeApi({
      getState: async () => state({ site: { ...site, panorama: false }, tab: "panorama", job: j }),
      frameImage: async () => new Uint8Array([1]),
      buildImage: vi.fn(async () => new Uint8Array([1])),
    });

  it("while matching, draws the pairs found and outlines the pair just reported", async () => {
    await mount(root, watching(job()));
    expect((await findByRole(root, "status")).textContent).toContain(
      "Matching the photos against each other",
    );
    expect(root.querySelector("h1")!.textContent).toBe("Panorama: Matching");
    const edges = root.querySelectorAll(".graph .edge");
    expect(edges).toHaveLength(1);
    expect(edges[0].classList.contains("hot")).toBe(true);
    expect(root.querySelectorAll(".graph .node.hot")).toHaveLength(2);
    expect(root.querySelector(".graph .node.dropped title")!.textContent).toContain(
      "0 control points",
    );
    expect(root.textContent).toContain("3 pairs compared, 1 share points");
  });

  it("a judged photo gets its sky wash, placed at its own box", async () => {
    const judged = job({
      phase: "judging",
      frames: [{ name: "a.jpg", state: "judged", layer: "layer0000.tif", box: [40, 10, 60, 20] }],
      active: [],
    });
    await mount(root, watching(judged));
    await waitFor(() => expect(root.querySelector(".live .sky")).not.toBeNull(), { timeout: 3000 });
    const sky = root.querySelector<HTMLElement>(".live .sky")!;
    expect([sky.style.left, sky.style.top, sky.style.width, sky.style.height]).toEqual([
      "40%",
      "20%",
      "60%",
      "40%",
    ]);
  });

  it("draws the horizon so far over the panorama as each photo is judged", async () => {
    const judging = job({
      step: "reblend",
      phase: "judging",
      outline: 3,
      frames: [{ name: "a.jpg", state: "judged", layer: "layer0000.tif", box: [0, 0, 60, 50] }],
    });
    const image = vi.fn(async (name: string) => (name === "progress" ? new Uint8Array([1]) : null));
    await mount(root, { ...watching(judging), image });
    await waitFor(() => expect(root.querySelector(".live img.progress")).not.toBeNull(), {
      timeout: 3000,
    });
    expect(image).toHaveBeenCalledWith("progress");
  });

  it("the heading names cpfind's own stage as matching moves through them", async () => {
    await mount(root, watching(job({ detail: "comparing neighbouring photos" })));
    await waitFor(() =>
      expect(root.querySelector("h1")!.textContent).toBe(
        "Panorama: Matching — comparing neighbouring photos",
      ),
    );
  });

  it("once photos land, shows the panorama and outlines the one being worked on", async () => {
    const placing = job({
      phase: "placing",
      frames: [
        { name: "a.jpg", state: "placed", layer: "layer0000.tif", box: [0, 0, 60, 50] },
        { name: "b.jpg", state: "matched", points: 40 },
      ],
      active: ["a.jpg"],
    });
    const api = watching(placing);
    await mount(root, api);
    // The layer arrives on the next progress poll.
    await waitFor(() => expect(root.querySelector(".live img")).not.toBeNull(), { timeout: 3000 });
    expect(root.querySelector(".graph")).toBeNull();
    expect(root.querySelector(".live img")!.classList.contains("hot")).toBe(true);
    expect(root.querySelectorAll(".live .hl")).toHaveLength(1); // the same wash as hovering
    expect(api.buildImage).toHaveBeenCalledWith("layer", "layer0000.tif");
    // Placed at its box: x 0/100, y 0/50, 60 by 50 of a 100 x 50 canvas.
    const img = root.querySelector<HTMLImageElement>(".live img")!;
    expect([img.style.left, img.style.top, img.style.width, img.style.height]).toEqual([
      "0%",
      "0%",
      "60%",
      "100%",
    ]);
    expect(root.querySelector(".live .sky")).toBeNull(); // not judged yet: no wash
    const rows = root.querySelectorAll(".progress li");
    expect(rows[0].classList.contains("hot")).toBe(true);
    expect(rows[1].classList.contains("hot")).toBe(false);
  });
});

describe("managing sites", () => {
  it("renames the open site from the top bar", async () => {
    const api = fakeApi({ getState: async () => state({ site, tab: "panorama" }) });
    await mount(root, api);
    const name = getByRole(root, "textbox", { name: "Site name" }) as HTMLInputElement;
    name.value = "Back yard";
    name.dispatchEvent(new Event("change"));
    expect(api.renameSite).toHaveBeenCalledWith("site-a", "Back yard");
  });

  it("lists every site and deletes one only after the user confirms", async () => {
    const api = fakeApi({ getState: async () => state({ site, tab: "panorama" }) });
    await mount(root, api);
    getByRole(root, "button", { name: "Manage sites…" }).click();
    expect(root.querySelector("h1")!.textContent).toBe("Sites");
    expect(root.querySelector("table.sites tbody tr")!.textContent).toContain("2026-10-06 16:00");
    const ask = vi.spyOn(window, "confirm").mockReturnValueOnce(false);
    getByRole(root, "button", { name: "Delete site-a" }).click();
    expect(api.deleteSite).not.toHaveBeenCalled();
    ask.mockReturnValueOnce(true);
    getByRole(root, "button", { name: "Delete site-a" }).click();
    expect(api.deleteSite).toHaveBeenCalledWith("site-a");
    ask.mockRestore();
  });

  it("the site being built cannot be deleted", async () => {
    const running: Job = {
      site: "site-a",
      step: "mosaic",
      status: "running",
      log: [],
      error: null,
      phase: null,
      detail: null,
      outline: 0,
      canvas: null,
      frames: [],
      active: [],
      pairs: [],
      compared: 0,
    };
    await mount(
      root,
      fakeApi({ getState: async () => state({ site, tab: "panorama", job: running }) }),
    );
    getByRole(root, "button", { name: "Manage sites…" }).click();
    expect(
      (getByRole(root, "button", { name: "Delete site-a" }) as HTMLButtonElement).disabled,
    ).toBe(true);
  });
});

describe("connect", () => {
  it("finds a telescope on the network and links it", async () => {
    const api = fakeApi();
    await mount(root, api);
    (await findByRole(root, "button", { name: "Find telescopes" })).click();
    (await findByRole(root, "button", { name: "Connect to 10.5.2.65" })).click();
    await findByText(root, "az 180.0° alt 30.0°");
    expect(api.connectScope).toHaveBeenCalledWith("10.5.2.65");
    expect(root.textContent).toContain("EQ mode");
    expect(root.querySelector("footer")!.textContent).toContain("scope: alpaca");
  });

  it("links a typed address", async () => {
    const api = fakeApi();
    await mount(root, api);
    const host = getByRole(root, "textbox", { name: "Telescope address" }) as HTMLInputElement;
    host.value = " 10.0.0.20 ";
    getByRole(root, "button", { name: "Connect" }).click();
    await waitFor(() => expect(api.connectScope).toHaveBeenCalledWith("10.0.0.20"));
  });

  it("says when nothing answered", async () => {
    await mount(root, fakeApi({ discoverScopes: vi.fn(async () => ({ scopes: [] })) }));
    getByRole(root, "button", { name: "Find telescopes" }).click();
    await findByText(root, /No telescopes answered/);
  });

  it("parks, then says the arm is closed and how to open it", async () => {
    const api = fakeApi();
    await mount(root, api);
    getByRole(root, "button", { name: "Find telescopes" }).click();
    (await findByRole(root, "button", { name: "Connect to 10.5.2.65" })).click();
    (await findByRole(root, "button", { name: "Park" })).click();
    await findByText(root, /Open it in the Seestar app/);
    expect(api.parkScope).toHaveBeenCalledOnce();
    expect((getByRole(root, "button", { name: "Park" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("shows a refused park and keeps the controls", async () => {
    const api = fakeApi({
      parkScope: vi.fn(async () => {
        throw new Error("engine /scope/park: 502 no Sun-safe path to (0,25)");
      }),
    });
    await mount(root, api);
    getByRole(root, "button", { name: "Find telescopes" }).click();
    (await findByRole(root, "button", { name: "Connect to 10.5.2.65" })).click();
    (await findByRole(root, "button", { name: "Park" })).click();
    expect((await findByRole(root, "alert")).textContent).toContain("Sun-safe");
    expect((getByRole(root, "button", { name: "Park" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("warns when the mount is not in EQ mode", async () => {
    let on = false;
    const api = fakeApi({
      scopeStatus: vi.fn(async () => (on ? linked({ eq: false }) : { link: "none" as const })),
    });
    const connect = api.connectScope;
    api.connectScope = vi.fn(async (host: string) => ((on = true), connect(host)));
    await mount(root, api);
    getByRole(root, "button", { name: "Find telescopes" }).click();
    (await findByRole(root, "button", { name: "Connect to 10.5.2.65" })).click();
    await findByText(root, "Switch the mount to EQ mode in the app.");
  });

  it("keeps the way out when the first status read fails", async () => {
    let on = false;
    const api = fakeApi({
      scopeStatus: vi.fn(async () => {
        if (on) throw new Error("engine /scope/status: 502 the telescope did not report");
        return { link: "none" as const };
      }),
    });
    const connect = api.connectScope;
    api.connectScope = vi.fn(async (host: string) => ((on = true), connect(host)));
    await mount(root, api);
    getByRole(root, "button", { name: "Find telescopes" }).click();
    (await findByRole(root, "button", { name: "Connect to 10.5.2.65" })).click();
    expect((await findByRole(root, "alert")).textContent).toContain("did not report");
    expect(getByRole(root, "button", { name: "Park and disconnect" })).toBeTruthy();
    expect(root.querySelector('[data-focus="scope-find"]')).toBeNull();
  });

  it("refreshes the status every few seconds while the tab is open, and stops after", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let az = 180;
    const api = fakeApi();
    const read = api.scopeStatus;
    api.scopeStatus = vi.fn(async () => {
      const st = await read();
      return st.link === "none" ? st : { ...st, az };
    });
    await mount(root, api);
    getByRole(root, "button", { name: "Find telescopes" }).click();
    (await findByRole(root, "button", { name: "Connect to 10.5.2.65" })).click();
    await findByText(root, "az 180.0° alt 30.0°");
    az = 200; // the scope moved
    await vi.advanceTimersByTimeAsync(3100);
    await findByText(root, "az 200.0° alt 30.0°");
    await openTab("Panorama");
    const reads = vi.mocked(api.scopeStatus).mock.calls.length;
    await vi.advanceTimersByTimeAsync(10_000);
    expect(vi.mocked(api.scopeStatus).mock.calls.length).toBe(reads);
  });

  it("disables the controls while a park runs", async () => {
    let finish: (st: ScopeStatus) => void = () => {};
    const api = fakeApi({
      parkScope: vi.fn(() => new Promise<ScopeStatus>((r) => (finish = r))),
    });
    await mount(root, api);
    getByRole(root, "button", { name: "Find telescopes" }).click();
    (await findByRole(root, "button", { name: "Connect to 10.5.2.65" })).click();
    (await findByRole(root, "button", { name: "Park" })).click();
    await findByText(root, /Parking: clearing the Sun/);
    for (const name of ["Park", "Park and disconnect"])
      expect((getByRole(root, "button", { name }) as HTMLButtonElement).disabled, name).toBe(true);
    finish(linked({ stowed: true }));
    await findByText(root, /Open it in the Seestar app/);
    expect(
      (getByRole(root, "button", { name: "Park and disconnect" }) as HTMLButtonElement).disabled,
    ).toBe(false);
  });

  it("points where it is told and shows a frame", async () => {
    const api = fakeApi();
    await mount(root, api);
    getByRole(root, "button", { name: "Find telescopes" }).click();
    (await findByRole(root, "button", { name: "Connect to 10.5.2.65" })).click();
    const az = (await findByRole(root, "spinbutton", { name: "Azimuth" })) as HTMLInputElement;
    const alt = getByRole(root, "spinbutton", { name: "Altitude" }) as HTMLInputElement;
    az.value = "260";
    alt.value = "24.2";
    getByRole(root, "button", { name: "Go" }).click();
    await findByText(root, "az 260.0° alt 24.2°");
    expect(api.pointScope).toHaveBeenCalledWith(260, 24.2);
    const ms = getByRole(root, "spinbutton", { name: "Exposure (ms)" }) as HTMLInputElement;
    ms.value = "0.5";
    getByRole(root, "button", { name: "Take a frame" }).click();
    await findByText(root, "0.5 ms: median 19360, 0.0% saturated");
    expect(api.takeFrame).toHaveBeenCalledWith(0.5);
    expect(root.querySelector<HTMLImageElement>("img.frame")!.src).toBe("blob:fake");
  });

  it("offers no aiming while the arm is closed", async () => {
    const api = fakeApi();
    const connect = api.connectScope;
    let on = false;
    api.scopeStatus = vi.fn(async () =>
      on ? linked({ stowed: true }) : { link: "none" as const },
    );
    api.connectScope = vi.fn(async (host: string) => ((on = true), connect(host)));
    await mount(root, api);
    getByRole(root, "button", { name: "Find telescopes" }).click();
    (await findByRole(root, "button", { name: "Connect to 10.5.2.65" })).click();
    await findByText(root, /Open it in the Seestar app/);
    expect(root.querySelector('[data-focus="scope-go"]')).toBeNull();
    expect(root.querySelector('[data-focus="scope-frame"]')).toBeNull();
  });

  it("shows a Sun refusal on a slew", async () => {
    const api = fakeApi({
      pointScope: vi.fn(async () => {
        throw new Error("engine /scope/point: 502 (110,15) is 3.0 deg from Sun (< 30.0)");
      }),
    });
    await mount(root, api);
    getByRole(root, "button", { name: "Find telescopes" }).click();
    (await findByRole(root, "button", { name: "Connect to 10.5.2.65" })).click();
    ((await findByRole(root, "spinbutton", { name: "Azimuth" })) as HTMLInputElement).value = "110";
    (getByRole(root, "spinbutton", { name: "Altitude" }) as HTMLInputElement).value = "15";
    getByRole(root, "button", { name: "Go" }).click();
    expect((await findByRole(root, "alert")).textContent).toContain("from Sun");
  });

  it("parks and disconnects", async () => {
    const api = fakeApi();
    await mount(root, api);
    getByRole(root, "button", { name: "Find telescopes" }).click();
    (await findByRole(root, "button", { name: "Connect to 10.5.2.65" })).click();
    (await findByRole(root, "button", { name: "Park and disconnect" })).click();
    await findByRole(root, "button", { name: "Find telescopes" });
    expect(api.disconnectScope).toHaveBeenCalledOnce();
  });
});

describe("telescope columns", () => {
  const openFit = async (api: TerminusApi) => {
    await mount(root, api);
    await openTab("Fit");
  };
  const withSite = (over: Partial<TerminusApi> = {}) => {
    const api = fakeApi({ columns: vi.fn(async () => cols()), ...over });
    api.getState = vi.fn(async () => state({ site }));
    return api;
  };

  it("lists the columns, the solution and the summary", async () => {
    await openFit(withSite());
    await findByText(root, /yaw 186.20° ± 4°/);
    const rows = [...root.querySelectorAll("table.columns tbody tr")].map((r) => r.textContent);
    expect(rows[0]).toContain("23.09°");
    expect(rows[0]).toContain("-0.40°");
    expect(rows[1]).toContain("—"); // excluded: no residual
    expect(root.querySelector("table.columns tfoot")!.textContent).toBe(
      "9 fitted · rms 0.62° · median 0.40° · max 1.30° · 9 within 2°",
    );
    expect(root.textContent).toContain("0.3 deg JNow bias");
  });

  it("excluding a column refits in the engine", async () => {
    const api = withSite();
    await openFit(api);
    const box = (await findByRole(root, "checkbox", {
      name: "Use az 40 in the fit",
    })) as HTMLInputElement;
    box.click();
    await waitFor(() => expect(api.editColumn).toHaveBeenCalledWith(40, { included: false }));
  });

  it("tags a column and untags it", async () => {
    const api = withSite();
    await openFit(api);
    const row = (await findByText(root, "23.09°")).closest("tr")!;
    getByRole(row as HTMLElement, "button", { name: "pocket" }).click();
    await waitFor(() => expect(api.editColumn).toHaveBeenCalledWith(40, { tags: ["pocket"] }));
    const other = (await findByText(root, "19.41°")).closest("tr")!;
    getByRole(other as HTMLElement, "button", { name: "false edge" }).click();
    await waitFor(() => expect(api.editColumn).toHaveBeenCalledWith(270, { tags: [] }));
  });

  it("shows a column's frames, marked where the edge was measured and predicted", async () => {
    const api = withSite();
    await openFit(api);
    (await findByRole(root, "button", { name: "3 frames" })).click();
    await findByText(root, /measured 23.09°, predicted 22.69°/);
    await waitFor(() => expect(api.columnFrame).toHaveBeenCalledTimes(3));
    const captions = [...root.querySelectorAll(".column-strip figcaption")].map(
      (c) => c.textContent,
    );
    expect(captions).toEqual([
      "23.25° · 100% sky",
      "23.00° · 45% sky · measured",
      "22.75° · 0% sky · predicted",
    ]);
  });

  it("reads the edge from two clicks on it and one in the sky", async () => {
    const api = withSite();
    await openFit(api);
    (await findByRole(root, "button", { name: "3 frames" })).click();
    const img = (await findByRole(root, "img", { name: "az 40 alt 23" })) as HTMLImageElement;
    img.getBoundingClientRect = () => ({ left: 10, top: 20, width: 90, height: 160 }) as DOMRect;
    const at = (x: number, y: number) =>
      img.dispatchEvent(new MouseEvent("click", { bubbles: true, clientX: x, clientY: y }));
    expect(root.textContent).toContain("Click a point on the edge in a frame.");
    at(10, 60); // left edge, a quarter down
    await findByText(root, /Click a second point on the edge/);
    expect(root.querySelectorAll(".dot.edge")).toHaveLength(1);
    const img2 = root.querySelector<HTMLImageElement>('img[alt="az 40 alt 23"]')!;
    img2.getBoundingClientRect = img.getBoundingClientRect;
    img2.dispatchEvent(new MouseEvent("click", { bubbles: true, clientX: 100, clientY: 60 }));
    await findByText(root, /Click once in the sky/);
    const img3 = root.querySelector<HTMLImageElement>('img[alt="az 40 alt 23"]')!;
    img3.getBoundingClientRect = img.getBoundingClientRect;
    img3.dispatchEvent(new MouseEvent("click", { bubbles: true, clientX: 55, clientY: 30 }));
    await waitFor(() =>
      expect(api.clickEdge).toHaveBeenCalledWith(
        40,
        "az040_alt23.00_sky045.jpg",
        [0, 0.25],
        [1, 0.25],
        [0.5, 0.0625],
      ),
    );
  });

  it("applies the fit as the site's orientation", async () => {
    const api = withSite();
    await openFit(api);
    (await findByRole(root, "button", { name: "Apply this orientation" })).click();
    await waitFor(() => expect(api.applyOrientation).toHaveBeenCalledOnce());
  });

  it("offers no apply before a fit", async () => {
    await openFit(withSite({ columns: vi.fn(async () => cols({ fit: null })) }));
    const b = (await findByRole(root, "button", {
      name: "Apply this orientation",
    })) as HTMLButtonElement;
    expect(b.disabled).toBe(true);
  });

  it("fits every column on request", async () => {
    const api = withSite();
    await openFit(api);
    (await findByRole(root, "button", { name: "Fit all columns" })).click();
    await waitFor(() => expect(api.fitColumns).toHaveBeenCalledOnce());
  });

  it("shows the columns even when a photo will not load", async () => {
    const api = withSite({
      frames: vi.fn(async (): Promise<Frames> => ({
        width: 72,
        height: 36,
        frames: [
          {
            name: "a.jpg",
            layer: "layer0000.tif",
            box: [0, 0, 36, 36],
            off: false,
            dropped: false,
          },
        ],
      })),
      frameImage: vi.fn(async () => {
        throw new Error("engine /site/frame/thumb.jpg: 422 this site's files could not be read");
      }),
    });
    await openFit(api);
    await findByText(root, /yaw 186.20°/);
  });

  it("falls back to the oriented mask's record without columns", async () => {
    await openFit(fakeApi());
    await findByText(root, /No telescope fit for this site yet/);
  });
});
