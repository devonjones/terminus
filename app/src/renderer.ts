import type { AppState, Disc, Horizon, SiteSummary, TerminusApi } from "./api/types";
import { el, fitView, horizonView, LAYERS, panoramaView, type Layer } from "./views";

const title = (s: string) => s[0].toUpperCase() + s.slice(1);
const PHOTO = /\.(jpe?g|png|tiff?)$/i;
const LATER: Record<string, string> = {
  connect: "Connecting the telescope arrives with the Alpaca driver.",
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
  layers: Set<Layer>;
  error: string;
}

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
    layers: new Set(LAYERS.map((l) => l.key)),
    error: "",
  };
  let polling = false;
  let generation = 0;

  const fail = (e: Error) => ((v.error = e.message), render());
  const toUrl = (bytes: Uint8Array | null) =>
    bytes ? URL.createObjectURL(new Blob([bytes as BlobPart], { type: "image/jpeg" })) : null;
  const revoke = (url: string | null) => url && URL.revokeObjectURL(url);

  // Loads can overlap (open a site, then another): only the newest one lands,
  // and the photos of the one it replaces are released.
  async function loadSite() {
    const mine = ++generation;
    const views = v.st.site
      ? await Promise.all([api.horizon(), api.disc(), api.image("panorama"), api.image("disc")])
      : ([null, null, null, null] as const);
    if (mine !== generation) return;
    revoke(v.pano);
    revoke(v.photo);
    [v.horizon, v.disc] = [views[0], views[1]];
    [v.pano, v.photo] = [toUrl(views[2]), toUrl(views[3])];
    render();
  }

  // While a site is building, refresh its progress; load the site once it is done.
  function poll() {
    if (polling || v.st.job?.status !== "running") return;
    polling = true;
    setTimeout(async () => {
      polling = false;
      try {
        v.st = await api.getState();
        if (v.st.job?.status === "running") return (render(), poll());
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

  const newSite = () => api.pickPhotos().then(adopt).catch(fail);

  function siteBar(): HTMLElement {
    const pick = el("select", {}, el("option", { value: "", textContent: "Choose a site…" }));
    pick.setAttribute("aria-label", "Site");
    pick.dataset.focus = "site";
    for (const s of v.sites)
      pick.append(el("option", { value: s.slug, textContent: `${s.slug} (${s.photos} photos)` }));
    pick.value = v.st.site?.slug ?? "";
    pick.addEventListener("change", () => {
      if (pick.value) api.openSite(pick.value).then(adopt).catch(fail);
    });
    const add = el("button", { textContent: "New site…" });
    add.dataset.focus = "new-site";
    add.addEventListener("click", newSite);
    return el("header", {}, pick, add);
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
            .then((next) => ((v.st = next), (v.error = ""), render()))
            .catch(fail);
        });
        return b;
      }),
    );
    nav.setAttribute("role", "tablist");
    return nav;
  }

  function content(): Node {
    const tab = v.st.tab;
    if (tab === "panorama") return panoramaView(v.st, v.pano, newSite);
    if (tab === "fit") return fitView(v.horizon);
    if (tab === "horizon") {
      if (!v.st.site) return el("p", { textContent: "Choose or create a site first." });
      if (!v.horizon || !v.disc) return el("p", { textContent: "This site has no horizon yet." });
      return horizonView({
        horizon: v.horizon,
        disc: v.disc,
        photo: v.photo,
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
    const panel = el("section", {}, el("h1", { textContent: title(s.tab) }), content());
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
    api.createSite(files).then(adopt).catch(fail);
  });

  await loadSite();
  poll();
}
