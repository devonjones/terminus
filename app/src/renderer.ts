import type {
  AppState,
  ColumnTag,
  Columns,
  Disc,
  Frames,
  Horizon,
  ScopeFrame,
  ScopeList,
  ScopeStatus,
  SiteSummary,
  TerminusApi,
} from "./api/types";
import { decode, pick, type Footprint } from "./frames";
import {
  buildTitle,
  buildView,
  columnsView,
  connectView,
  type Clicks,
  el,
  fitView,
  horizonView,
  jobPanel,
  LAYERS,
  manageView,
  panoramaView,
  type BuildImages,
  type Layer,
} from "./views";

const title = (s: string) => s[0].toUpperCase() + s.slice(1);
const PHOTO = /\.(jpe?g|png|tiff?)$/i;
const PHOTO_LOADS = 4; // photos fetched at once for the curation strip
const LATER: Record<string, string> = {
  orient: "Orienting against the telescope arrives in a later stage.",
  export: "Exporting to planning programs arrives next.",
};

interface View {
  st: AppState;
  sites: SiteSummary[];
  horizon: Horizon | null;
  disc: Disc | null;
  pano: string | null; // blob: URLs
  photo: string | null;
  outline: string | null;
  layers: Set<Layer>;
  error: string;
  // Curation: the site's photos, their pictures, and the ones the user wants off.
  frames: Frames | null;
  disagree: string | null;
  thumbs: Map<string, string>;
  footprints: Map<string, string>;
  fps: Footprint[];
  pending: Set<string>;
  showDisagree: boolean;
  build: BuildImages; // what a running build has produced so far
  managing: boolean; // the site list is showing instead of the tab
  scope: ScopeStatus | null;
  found: ScopeList["scopes"] | null;
  scopeBusy: string;
  frame: ScopeFrame | null;
  preview: string | null; // blob: URL
  // The site's telescope columns (Fit tab), the one being looked at, its frames.
  cols: Columns | null;
  selCol: number | null;
  clicks: Clicks | null;
  colFrames: Map<string, string>;
  colBusy: string;
}

const SCOPE_POLL_MS = 3000;

// Render the app: a site picker, the tab bar, the active tab and a status line.
// Every number and pixel comes from the sidecar; this file only arranges them.
export async function mount(root: HTMLElement, api: TerminusApi): Promise<void> {
  const v: View = {
    st: await api.getState(),
    sites: (await api.listSites()).sites,
    horizon: null,
    disc: null,
    pano: null,
    photo: null,
    outline: null,
    layers: new Set(LAYERS.map((l) => l.key)),
    error: "",
    frames: null,
    disagree: null,
    thumbs: new Map(),
    footprints: new Map(),
    fps: [],
    pending: new Set(),
    showDisagree: false,
    build: {
      thumbs: new Map(),
      layers: new Map(),
      verdicts: new Map(),
      progress: null,
      progressN: 0,
    },
    managing: false,
    scope: null,
    found: null,
    scopeBusy: "",
    frame: null,
    preview: null,
    cols: null,
    selCol: null,
    clicks: null,
    colFrames: new Map(),
    colBusy: "",
  };
  let polling = false;
  let generation = 0;

  const fail = (e: Error) => ((v.error = e.message), render());
  const toUrl = (bytes: Uint8Array | null, type = "image/jpeg") =>
    bytes ? URL.createObjectURL(new Blob([bytes as BlobPart], { type })) : null;
  const revoke = (url: string | null) => url && URL.revokeObjectURL(url);
  const revokeFrames = () => {
    revoke(v.disagree);
    for (const url of [...v.thumbs.values(), ...v.footprints.values()]) revoke(url);
    v.disagree = null;
    v.thumbs = new Map();
    v.footprints = new Map();
    v.fps = [];
  };

  // Loads can overlap (open a site, then another): only the newest one lands,
  // and the photos of the one it replaces are released.
  async function loadSite() {
    const mine = ++generation;
    let views;
    try {
      views = v.st.site
        ? await Promise.all([
            api.horizon(),
            api.disc(),
            api.image("panorama"),
            api.image("disc"),
            api.frames(),
            api.image("disagree"),
            api.image("outline"),
          ])
        : ([null, null, null, null, null, null, null] as const);
    } catch (e) {
      if (mine !== generation) return; // a newer load owns the screen
      // Never leave the previous site's views up under the new site's name.
      revoke(v.pano);
      revoke(v.photo);
      revoke(v.outline);
      revokeFrames();
      v.horizon = v.disc = v.pano = v.photo = v.outline = v.frames = null;
      throw e;
    }
    if (mine !== generation) return;
    revoke(v.pano);
    revoke(v.photo);
    revoke(v.outline);
    revokeFrames();
    const [horizon, disc, pano, photo, frames, disagree, outline] = views;
    [v.horizon, v.disc, v.frames] = [horizon, disc, frames];
    [v.pano, v.photo] = [toUrl(pano), toUrl(photo)];
    v.disagree = toUrl(disagree, "image/png");
    v.outline = toUrl(outline, "image/png");
    v.pending = new Set(v.frames?.frames.filter((f) => f.off).map((f) => f.name));
    render();
    if (v.frames) await loadPhotos(mine, v.frames);
    const cols = v.st.site ? await api.columns() : null;
    if (mine !== generation) return;
    for (const url of v.colFrames.values()) revoke(url);
    [v.cols, v.selCol, v.colFrames] = [cols, null, new Map()];
    render();
  }

  // Each edit refits in the engine; the table shows its answer, never a guess.
  async function columnsAct(doing: string, act: () => Promise<Columns>) {
    v.colBusy = doing;
    render();
    try {
      v.cols = await act();
      v.error = "";
    } catch (e) {
      v.error = (e as Error).message;
    }
    v.colBusy = "";
    render();
  }

  async function showColumn(az: number) {
    v.selCol = az;
    render();
    const col = v.cols?.columns.find((c) => c.az === az);
    for (const f of col?.frames ?? []) {
      const key = `${az}/${f.name}`;
      if (v.colFrames.has(key)) continue;
      const url = toUrl(await api.columnFrame(az, f.name));
      if (url) v.colFrames.set(key, url);
      if (v.selCol === az) render();
    }
  }

  // The strip's thumbnails and the hover footprints: one small picture per photo,
  // fetched after the panorama is on screen.
  async function loadPhotos(mine: number, frames: Frames) {
    const thumbs = new Map<string, string>();
    const footprints = new Map<string, string>();
    const fps: Footprint[] = [];
    const one = async (f: Frames["frames"][number]) => {
      const [thumb, fp] = await Promise.all([
        api.frameImage("thumb", f.name),
        f.box ? api.frameImage("footprint", f.name) : null,
      ]);
      const t = toUrl(thumb);
      if (t) thumbs.set(f.name, t);
      const m = toUrl(fp, "image/png");
      if (m && fp && f.box) {
        footprints.set(f.name, m);
        // Hover needs the mask's pixels; without a canvas the strip still works.
        await decode(f.name, f.box, fp).then(
          (d) => fps.push(d),
          (e: Error) => console.warn(`no hover for ${f.name}: ${e.message}`),
        );
      }
    };
    // A few at a time: each thumbnail decodes a full-size photo in the engine.
    const queue = [...frames.frames];
    await Promise.all(
      Array.from({ length: PHOTO_LOADS }, async () => {
        for (let f = queue.shift(); f; f = queue.shift()) await one(f);
      }),
    );
    if (mine !== generation) {
      for (const url of [...thumbs.values(), ...footprints.values()]) revoke(url);
      return;
    }
    [v.thumbs, v.footprints, v.fps] = [thumbs, footprints, fps];
    render();
  }

  // A new build replaces every layer, so the last one's pictures go.
  function startBuild() {
    const b = v.build;
    for (const url of [...b.thumbs.values(), ...b.layers.values(), ...b.verdicts.values()])
      revoke(url);
    revoke(b.progress);
    v.build = {
      thumbs: new Map(),
      layers: new Map(),
      verdicts: new Map(),
      progress: null,
      progressN: 0,
    };
  }

  // Fetch a few of the pictures the running build has produced and we lack.
  async function fetchBuild() {
    const job = v.st.job;
    if (!job || job.site !== v.st.site?.slug) return;
    const b = v.build;
    const wants: [Map<string, string>, string, () => Promise<Uint8Array | null>, string][] = [];
    for (const f of job.frames) {
      if (!b.thumbs.has(f.name))
        wants.push([b.thumbs, f.name, () => api.frameImage("thumb", f.name), "image/jpeg"]);
      if (f.layer && !b.layers.has(f.layer)) {
        const layer = f.layer;
        wants.push([b.layers, layer, () => api.buildImage("layer", layer), "image/webp"]);
      }
      if (f.layer && f.state === "judged" && !b.verdicts.has(f.layer)) {
        const layer = f.layer;
        wants.push([b.verdicts, layer, () => api.buildImage("verdict", layer), "image/png"]);
      }
    }
    for (const [into, key, get, type] of wants.slice(0, PHOTO_LOADS * 2)) {
      const url = toUrl(await get(), type);
      if (url) into.set(key, url);
    }
    if (job.outline > b.progressN) {
      const seen = job.outline;
      const url = toUrl(await api.image("progress"), "image/png");
      if (url) {
        revoke(b.progress);
        b.progress = url;
        b.progressN = seen;
      }
    }
  }

  const curate = (off: string[], restitch: boolean) => {
    startBuild();
    return api.curate(off, restitch).then(adopt).catch(fail);
  };

  // While a site is building, refresh its progress; load the site once it is done.
  function poll() {
    if (polling || v.st.job?.status !== "running") return;
    polling = true;
    setTimeout(async () => {
      polling = false;
      try {
        v.st = await api.getState();
        v.error = ""; // the engine answered: an earlier failed read no longer applies
        if (v.st.job?.status === "running") {
          await fetchBuild();
          return (render(), poll());
        }
        v.sites = (await api.listSites()).sites;
        await loadSite();
      } catch (e) {
        fail(e as Error);
        poll(); // a hiccup in reading progress does not stop the build
      }
    }, 1000);
  }

  async function adopt(next: AppState | null) {
    if (!next) return;
    v.st = next;
    v.error = "";
    v.sites = (await api.listSites()).sites;
    await loadSite();
    poll();
  }

  // The Connect tab: the telescope's status, refreshed while the tab is open.
  let scopePolling = false;
  async function readScope() {
    v.scope = await api.scopeStatus();
  }
  function pollScope() {
    if (scopePolling || v.st.tab !== "connect" || v.st.scope.link === "none") return;
    scopePolling = true;
    setTimeout(async () => {
      scopePolling = false;
      if (v.st.tab !== "connect" || v.st.scope.link === "none") return;
      try {
        await readScope();
        render();
      } catch (e) {
        fail(e as Error);
      }
      pollScope();
    }, SCOPE_POLL_MS);
  }
  // One scope request at a time, with what it is doing on screen.
  async function scopeAct(doing: string, act: () => Promise<void>) {
    v.scopeBusy = doing;
    render();
    try {
      await act();
      v.error = "";
    } catch (e) {
      v.error = (e as Error).message;
    }
    v.scopeBusy = "";
    render();
    pollScope();
  }
  const scopeProps = () => ({
    linked: v.st.scope.link !== "none",
    status: v.scope,
    found: v.found,
    busy: v.scopeBusy,
    frame: v.frame,
    preview: v.preview,
    onPoint: (az: number, alt: number) =>
      scopeAct(`Slewing to az ${az} alt ${alt}, clear of the Sun…`, async () => {
        v.scope = await api.pointScope(az, alt);
      }),
    onFrame: (ms: number) =>
      scopeAct(`Exposing ${ms} ms…`, async () => {
        v.frame = await api.takeFrame(ms);
        revoke(v.preview);
        v.preview = toUrl(await api.framePreview());
      }),
    onFind: () =>
      scopeAct("Looking for telescopes…", async () => {
        v.found = (await api.discoverScopes()).scopes;
      }),
    onConnect: (host: string) =>
      scopeAct(`Connecting to ${host}…`, async () => {
        v.st = await api.connectScope(host);
        await readScope();
      }),
    onPark: () =>
      scopeAct("Parking: clearing the Sun, then closing the arm…", async () => {
        v.scope = await api.parkScope();
      }),
    onDisconnect: () =>
      scopeAct("Parking, then disconnecting…", async () => {
        v.st = await api.disconnectScope();
        await readScope();
      }),
  });

  const newSite = () => {
    startBuild();
    return api.pickPhotos().then(adopt).catch(fail);
  };

  function siteBar(): HTMLElement {
    const pick = el("select", {}, el("option", { value: "", textContent: "Choose a site…" }));
    pick.setAttribute("aria-label", "Site");
    pick.dataset.focus = "site";
    for (const s of v.sites)
      pick.append(el("option", { value: s.slug, textContent: `${s.name} (${s.photos} photos)` }));
    pick.value = v.st.site?.slug ?? "";
    pick.addEventListener("change", () => {
      v.managing = false;
      if (pick.value) api.openSite(pick.value).then(adopt).catch(fail);
    });
    const parts: Node[] = [pick];
    const open = v.st.site;
    if (open) {
      // The open site's name, edited in place: Enter or leaving the box saves it.
      const name = el("input", { type: "text", value: open.name, className: "site-name" });
      name.setAttribute("aria-label", "Site name");
      name.dataset.focus = "site-name";
      name.addEventListener("keydown", (e) => e.key === "Enter" && name.blur());
      name.addEventListener("change", () => {
        if (name.value.trim() && name.value.trim() !== open.name) renameSite(open.slug, name.value);
      });
      parts.push(name);
    }
    const add = el("button", { textContent: "New site…" });
    add.dataset.focus = "new-site";
    add.addEventListener("click", newSite);
    const manage = el("button", { textContent: v.managing ? "Done" : "Manage sites…" });
    manage.dataset.focus = "manage";
    manage.addEventListener("click", () => ((v.managing = !v.managing), render()));
    return el("header", {}, ...parts, add, manage);
  }

  function tabBar(): HTMLElement {
    const nav = el(
      "nav",
      {},
      ...v.st.tabs.map((t) => {
        const b = el("button", { textContent: title(t) });
        b.setAttribute("role", "tab");
        b.setAttribute("aria-selected", String(t === v.st.tab));
        b.dataset.focus = `tab:${t}`;
        b.addEventListener("click", () => {
          api
            .setTab(t)
            .then(async (next) => {
              v.st = next;
              v.error = "";
              if (t === "connect") await readScope();
              render();
              pollScope();
            })
            .catch(fail);
        });
        return b;
      }),
    );
    nav.setAttribute("role", "tablist");
    return nav;
  }

  const renameSite = (slug: string, name: string) =>
    api
      .renameSite(slug, name)
      .then(async (next) => ((v.st = next), (v.sites = (await api.listSites()).sites), render()))
      .catch(fail);

  const deleteSite = (slug: string, name: string) => {
    if (!confirm(`Delete "${name}" and its copies of the photos? This cannot be undone.`)) return;
    api
      .deleteSite(slug)
      .then(async (next) => {
        v.st = next;
        v.sites = (await api.listSites()).sites;
        await loadSite();
      })
      .catch(fail);
  };

  function content(): Node {
    if (v.managing)
      return manageView(
        v.sites,
        v.st.site?.slug ?? null,
        v.st.job?.status === "running" ? v.st.job.site : null,
        renameSite,
        deleteSite,
      );
    const tab = v.st.tab;
    const job = v.st.job;
    if (tab === "panorama" && job?.frames.length && job.site === v.st.site?.slug) {
      if (job.status === "running") return buildView(job, v.build);
      if (job.status === "failed") return el("div", {}, jobPanel(job), buildView(job, v.build));
    }
    if (tab === "panorama")
      return panoramaView(
        v.st,
        v.pano,
        newSite,
        v.frames && {
          frames: v.frames,
          disagree: v.disagree,
          thumbs: v.thumbs,
          footprints: v.footprints,
          hit: (x, y) => pick(v.fps, x, y),
          pending: v.pending,
          showDisagree: v.showDisagree,
          onToggle: (name) => (
            v.pending.has(name) ? v.pending.delete(name) : v.pending.add(name),
            render()
          ),
          onShowDisagree: (on) => ((v.showDisagree = on), render()),
          onApply: (restitch) => curate([...v.pending], restitch),
        },
        v.st.site?.panorama ? () => curate([], false) : null,
      );
    if (tab === "connect") return connectView(scopeProps());
    if (tab === "fit" && v.cols)
      return columnsView({
        cols: v.cols,
        selected: v.selCol,
        frames: v.colFrames,
        busy: v.colBusy,
        onSelect: (az) => void showColumn(az),
        onInclude: (az, on) =>
          void columnsAct(`Refitting ${on ? "with" : "without"} az ${az}…`, () =>
            api.editColumn(az, { included: on }),
          ),
        onTag: (az, tag: ColumnTag, on) => {
          const now = v.cols?.columns.find((c) => c.az === az)?.tags ?? [];
          const tags = on ? [...now, tag] : now.filter((t) => t !== tag);
          void columnsAct(`Tagging az ${az}…`, () => api.editColumn(az, { tags }));
        },
        onApply: () => {
          startBuild();
          api.applyOrientation().then(adopt).catch(fail);
        },
        clicks: v.clicks,
        onFrameClick: (az, name, pt) => {
          const same = v.clicks && v.clicks.az === az && v.clicks.name === name;
          const pts = same ? [...v.clicks!.pts, pt] : [pt];
          if (pts.length < 3) return ((v.clicks = { az, name, pts }), render());
          v.clicks = null;
          const [p1, p2, sky] = pts;
          void columnsAct(`Reading az ${az} at the clicked edge…`, () =>
            api.clickEdge(az, name, p1, p2, sky),
          );
        },
        onFitAll: () =>
          void columnsAct("Fitting every included column (this takes minutes)…", () =>
            api.fitColumns(),
          ),
      });
    if (tab === "fit") return fitView(v.horizon);
    if (tab === "horizon") {
      if (!v.st.site) return el("p", { textContent: "Choose or create a site first." });
      if (!v.horizon || !v.disc) return el("p", { textContent: "This site has no horizon yet." });
      return horizonView({
        horizon: v.horizon,
        disc: v.disc,
        photo: v.photo,
        outline: v.outline,
        spin: v.st.spin,
        layers: v.layers,
        onToggle: (l) => (v.layers.has(l) ? v.layers.delete(l) : v.layers.add(l), render()),
        // Live while dragging: turn the rotor only, so the slider keeps its grip.
        onSpinPreview: (deg) => {
          const rotor = root.querySelector<HTMLElement>(".rotor");
          if (rotor) rotor.style.transform = `rotate(${deg}deg)`;
          const shown = root.querySelector(".spin-value");
          if (shown) shown.textContent = ` ${Math.round(deg)}°`;
        },
        onSpin: (deg) =>
          api
            .setSpin(deg)
            .then((next) => ((v.st = next), render()))
            .catch(fail),
      });
    }
    return el("p", { textContent: LATER[tab] ?? "" });
  }

  function render() {
    const s = v.st;
    // While this site builds, the heading says what the build is doing.
    const job = s.job;
    const building = s.tab === "panorama" && job?.status === "running" && job.site === s.site?.slug;
    const heading = building ? `${title(s.tab)}: ${buildTitle(job)}` : title(s.tab);
    const panel = el(
      "section",
      {},
      el("h1", { textContent: v.managing ? "Sites" : heading }),
      content(),
    );
    panel.setAttribute("role", "tabpanel");
    const status = el(
      "footer",
      {},
      `engine ${s.version} · site: ${s.site?.slug ?? "none"} · scope: ${s.scope.link} · sun mode: ${s.sun_mode.toUpperCase()}`,
    );
    const parts: Node[] = [siteBar(), tabBar(), panel, status];
    if (v.error) {
      const alert = el("p", { textContent: v.error, className: "banner warn" });
      alert.setAttribute("role", "alert");
      parts.splice(2, 0, alert);
    }
    // Re-rendering replaces every control, so hand focus back to the one that
    // had it (a tab, a layer toggle, the spin slider), found by its key.
    const focused = (root.ownerDocument.activeElement as HTMLElement | null)?.dataset?.focus;
    root.replaceChildren(...parts);
    if (focused) root.querySelector<HTMLElement>(`[data-focus="${focused}"]`)?.focus();
  }

  // Photos dropped anywhere on the window start a new site.
  root.ownerDocument.addEventListener("dragover", (e) => e.preventDefault());
  root.ownerDocument.addEventListener("drop", (e) => {
    e.preventDefault();
    const files = [...(e.dataTransfer?.files ?? [])].filter((f) => PHOTO.test(f.name));
    if (files.length === 0) return fail(new Error("Drop photographs: .jpg, .png or .tif files."));
    startBuild();
    api.createSite(files).then(adopt).catch(fail);
  });

  if (v.st.tab === "connect") await readScope().catch(fail);
  await loadSite();
  poll();
  pollScope();
}
