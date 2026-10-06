import type { AppState, Disc, FitColumn, Horizon, Job } from "./api/types";
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

const points = (xy: number[][]) => xy.map(([x, y]) => `${x},${y}`).join(" ");

export function jobPanel(job: Job): HTMLElement {
  const steps = {
    mosaic: "Stitching the photos into a panorama",
    skymask: "Reading the horizon off it",
  };
  const head =
    job.status === "running"
      ? `${job.step ? steps[job.step] : "Starting"}…`
      : job.status === "failed"
        ? `Building this site failed: ${job.error ?? "no reason given"}`
        : "Built.";
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
  pick.addEventListener("click", onPick);
  return el(
    "div",
    { className: "drop" },
    el("p", { textContent: "Drop the photographs of a site here, or" }),
    pick,
  );
}

export function panoramaView(st: AppState, pano: string | null, onPick: () => void): HTMLElement {
  const parts: Node[] = [];
  if (st.job && st.site && st.job.site === st.site.slug && st.job.status !== "done")
    parts.push(jobPanel(st.job));
  if (pano)
    parts.push(
      el("img", {
        className: "pano",
        src: pano,
        alt: "The panorama stitched from this site's photos",
      }),
    );
  else if (!st.site) parts.push(dropZone(onPick));
  else if (!st.job) parts.push(el("p", { textContent: "This site has no panorama yet." }));
  return el("div", {}, ...parts);
}

export const LAYERS = [
  { key: "actual", label: "Horizon" },
  { key: "planning", label: "Planning horizon" },
  { key: "pockets", label: "Sky pockets" },
  { key: "fiducials", label: "Telescope columns" },
  { key: "grid", label: "Altitude grid" },
] as const;
export type Layer = (typeof LAYERS)[number]["key"];

// The overlays are drawn from the sidecar's disc pixels as given: the projection
// is polar.disc_xy's, never re-derived here.
function overlays(d: Disc, on: Set<Layer>): SVGElement {
  const g = svg("g");
  if (on.has("planning") && d.planning.length)
    g.append(svg("polygon", { class: "pl", points: points(d.planning) }));
  if (on.has("pockets"))
    for (const [[x1, y1], [x2, y2]] of d.pockets)
      g.append(svg("line", { class: "pk", x1, y1, x2, y2 }));
  if (on.has("actual") && d.actual.length)
    g.append(svg("polygon", { class: "hz", points: points(d.actual) }));
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
      from = [e.clientX, e.clientY];
      stage.setPointerCapture(e.pointerId);
    });
    stage.addEventListener("pointermove", (e) => {
      if (!from) return;
      const [cx, cy] = centre();
      spin = dragSpin(spin, cx, cy, from, [e.clientX, e.clientY]);
      from = [e.clientX, e.clientY];
      p.onSpinPreview(spin);
    });
    stage.addEventListener("pointerup", () => {
      if (from) p.onSpin(spin);
      from = null;
    });
  }

  const present: Record<Layer, boolean> = {
    actual: d.actual.length > 0,
    planning: d.planning.length > 0,
    pockets: d.pockets.length > 0,
    fiducials: d.fiducials.length > 0,
    grid: true,
  };
  const toggles = LAYERS.filter((l) => present[l.key]).map((l) => {
    const b = el("button", { textContent: l.label, className: `layer ${l.key}` });
    b.setAttribute("aria-pressed", String(p.layers.has(l.key)));
    b.addEventListener("click", () => p.onToggle(l.key));
    return b;
  });

  const parts: Node[] = [stage, el("div", { className: "bar" }, ...toggles)];
  if (spinning) {
    const slider = el("input", { type: "range", min: "0", max: "359", step: "1" });
    slider.value = String(Math.round(p.spin));
    slider.setAttribute("aria-label", "Rough north: turn the disc until north is up");
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
      el("label", {}, "Rough north ", slider, ` ${Math.round(p.spin)}°`),
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
