// @vitest-environment jsdom
import { findByText, getByRole, waitFor } from "@testing-library/dom";
import { describe, expect, it, vi } from "vitest";
import type { AppState, TerminusApi } from "./api/types";
import { mount } from "./renderer";

const state: AppState = {
  version: "0.1.0",
  site: null,
  tab: "connect",
  tabs: ["connect", "panorama", "horizon"],
  scope: { link: "none" },
  sun_mode: "sun",
};

function fakeApi(over: Partial<TerminusApi> = {}): TerminusApi {
  return {
    getState: vi.fn(async () => state),
    setTab: vi.fn(async (tab: string) => ({ ...state, tab })),
    ...over,
  };
}

describe("mount", () => {
  it("renders the tabs the sidecar lists, the active one selected, and a status line", async () => {
    const root = document.createElement("div");
    await mount(root, fakeApi());
    const tabs = root.querySelectorAll('[role="tab"]');
    expect([...tabs].map((t) => t.textContent)).toEqual(["Connect", "Panorama", "Horizon"]);
    expect(getByRole(root, "tab", { selected: true }).textContent).toBe("Connect");
    expect(root.querySelector("footer")!.textContent).toBe(
      "engine 0.1.0 · site: none · scope: none · sun mode: SUN",
    );
  });

  it("asks the sidecar to change tab and renders what it returns", async () => {
    const root = document.createElement("div");
    const api = fakeApi();
    await mount(root, api);
    getByRole(root, "tab", { name: "Horizon" }).click();
    expect(api.setTab).toHaveBeenCalledWith("horizon");
    await waitFor(() => expect(root.querySelector("h1")!.textContent).toBe("Horizon"));
    expect(getByRole(root, "tab", { selected: true }).textContent).toBe("Horizon");
  });

  it("keeps keyboard focus on the tab bar after a tab change", async () => {
    const root = document.body.appendChild(document.createElement("div"));
    await mount(root, fakeApi());
    const tab = getByRole(root, "tab", { name: "Horizon" });
    tab.focus();
    tab.click();
    await waitFor(() => expect(root.querySelector("h1")!.textContent).toBe("Horizon"));
    expect(document.activeElement).toBe(getByRole(root, "tab", { selected: true }));
    root.remove();
  });

  it("shows a refused tab change instead of dropping it", async () => {
    const root = document.createElement("div");
    document.body.appendChild(root);
    await mount(root, fakeApi({ setTab: async () => Promise.reject(new Error("engine down")) }));
    getByRole(root, "tab", { name: "Panorama" }).click();
    expect((await findByText(root, "engine down")).getAttribute("role")).toBe("alert");
    expect(getByRole(root, "tab", { selected: true }).textContent).toBe("Connect");
    expect(document.activeElement).toBe(getByRole(root, "tab", { selected: true }));
  });
});
