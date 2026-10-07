import type {
  AppState,
  ColumnTag,
  Columns,
  TelescopeColumn,
  BuildFrame,
  Disc,
  FitColumn,
  Frames,
  Horizon,
  Job,
  ScopeFrame,
  ScopeList,
  ScopeStatus,
  SiteSummary,
} from "./api/types";
import { dragSpin } from "./spin";

export function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  props: Partial<HTMLElementTagNameMap[K]> = {},
  ...children: (Node | string)[]
): HTMLElementTagNameMap[K] {
  const e = Object.assign(document.createElement(tag), props);
  e.append(...children);
  return e;
}

const SVG = "http://www.w3.org/2000/svg";
function svg(tag: string, attrs: Record<string, string | number> = {}, ...children: Node[]) {
  const e = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, String(v));
  e.append(...children);
  return e;
}

// A control that waits on the engine stays focusable, so a redraw can hand focus
// back to it; while busy it is marked unavailable and its action does nothing.
function waitable<T extends HTMLElement>(e: T, busy: string, act: () => void): T {
  if (busy) e.setAttribute("aria-disabled", "true");
  e.addEventListener("click", () => !busy && act());
  return e;
}

const points = (xy: number[][]) => xy.map(([x, y]) => `${x},${y}`).join(" ");

export function jobPanel(job: Job): HTMLElement {
  const steps = {
    mosaic: "Stitching the photos into a panorama",
    reblend: "Blending the photos and voting on the sky",
    skymask: "Reading the horizon off it",
    horizon: "Drawing the horizon and the planning horizon",
  };
  let head = "Built.";
  if (job.status === "running") head = `${job.step ? steps[job.step] : "Starting"}…`;
  if (job.status === "failed")
    head = `Building this site failed: ${job.error ?? "no reason given"}`;
  const p = el("p", { textContent: head });
  if (job.status === "failed") p.setAttribute("role", "alert");
  else p.setAttribute("role", "status");
  return el(
    "div",
    { className: "job" },
    p,
    el("pre", { textContent: job.log.slice(-8).join("\n") }),
  );
}

export function dropZone(onPick: () => void): HTMLElement {
  const pick = el("button", { textContent: "Choose photos…" });
  pick.dataset.focus = "pick";
  pick.addEventListener("click", onPick);
  return el(
    "div",
    { className: "drop" },
    el("p", { textContent: "Drop the photographs of a site here, or" }),
    pick,
  );
}

// The pictures a build view shows, fetched as the build produces them.
export interface BuildImages {
  thumbs: Map<string, string>; // by photo name
  layers: Map<string, string>; // by layer file
  verdicts: Map<string, string>; // by layer file
  progress: string | null; // the horizon so far, over the whole panorama
  progressN: number; // which redraw of it we hold (job.outline)
}

const PHASES: Record<string, string> = {
  matching: "Matching the photos against each other",
  solving: "Working out where each photo points",
  placing: "Laying each photo onto the panorama",
  segmenting: "Labelling what each photo shows: sky, trees, buildings",
  blending: "Blending the photos and matching their colours",
  judging: "Judging sky against terrain, photo by photo",
};

const PHASE_TITLES: Record<string, string> = {
  matching: "Matching",
  solving: "Solving",
  placing: "Placing",
  segmenting: "Segmenting",
  blending: "Blending",
  judging: "Judging",
};

// The build's phase for the page heading: "Matching — comparing neighbouring photos".
// The last two steps have no phases of their own: their names stand in.
const STEP_TITLES: Record<string, string> = {
  skymask: "Reading the horizon",
  horizon: "Drawing the horizons",
};
const STEP_LINES: Record<string, string> = {
  skymask: "Reading the horizon off the panorama",
  horizon: "Drawing the horizon and the planning horizon",
};

export function buildTitle(job: Job): string {
  const phase = STEP_TITLES[job.step ?? ""] ?? PHASE_TITLES[job.phase ?? ""] ?? "Starting";
  return job.detail ? `${phase} — ${job.detail}` : phase;
}

function frameDetail(f: BuildFrame): string {
  if (f.state === "dropped") return `left out: ${f.reason ?? "it could not be placed"}`;
  if (f.state === "off") return "turned off";
  const parts: string[] = [];
  if (f.points !== undefined) parts.push(`${f.points} points`);
  if (f.links) parts.push(`${f.links.length} neighbours`);
  return parts.join(" · ");
}

const STATES: Record<BuildFrame["state"], string> = {
  listed: "waiting",
  matched: "matched",
  dropped: "dropped",
  placed: "placed",
  off: "off",
  judged: "judged",
};

// cpfind's matching, as it happens: the photos on a ring in the order they were
// taken (roughly their order round the horizon), and a line for every pair found
// to share points, thicker for more. A photo nothing reaches is one the stitch
// cannot place.
function matchGraph(job: Job, imgs: BuildImages): SVGElement {
  const [W, H, R] = [1000, 560, 230];
  const n = job.frames.length;
  const where = new Map(
    job.frames.map((f, i) => {
      const a = (2 * Math.PI * i) / Math.max(n, 1) - Math.PI / 2;
      return [f.name, [W / 2 + R * 1.85 * Math.cos(a), H / 2 + R * Math.sin(a)]];
    }),
  );
  const active = new Set(job.active);
  const g = svg("g");
  for (const p of job.pairs) {
    const [a, b] = [where.get(p.a), where.get(p.b)];
    if (!a || !b) continue;
    const hot = active.has(p.a) && active.has(p.b);
    g.append(
      svg("line", {
        class: hot ? "edge hot" : "edge",
        x1: a[0],
        y1: a[1],
        x2: b[0],
        y2: b[1],
        "stroke-width": 1 + Math.log2(p.matches),
      }),
    );
  }
  for (const f of job.frames) {
    const [x, y] = where.get(f.name)!;
    const cls = ["node", f.state, active.has(f.name) ? "hot" : ""].join(" ");
    const tip = svg("title");
    tip.textContent = `${f.name}: ${frameDetail(f) || STATES[f.state]}`;
    g.append(
      svg(
        "g",
        { class: cls },
        tip,
        svg("image", {
          href: imgs.thumbs.get(f.name) ?? "",
          x: x - 32,
          y: y - 24,
          width: 64,
          height: 48,
          preserveAspectRatio: "xMidYMid slice",
        }),
        svg("rect", { x: x - 32, y: y - 24, width: 64, height: 48, rx: 4 }),
      ),
    );
  }
  return svg("svg", { class: "graph", viewBox: `0 0 ${W} ${H}`, role: "img" }, g);
}

// A build as it happens: each photo appears on the panorama as it is laid down,
// its sky verdict washes over it as it is judged, and the list says where every
// photo is up to, including the ones the stitch had to leave out and why.
export function buildView(job: Job, imgs: BuildImages): HTMLElement {
  const [W, H] = job.canvas ?? [2, 1];
  const head = STEP_LINES[job.step ?? ""] ?? PHASES[job.phase ?? ""] ?? "Starting";
  const status = el("p", {
    className: "phase",
    textContent: job.status === "running" ? `${head}${job.detail ? `: ${job.detail}` : ""}…` : "",
  });
  status.setAttribute("role", "status");

  const active = new Set(job.active);
  const live = el("div", { className: "live" });
  live.style.aspectRatio = `${W} / ${H}`;
  const at = (box: number[]) => ({
    left: `${(box[0] / W) * 100}%`,
    top: `${(box[1] / H) * 100}%`,
    width: `${(box[2] / W) * 100}%`,
    height: `${(box[3] / H) * 100}%`,
  });
  for (const f of job.frames) {
    const url = f.layer && imgs.layers.get(f.layer);
    if (!f.box || !url) continue;
    const img = el("img", { src: url, alt: "", className: active.has(f.name) ? "hot" : "" });
    Object.assign(img.style, at(f.box));
    live.append(img);
  }
  // The photo being worked on gets curation's hover highlight: a yellow wash
  // clipped to the photo's own shape (its layer's transparency).
  for (const f of job.frames) {
    const url = f.layer && imgs.layers.get(f.layer);
    if (!f.box || !url || !active.has(f.name)) continue;
    const hl = el("div", { className: "hl" });
    Object.assign(hl.style, at(f.box), {
      maskImage: `url("${url}")`,
      webkitMaskImage: `url("${url}")`,
    });
    live.append(hl);
  }
  for (const f of job.frames) {
    const mask = f.layer && imgs.verdicts.get(f.layer);
    if (!f.box || !mask || f.state !== "judged") continue;
    const sky = el("div", { className: "sky" });
    Object.assign(sky.style, at(f.box), {
      maskImage: `url("${mask}")`,
      webkitMaskImage: `url("${mask}")`,
    });
    live.append(sky);
  }

  // The horizon as far as the photos judged so far can tell, redrawn after each.
  if (imgs.progress) live.append(el("img", { src: imgs.progress, alt: "", className: "progress" }));

  const count = (s: BuildFrame["state"]) => job.frames.filter((f) => f.state === s).length;
  const placed = job.frames.filter((f) => f.layer).length;
  const summary = el("p", {
    textContent: `${job.frames.length} photos · ${placed} placed · ${count("dropped")} left out`,
  });
  const list = el("ul", { className: "progress" });
  list.setAttribute("aria-label", "Photos");
  for (const f of job.frames) {
    const li = el(
      "li",
      { className: `${f.state}${active.has(f.name) ? " hot" : ""}` },
      el("img", { src: imgs.thumbs.get(f.name) ?? "", alt: "" }),
      el("span", { className: "name", textContent: f.name }),
      el("span", { className: "chip", textContent: STATES[f.state] }),
      el("small", { textContent: frameDetail(f) }),
    );
    list.append(li);
  }
  // Until photos start landing on the panorama, the matching is the picture.
  const matching = !job.frames.some((f) => f.layer);
  const shown: Node = matching ? matchGraph(job, imgs) : live;
  const legend = el(
    "p",
    { className: "legend" },
    matching
      ? "Blue lines: photos found to share points (thicker for more). Yellow: the pair just compared. Red: a photo the stitch could not place."
      : "Yellow wash and edge: the photo being worked on. Blue: sky, as each photo judged it. Yellow line: the horizon so far.",
  );
  const compared = job.compared
    ? ` · ${job.compared} pairs compared, ${job.pairs.length} share points`
    : "";
  summary.textContent += compared;
  return el("div", { className: "build" }, status, shown, legend, summary, list);
}

// Every site, to rename or delete. The one being built cannot be deleted.
export function manageView(
  sites: SiteSummary[],
  open: string | null,
  building: string | null,
  onRename: (slug: string, name: string) => void,
  onDelete: (slug: string, name: string) => void,
): HTMLElement {
  if (!sites.length) return el("p", { textContent: "No sites yet." });
  const rows = sites.map((s) => {
    const name = el("input", { type: "text", value: s.name });
    name.setAttribute("aria-label", `Name of ${s.slug}`);
    name.dataset.focus = `manage-name:${s.slug}`;
    name.addEventListener("keydown", (e) => e.key === "Enter" && name.blur());
    name.addEventListener("change", () => {
      if (name.value.trim() && name.value.trim() !== s.name) onRename(s.slug, name.value);
    });
    const del = el("button", {
      textContent: "Delete",
      disabled: s.slug === building,
      title: s.slug === building ? "It is being built" : `Delete ${s.name}`,
    });
    del.setAttribute("aria-label", `Delete ${s.name}`);
    del.addEventListener("click", () => onDelete(s.slug, s.name));
    return el(
      "tr",
      { className: s.slug === open ? "open" : "" },
      el("td", {}, name),
      el("td", { textContent: String(s.photos) }),
      el("td", { textContent: s.mask ? "yes" : "no" }),
      el("td", { textContent: s.updated.replace("T", " ") }),
      el("td", {}, del),
    );
  });
  const head = el(
    "tr",
    {},
    ...["Name", "Photos", "Horizon", "Last changed", ""].map((h) => el("th", { textContent: h })),
  );
  return el("table", { className: "sites" }, el("thead", {}, head), el("tbody", {}, ...rows));
}

export interface ConnectProps {
  linked: boolean; // the sidecar holds a link, whether or not a status read worked
  status: ScopeStatus | null; // null until first read
  found: ScopeList["scopes"] | null; // null until a search has run
  busy: string; // what is in progress, in words; "" when idle
  frame: ScopeFrame | null; // the last frame taken
  preview: string | null; // blob: URL of that frame in colour
  onPoint(az: number, alt: number): void;
  onFrame(exposureMs: number): void;
  onFind(): void;
  onConnect(host: string): void;
  onPark(): void;
  onDisconnect(): void;
}

const deg = (n: number) => `${n.toFixed(1)}°`;

// The telescope: find it, link it, see where it points and where the Sun is.
// Every motion is decided and Sun-checked by the engine; these are requests.
export function connectView(p: ConnectProps): HTMLElement {
  const busy = p.busy ? el("p", { textContent: p.busy, className: "busy" }) : "";
  const st = p.status;
  if (!p.linked) {
    const find = waitable(el("button", { textContent: "Find telescopes" }), p.busy, p.onFind);
    find.dataset.focus = "scope-find";
    const host = el("input", { type: "text", placeholder: "10.0.0.20" });
    host.setAttribute("aria-label", "Telescope address");
    host.dataset.focus = "scope-host";
    const go = waitable(el("button", { textContent: "Connect" }), p.busy, () => {
      if (host.value.trim()) p.onConnect(host.value.trim());
    });
    go.dataset.focus = "scope-connect";
    host.addEventListener("keydown", (e) => e.key === "Enter" && go.click());
    const list =
      p.found === null
        ? ""
        : p.found.length === 0
          ? el("p", { textContent: "No telescopes answered. Is Alpaca on in the Seestar app?" })
          : el(
              "ul",
              { className: "found" },
              ...p.found.map((f) => {
                const b = waitable(
                  el("button", { textContent: `Connect to ${f.host}` }),
                  p.busy,
                  () => p.onConnect(f.host),
                );
                b.dataset.focus = `scope-host:${f.host}`;
                return el("li", {}, b);
              }),
            );
    return el(
      "div",
      { className: "connect" },
      el("p", {
        textContent:
          "Turn on Alpaca in the Seestar app (Settings), with the scope on your Wi-Fi in EQ mode.",
      }),
      el("div", { className: "row" }, find, host, go),
      list,
      busy,
    );
  }
  const leave = waitable(
    el("button", { textContent: "Park and disconnect" }),
    p.busy,
    p.onDisconnect,
  );
  leave.dataset.focus = "scope-disconnect";
  if (!st || st.link === "none")
    return el(
      "div",
      { className: "connect" },
      el("p", { textContent: "Linked. Reading the telescope…" }),
      el("div", { className: "row" }, leave),
      busy,
    );
  const rows: [string, string][] = [
    ["Telescope", st.host ?? ""],
    ["Mount", st.eq ? "EQ mode" : "not in EQ mode: terminus needs EQ mode"],
    ["Arm", st.stowed ? "closed" : "open"],
    ["Pointing", st.stowed ? "stowed" : `az ${deg(st.az!)} alt ${deg(st.alt!)}`],
    ["Sun", `az ${deg(st.sun!.az)} alt ${deg(st.sun!.alt)}`],
    ["Keeps clear of the Sun by", deg(st.cone!)],
  ];
  if (st.moving) rows.push(["Now", "moving"]);
  const table = el(
    "dl",
    { className: "scope" },
    ...rows.flatMap(([k, v]) => [el("dt", { textContent: k }), el("dd", { textContent: v })]),
  );
  const park = waitable(
    el("button", { textContent: "Park", disabled: !!st.stowed }),
    p.busy,
    p.onPark,
  );
  park.dataset.focus = "scope-park";
  const notes: Node[] = [];
  if (st.stowed)
    notes.push(
      el("p", {
        className: "banner",
        textContent: "The arm is closed. Open it in the Seestar app, then close the app.",
      }),
    );
  if (!st.eq)
    notes.push(
      el("p", { className: "banner warn", textContent: "Switch the mount to EQ mode in the app." }),
    );
  if (!st.stowed && st.sun && st.sun.alt > 0)
    notes.push(
      el("p", {
        className: "banner warn",
        textContent:
          "The Sun is up. Run with the scope in shade and a solar-safe cap to hand, and aim away " +
          `from the Sun's part of the sky: every slew is kept ${st.cone ?? 30}° clear of it, but shade is what protects the camera.`,
      }),
    );
  const aim: Node[] = st.stowed ? [] : [aimRow(p), frameRow(p)];
  if (p.frame)
    aim.push(
      el("p", {
        className: "frame-stats",
        textContent: `${p.frame.exposure_ms} ms: median ${Math.round(p.frame.median)}, ${(
          p.frame.saturated * 100
        ).toFixed(1)}% saturated`,
      }),
    );
  if (p.preview) aim.push(el("img", { src: p.preview, className: "frame", alt: "Last frame" }));
  return el(
    "div",
    { className: "connect" },
    table,
    ...notes,
    el("div", { className: "row" }, park, leave),
    ...aim,
    busy,
  );
}

function numberBox(label: string, value: string, focus: string): HTMLInputElement {
  const box = el("input", { type: "number", value, step: "any" });
  box.setAttribute("aria-label", label);
  box.dataset.focus = focus;
  return box;
}

// Where to point: the engine checks the target and the path against the Sun.
function aimRow(p: ConnectProps): HTMLElement {
  const az = numberBox("Azimuth", "", "scope-az");
  const alt = numberBox("Altitude", "", "scope-alt");
  const go = waitable(el("button", { textContent: "Go" }), p.busy, () => {
    if (az.value !== "" && alt.value !== "") p.onPoint(Number(az.value), Number(alt.value));
  });
  go.dataset.focus = "scope-go";
  return el("div", { className: "row" }, "Go to az ", az, " alt ", alt, go);
}

function frameRow(p: ConnectProps): HTMLElement {
  const ms = numberBox("Exposure (ms)", String(p.frame?.exposure_ms ?? 2), "scope-ms");
  const take = waitable(el("button", { textContent: "Take a frame" }), p.busy, () => {
    if (ms.value !== "") p.onFrame(Number(ms.value));
  });
  take.dataset.focus = "scope-frame";
  return el("div", { className: "row" }, ms, " ms ", take);
}

export interface CurateProps {
  frames: Frames; // as last built: `off` is what the panorama now leaves out
  disagree: string | null; // blob: URLs
  thumbs: Map<string, string>;
  footprints: Map<string, string>;
  hit(x: number, y: number): string | null; // the photo at a panorama pixel
  pending: Set<string>; // the photos the user wants off
  showDisagree: boolean;
  onToggle(name: string): void;
  onShowDisagree(on: boolean): void;
  onApply(restitch: boolean): void;
}

export function panoramaView(
  st: AppState,
  pano: string | null,
  onPick: () => void,
  curate: CurateProps | null = null,
  onPrepare: (() => void) | null = null,
): HTMLElement {
  // Only this site's own build matters here; another site building elsewhere does not.
  const job = st.job && st.site && st.job.site === st.site.slug ? st.job : null;
  const busy = st.job?.status === "running";
  const parts: Node[] = [];
  if (job && job.status !== "done") parts.push(jobPanel(job));
  if (pano && curate) parts.push(curateView(pano, curate, busy));
  else if (pano) {
    parts.push(
      el("img", {
        className: "pano",
        src: pano,
        alt: "The panorama stitched from this site's photos",
      }),
    );
    if (onPrepare && !busy) {
      const prep = el("button", { textContent: "Prepare the photos for curation" });
      prep.dataset.focus = "prepare";
      prep.addEventListener("click", onPrepare);
      parts.push(
        el(
          "p",
          { className: "bar" },
          "This site was built before photos could be turned off. ",
          prep,
        ),
      );
    }
  } else if (!st.site) parts.push(dropZone(onPick));
  else if (job?.status !== "running")
    parts.push(el("p", { textContent: "This site has no panorama yet." }));
  return el("div", {}, ...parts);
}

// The panorama with its photos: hover to see which photo made a spot, click (or
// use the strip) to turn a photo off, then re-blend or re-stitch.
function curateView(pano: string, p: CurateProps, busy: boolean): HTMLElement {
  const { width: W, height: H } = p.frames;
  const byName = new Map(p.frames.frames.map((f) => [f.name, f]));
  const img = el("img", {
    className: "pano",
    src: pano,
    alt: "The panorama stitched from this site's photos",
  });
  const veil = el("img", { className: "veil", src: p.disagree ?? "", alt: "" });
  veil.hidden = !(p.showDisagree && p.disagree);
  const hl = el("div", { className: "hl" });
  hl.hidden = true;
  const here = el("p", { className: "here", textContent: "Hover the panorama to find a photo." });
  here.setAttribute("aria-live", "polite");
  const buttons = new Map<string, HTMLButtonElement>();

  let shown: string | null = null;
  const show = (name: string | null) => {
    if (name === shown) return;
    buttons.get(shown ?? "")?.classList.remove("hot");
    shown = name;
    const f = name ? byName.get(name) : undefined;
    const mask = name ? p.footprints.get(name) : undefined;
    if (!f?.box || !mask) {
      hl.hidden = true;
      here.textContent = "Hover the panorama to find a photo.";
      return;
    }
    const [x, y, w, h] = f.box;
    Object.assign(hl.style, {
      left: `${(x / W) * 100}%`,
      top: `${(y / H) * 100}%`,
      width: `${(w / W) * 100}%`,
      height: `${(h / H) * 100}%`,
      maskImage: `url("${mask}")`,
      webkitMaskImage: `url("${mask}")`,
    });
    hl.hidden = false;
    buttons.get(f.name)?.classList.add("hot");
    const off = p.pending.has(f.name);
    here.textContent = `${f.name}: click to turn it ${off ? "back on" : "off"}.`;
  };
  const at = (e: MouseEvent) => {
    const r = img.getBoundingClientRect();
    if (!r.width) return null;
    return p.hit(((e.clientX - r.left) / r.width) * W, ((e.clientY - r.top) / r.height) * H);
  };
  const stage = el("div", { className: "stage" }, img, veil, hl);
  stage.addEventListener("mousemove", (e) => show(at(e)));
  stage.addEventListener("mouseleave", () => show(null));
  stage.addEventListener("click", (e) => {
    const name = at(e);
    if (name && !busy) p.onToggle(name);
  });

  const strip = el("ul", { className: "strip" });
  strip.setAttribute("aria-label", "Photos");
  for (const f of p.frames.frames) {
    const off = p.pending.has(f.name);
    // A photo the stitch could not place is not in the panorama either: it shows
    // as off. Clicking it still decides whether the next re-stitch tries it again.
    const shownOff = off || f.dropped;
    let note = "";
    if (f.dropped && off) note = "the stitch could not place it; left out of re-stitches";
    else if (f.dropped) note = "the stitch could not place it";
    else if (!f.layer && !off) note = "re-stitch to bring it back";
    const b = el(
      "button",
      { className: shownOff ? "off" : "", disabled: busy, title: f.name },
      el("img", { src: p.thumbs.get(f.name) ?? "", alt: "" }),
      el("span", { textContent: f.name }),
      ...(note ? [el("small", { textContent: note })] : []),
    );
    b.setAttribute("aria-pressed", String(!shownOff));
    b.dataset.focus = `frame:${f.name}`;
    b.addEventListener("click", () => p.onToggle(f.name));
    b.addEventListener("mouseenter", () => show(f.name));
    b.addEventListener("focus", () => show(f.name));
    b.addEventListener("mouseleave", () => show(null));
    b.addEventListener("blur", () => show(null));
    buttons.set(f.name, b);
    strip.append(el("li", {}, b));
  }

  const applied = new Set(p.frames.frames.filter((f) => f.off).map((f) => f.name));
  const changed = applied.size !== p.pending.size || [...p.pending].some((n) => !applied.has(n));
  // A photo the last stitch left out has no layer to blend back in.
  const needsStitch = p.frames.frames.some((f) => !f.layer && f.off && !p.pending.has(f.name));
  const reblend = el("button", {
    textContent: "Re-blend",
    disabled: busy || !changed || needsStitch,
    title: needsStitch
      ? "A photo you turned back on was left out of the stitch: re-stitch instead"
      : "Blend the stitched photos again without the ones turned off (fast)",
  });
  reblend.dataset.focus = "reblend";
  reblend.addEventListener("click", () => p.onApply(false));
  const restitch = el("button", {
    textContent: "Re-stitch",
    disabled: busy,
    title: "Stitch again from the photos, leaving out the ones turned off (slow)",
  });
  restitch.dataset.focus = "restitch";
  restitch.addEventListener("click", () => p.onApply(true));
  const veilBox = el("input", { type: "checkbox", checked: p.showDisagree, disabled: !p.disagree });
  veilBox.dataset.focus = "disagree";
  veilBox.addEventListener("change", () => p.onShowDisagree(veilBox.checked));
  const n = p.frames.frames.length;
  const dropped = p.frames.frames.filter((f) => f.dropped && !p.pending.has(f.name)).length;
  const bar = el(
    "div",
    { className: "bar" },
    el("label", {}, veilBox, " Show where the photos disagree"),
    reblend,
    restitch,
    el("span", {
      textContent:
        `${p.pending.size} of ${n} photos off` +
        (dropped ? ` · ${dropped} the stitch could not place` : ""),
    }),
  );
  return el("div", { className: "curate" }, stage, here, bar, strip);
}

export const LAYERS = [
  { key: "actual", label: "Horizon" },
  { key: "planning", label: "Planning horizon" },
  { key: "fiducials", label: "Telescope columns" },
  { key: "grid", label: "Altitude grid" },
] as const;
export type Layer = (typeof LAYERS)[number]["key"];

// The overlays are drawn from the sidecar's disc pixels as given: the projection
// is polar.disc_xy's, never re-derived here. The actual horizon is not among
// them: it is an outline raster (the sidecar's outline.png), not a line.
function overlays(d: Disc, on: Set<Layer>): SVGElement {
  const g = svg("g");
  if (on.has("planning") && d.planning.length)
    g.append(svg("polygon", { class: "pl", points: points(d.planning) }));
  if (on.has("fiducials"))
    for (const f of d.fiducials) {
      const [x, y] = f.xy;
      const tip = svg("title");
      tip.textContent = `az ${f.az.toFixed(0)}: telescope edge ${f.bound ? "at least " : ""}${f.alt.toFixed(1)}°`;
      // A ceiling-bound column is "at least this high": an upward chevron, not a dot.
      g.append(
        f.bound
          ? svg(
              "path",
              { class: "bnd", d: `M${x - 12} ${y + 8} L${x} ${y - 6} L${x + 12} ${y + 8}` },
              tip,
            )
          : svg("circle", { class: "edg", cx: x, cy: y, r: 9 }, tip),
      );
    }
  return svg("svg", { viewBox: `0 0 ${d.size} ${d.size}`, "aria-hidden": "true" }, g);
}

function grid(d: Disc): SVGElement {
  const g = svg("g", { class: "grid" });
  for (const r of d.rings) {
    g.append(svg("circle", { cx: d.centre, cy: d.centre, r: r.r }));
    const t = svg("text", { x: d.centre + 6, y: d.centre - r.r - 6 });
    t.textContent = String(r.alt);
    g.append(t);
  }
  for (const c of d.cardinals) {
    const t = svg("text", { class: "cd", x: c.xy[0], y: c.xy[1] });
    t.textContent = c.label;
    g.append(t);
  }
  return svg("svg", { viewBox: `0 0 ${d.size} ${d.size}`, "aria-hidden": "true" }, g);
}

export interface DiscProps {
  horizon: Horizon;
  disc: Disc;
  photo: string | null;
  outline: string | null; // the actual horizon, as a disc raster (blob: URL)
  spin: number;
  layers: Set<Layer>;
  onToggle(layer: Layer): void;
  onSpinPreview(deg: number): void;
  onSpin(deg: number): void;
}

export function horizonView(p: DiscProps): HTMLElement {
  const { horizon: h, disc: d } = p;
  // An unoriented mask is in the panorama's own azimuth: the user spins the
  // photo AND its lines together (one rotation), while N/E/S/W stay put.
  const spinning = !h.oriented;
  const rotor = el("div", { className: "rotor" });
  rotor.style.transform = spinning ? `rotate(${p.spin}deg)` : "";
  if (p.photo)
    rotor.append(el("img", { src: p.photo, alt: "The site's photo, reprojected onto the sky" }));
  // The outline turns with the photo: both come from the same reprojection.
  if (p.outline && p.layers.has("actual"))
    rotor.append(el("img", { src: p.outline, alt: "", className: "outline" }));
  rotor.append(overlays(d, p.layers));
  const stage = el("div", { className: "disc" }, rotor);
  if (p.layers.has("grid")) stage.append(grid(d));
  if (spinning) {
    // Drag the disc to turn it; the slider below is the keyboard path.
    let from: [number, number] | null = null;
    let spin = p.spin;
    const centre = () => {
      const r = stage.getBoundingClientRect();
      return [r.left + r.width / 2, r.top + r.height / 2] as const;
    };
    stage.addEventListener("pointerdown", (e) => {
      e.preventDefault(); // a drag turns the disc; it must not select or drag the images
      from = [e.clientX, e.clientY];
      stage.setPointerCapture?.(e.pointerId);
    });
    stage.addEventListener("pointermove", (e) => {
      if (!from) return;
      const [cx, cy] = centre();
      spin = dragSpin(spin, cx, cy, from, [e.clientX, e.clientY]);
      from = [e.clientX, e.clientY];
      p.onSpinPreview(spin);
    });
    // pointercancel (the browser took over the touch) ends the drag like a release.
    const end = () => {
      if (from) p.onSpin(spin);
      from = null;
    };
    stage.addEventListener("pointerup", end);
    stage.addEventListener("pointercancel", end);
  }

  const present: Record<Layer, boolean> = {
    actual: p.outline !== null,
    planning: d.planning.length > 0,
    fiducials: d.fiducials.length > 0,
    grid: true,
  };
  const toggles = LAYERS.filter((l) => present[l.key]).map((l) => {
    const b = el("button", { textContent: l.label, className: `layer ${l.key}` });
    b.setAttribute("aria-pressed", String(p.layers.has(l.key)));
    b.dataset.focus = `layer:${l.key}`;
    b.addEventListener("click", () => p.onToggle(l.key));
    return b;
  });

  const parts: Node[] = [stage, el("div", { className: "bar" }, ...toggles)];
  if (h.backend?.startsWith("heuristic"))
    parts.push(
      el("p", {
        className: "note",
        textContent: "Read by colour, not by segmentation: check the line against the photo.",
      }),
    );
  if (spinning) {
    const slider = el("input", { type: "range", min: "0", max: "359", step: "1" });
    slider.value = String(Math.round(p.spin));
    slider.setAttribute("aria-label", "Rough north: turn the disc until north is up");
    slider.dataset.focus = "spin";
    slider.addEventListener("input", () => p.onSpinPreview(Number(slider.value)));
    slider.addEventListener("change", () => p.onSpin(Number(slider.value)));
    parts.push(
      el("p", {
        className: "note",
        textContent:
          "UNORIENTED: this horizon is in the panorama's own azimuth, not true north. " +
          "Drag the disc or use the slider until north is up. That is a rough guess; " +
          "the telescope fit (Orient) is what pins it.",
      }),
      el(
        "label",
        {},
        "Rough north ",
        slider,
        el("span", { className: "spin-value", textContent: ` ${Math.round(p.spin)}°` }),
      ),
    );
  } else if (h.solution) {
    const s = h.solution;
    parts.push(
      el("p", {
        className: "note",
        textContent: `Oriented: yaw ${s.yaw.toFixed(2)}°, pitch ${s.pitch.toFixed(2)}°, tilt ${s.tilt_mag.toFixed(2)}° toward ${s.tilt_dir.toFixed(0)}°.`,
      }),
    );
  }
  return el("div", {}, ...parts);
}

function fitRow(f: FitColumn): HTMLTableRowElement {
  const kind = f.bound ? "at least (ceiling)" : f.alt !== undefined ? "edge" : "—";
  const status = f.used ? "used" : f.excluded_by === "mask" ? "excluded" : "not used";
  const note = f.used
    ? f.residual !== undefined
      ? `${f.residual >= 0 ? "+" : ""}${f.residual.toFixed(2)}°`
      : "—"
    : (f.reason ?? "");
  const tr = el(
    "tr",
    {},
    ...[f.az.toFixed(0), f.alt !== undefined ? f.alt.toFixed(1) : "—", kind, status, note].map(
      (c) => el("td", { textContent: c }),
    ),
  );
  if (!f.used) tr.className = "unused";
  return tr;
}

export function fitView(h: Horizon | null): HTMLElement {
  if (!h || h.fit.length === 0)
    return el("p", {
      textContent:
        "No telescope fit for this site yet. The table appears once the telescope has measured columns (Orient).",
    });
  const used = h.fit.filter((f) => f.used).length;
  const parts: Node[] = [];
  if (h.settled === false) {
    const warn = el("p", {
      className: "banner warn",
      textContent:
        "This fit did not settle: the yaw was still moving when the run stopped, so the orientation is provisional.",
    });
    warn.setAttribute("role", "alert");
    parts.push(warn);
  }
  const head = el(
    "tr",
    {},
    ...["az", "alt", "kind", "in fit", "residual / reason"].map((c) =>
      el("th", { textContent: c }),
    ),
  );
  parts.push(
    el("p", { textContent: `${used} of ${h.fit.length} telescope columns used by the fit.` }),
    el(
      "table",
      { className: "fit" },
      el("thead", {}, head),
      el("tbody", {}, ...[...h.fit].sort((a, b) => a.az - b.az).map(fitRow)),
    ),
  );
  return el("div", {}, ...parts);
}

export interface ColumnsProps {
  cols: Columns;
  selected: number | null; // az of the column whose frames are shown
  frames: Map<string, string>; // "az/name" -> blob: URL
  busy: string;
  onSelect(az: number): void;
  onInclude(az: number, on: boolean): void;
  onTag(az: number, tag: ColumnTag, on: boolean): void;
  onFitAll(): void;
  onApply(): void;
  clicks: Clicks | null; // an edge being clicked in one frame
  onFrameClick(az: number, name: string, pt: [number, number]): void;
}

export interface Clicks {
  az: number;
  name: string;
  pts: [number, number][];
}

const CLICK_STEPS = [
  "Click a point on the edge in a frame.",
  "Click a second point on the edge.",
  "Click once in the sky.",
];

const TAGS: ColumnTag[] = ["false edge", "pocket", "near object"];
const signed = (n: number) => `${n >= 0 ? "+" : ""}${n.toFixed(2)}°`;

// The telescope columns, editable: include or exclude, tag, and see each one's
// frames. Every change refits in the engine; the numbers here are its answer.
export function columnsView(p: ColumnsProps): HTMLElement {
  const fit = p.cols.fit;
  const all = waitable(el("button", { textContent: "Fit all columns" }), p.busy, p.onFitAll);
  all.dataset.focus = "fit-all";
  const head = fit
    ? el(
        "p",
        { className: "solution" },
        `yaw ${fit.solution.yaw.toFixed(2)}°${fit.yaw_pm != null ? ` ± ${fit.yaw_pm.toFixed(0)}°` : " (not bounded)"}` +
          ` · pitch ${fit.solution.pitch.toFixed(2)}° · tilt ${fit.solution.tilt_mag.toFixed(2)}° toward ${fit.solution.tilt_dir.toFixed(0)}°`,
      )
    : el("p", { textContent: "Not fitted yet." });
  const rows = [...p.cols.columns].sort((a, b) => a.az - b.az).map((c) => columnRow(c, p));
  const table = el(
    "table",
    { className: "fit columns" },
    el(
      "thead",
      {},
      el(
        "tr",
        {},
        ...["az", "measured", "how", "residual", "in fit", "tags", ""].map((h) =>
          el("th", { textContent: h }),
        ),
      ),
    ),
    el("tbody", {}, ...rows),
  );
  if (fit) {
    const m = fit.summary;
    const f = (n: number | null | undefined) => (n == null ? "—" : `${n.toFixed(2)}°`);
    table.append(
      el(
        "tfoot",
        {},
        el(
          "tr",
          {},
          el("td", {
            colSpan: 7,
            textContent: `${m.n} fitted · rms ${f(m.rms)} · median ${f(m.median)} · max ${f(m.max)} · ${m.within_2} within 2°`,
          }),
        ),
      ),
    );
  }
  const apply = waitable(
    el("button", {
      textContent: "Apply this orientation",
      disabled: !fit,
      title: fit ? "Re-read the site's horizon with this rotation" : "Fit the columns first",
    }),
    p.busy,
    p.onApply,
  );
  apply.dataset.focus = "apply-orientation";
  const parts: Node[] = [el("div", { className: "row" }, head, all, apply), table];
  if (p.busy) parts.push(el("p", { className: "busy", textContent: p.busy }));
  const sel = p.cols.columns.find((c) => c.az === p.selected);
  if (sel) parts.push(strip(sel, p));
  if (p.cols.note) parts.push(el("p", { className: "note", textContent: p.cols.note }));
  return el("div", { className: "columns-view" }, ...parts);
}

function columnRow(c: TelescopeColumn, p: ColumnsProps): HTMLTableRowElement {
  const inc = el("input", { type: "checkbox", checked: c.included, disabled: !c.usable });
  inc.setAttribute("aria-label", `Use az ${c.az} in the fit`);
  if (!c.usable) inc.title = c.note;
  if (p.busy) inc.setAttribute("aria-disabled", "true");
  inc.dataset.focus = `include:${c.az}`;
  inc.addEventListener("change", () => {
    if (p.busy)
      inc.checked = c.included; // waiting on the engine: undo the tick
    else p.onInclude(c.az, inc.checked);
  });
  const tags = el(
    "span",
    { className: "tags" },
    ...TAGS.map((t) => {
      const on = c.tags.includes(t);
      const b = waitable(
        el("button", { textContent: t, className: on ? "tag on" : "tag" }),
        p.busy,
        () => p.onTag(c.az, t, !on),
      );
      b.setAttribute("aria-pressed", String(on));
      b.dataset.focus = `tag:${c.az}:${t}`;
      return b;
    }),
  );
  const look = el("button", {
    textContent: c.frames.length ? `${c.frames.length} frames` : "no frames",
    disabled: !c.frames.length,
  });
  look.addEventListener("click", () => p.onSelect(c.az));
  look.dataset.focus = `frames:${c.az}`;
  const tr = el(
    "tr",
    {
      className: [c.included ? "" : "unused", c.az === p.selected ? "selected" : ""]
        .join(" ")
        .trim(),
    },
    el("td", { textContent: c.az.toFixed(0) }),
    el("td", { textContent: c.alt == null ? "—" : `${c.bound ? "≥ " : ""}${c.alt.toFixed(2)}°` }),
    el("td", { textContent: c.method }),
    el("td", { textContent: c.residual == null ? "—" : signed(c.residual) }),
    el("td", {}, inc),
    el("td", {}, tags),
    el("td", {}, look),
  );
  return tr;
}

// A column's frames from the top down, each marked where the measured edge and
// the fit's prediction fall.
function strip(c: TelescopeColumn, p: ColumnsProps): HTMLElement {
  const frames = p.frames;
  const sorted = [...c.frames].sort((a, b) => b.alt - a.alt);
  const step = sorted.length > 1 ? Math.abs(sorted[0].alt - sorted[1].alt) : 0.25;
  const predicted = c.residual == null || c.alt == null ? null : c.alt + c.residual;
  const near = (alt: number, x: number | null) => x != null && Math.abs(alt - x) <= step / 2;
  const tiles = sorted.map((f) => {
    const url = frames.get(`${c.az}/${f.name}`);
    const mine = p.clicks && p.clicks.az === c.az && p.clicks.name === f.name ? p.clicks.pts : [];
    const marks = [near(f.alt, c.alt) ? "measured" : "", near(f.alt, predicted) ? "predicted" : ""]
      .filter(Boolean)
      .join(" · ");
    return el(
      "figure",
      { className: marks ? "frame marked" : "frame" },
      url ? clickable(url, c.az, f, mine, p) : el("div", { className: "loading" }),
      el("figcaption", {
        textContent: `${f.alt.toFixed(2)}° · ${Math.round(f.sky * 100)}% sky${marks ? ` · ${marks}` : ""}`,
      }),
    );
  });
  return el(
    "section",
    { className: "column-strip" },
    el("h2", {
      textContent:
        `az ${c.az.toFixed(0)}: ` +
        (c.alt == null ? `no measurement (${c.note})` : `measured ${c.alt.toFixed(2)}°`) +
        (predicted == null ? "" : `, predicted ${predicted.toFixed(2)}°`),
    }),
    el("p", {
      className: "hint",
      textContent:
        CLICK_STEPS[Math.min(p.clicks?.pts.length ?? 0, 2)] +
        " The edge's altitude comes from how much of the frame lies on the sky side.",
    }),
    el("div", { className: "tiles" }, ...tiles),
  );
}

// A frame that takes clicks: points in 0-1 fractions of the image, shown as dots.
function clickable(
  url: string,
  az: number,
  f: { alt: number; name: string },
  pts: [number, number][],
  p: ColumnsProps,
): HTMLElement {
  const img = el("img", { src: url, alt: `az ${az} alt ${f.alt}` });
  img.addEventListener("click", (e) => {
    const r = img.getBoundingClientRect();
    if (!r.width || !r.height || p.busy) return;
    const x = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
    const y = Math.min(1, Math.max(0, (e.clientY - r.top) / r.height));
    p.onFrameClick(az, f.name, [x, y]);
  });
  const dots = pts.map(([x, y], i) => {
    const d = el("span", { className: i < 2 ? "dot edge" : "dot sky" });
    d.style.left = `${(x * 100).toFixed(1)}%`;
    d.style.top = `${(y * 100).toFixed(1)}%`;
    return d;
  });
  return el("div", { className: "clickable" }, img, ...dots);
}
