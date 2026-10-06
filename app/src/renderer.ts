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
  let keepFocus = false;
  let polling = false;

  const fail = (e: Error) => ((v.error = e.message), render());
  const blob = (bytes: Uint8Array | null, old: string | null) => {
    if (old) URL.revokeObjectURL(old);
    return bytes
      ? URL.createObjectURL(new Blob([bytes as BlobPart], { type: "image/jpeg" }))
      : null;
  };

  async function loadSite() {
    v.horizon = v.disc = null;
    v.pano = blob(null, v.pano);
    v.photo = blob(null, v.photo);
    if (!v.st.site) return render();
    v.horizon = await api.horizon();
    v.disc = await api.disc();
    v.pano = blob(await api.image("panorama"), null);
    v.photo = blob(await api.image("disc"), null);
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

  const newSite = () => api.pickPhotos().then(adopt, fail);

  function siteBar(): HTMLElement {
    const pick = el("select", {}, el("option", { value: "", textContent: "Choose a site…" }));
    pick.setAttribute("aria-label", "Site");
    for (const s of v.sites)
      pick.append(el("option", { value: s.slug, textContent: `${s.slug} (${s.photos} photos)` }));
    pick.value = v.st.site?.slug ?? "";
    pick.addEventListener("change", () => {
      if (pick.value) api.openSite(pick.value).then(adopt, fail);
    });
    const add = el("button", { textContent: "New site…" });
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
        b.addEventListener("click", () => {
          keepFocus = true;
          api.setTab(t).then((next) => ((v.st = next), (v.error = ""), render()), fail);
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
        },
        onSpin: (deg) => api.setSpin(deg).then((next) => ((v.st = next), render()), fail),
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
    root.replaceChildren(...parts);
    // Re-rendering replaced the button that had focus; give it to the selected tab.
    if (keepFocus) root.querySelector<HTMLElement>('[aria-selected="true"]')?.focus();
    keepFocus = false;
  }

  // Photos dropped anywhere on the window start a new site.
  root.ownerDocument.addEventListener("dragover", (e) => e.preventDefault());
  root.ownerDocument.addEventListener("drop", (e) => {
    e.preventDefault();
    const files = [...(e.dataTransfer?.files ?? [])].filter((f) => PHOTO.test(f.name));
    if (files.length === 0) return fail(new Error("Drop photographs: .jpg, .png or .tif files."));
    api.createSite(files).then(adopt, fail);
  });

  await loadSite();
  poll();
}
