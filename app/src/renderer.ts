import type { AppState, TerminusApi } from "./api/types";

const title = (s: string) => s[0].toUpperCase() + s.slice(1);

function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  props: Partial<HTMLElementTagNameMap[K]> = {},
  ...children: (Node | string)[]
): HTMLElementTagNameMap[K] {
  const e = Object.assign(document.createElement(tag), props);
  e.append(...children);
  return e;
}

// Render the tab bar, the (empty for now) active tab and a status line.
export async function mount(root: HTMLElement, api: TerminusApi): Promise<void> {
  let error = "";
  const render = (s: AppState, keepFocus = false) => {
    const tabs = s.tabs.map((t) => {
      const b = el("button", { textContent: title(t) });
      b.setAttribute("role", "tab");
      b.setAttribute("aria-selected", String(t === s.tab));
      b.addEventListener("click", () => {
        api.setTab(t).then(
          (next) => ((error = ""), render(next, true)),
          (e: Error) => ((error = e.message), render(s, true)),
        );
      });
      return b;
    });
    const nav = el("nav", {}, ...tabs);
    nav.setAttribute("role", "tablist");
    const panel = el("section", {}, el("h1", { textContent: title(s.tab) }), "Nothing here yet.");
    panel.setAttribute("role", "tabpanel");
    const status = el(
      "footer",
      {},
      `engine ${s.version} · site: ${s.site ?? "none"} · scope: ${s.scope.link} · sun mode: ${s.sun_mode.toUpperCase()}`,
    );
    const parts: Node[] = [nav, panel, status];
    if (error) {
      const alert = el("p", { textContent: error });
      alert.setAttribute("role", "alert");
      parts.push(alert);
    }
    root.replaceChildren(...parts);
    // Re-rendering replaced the button that had focus; give it to the selected tab.
    if (keepFocus) root.querySelector<HTMLElement>('[aria-selected="true"]')?.focus();
  };
  render(await api.getState());
}
