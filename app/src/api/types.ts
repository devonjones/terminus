// The sidecar's /state payload (src/terminus/server/__init__.py, Sidecar.state).
export interface AppState {
  version: string;
  site: string | null;
  tab: string;
  tabs: string[];
  scope: { link: string };
  sun_mode: "sun" | "shade";
}

// What the preload exposes on window.terminus. Keep it narrow: named calls only.
export interface TerminusApi {
  getState(): Promise<AppState>;
  setTab(tab: string): Promise<AppState>;
}

declare global {
  interface Window {
    terminus: TerminusApi;
  }
}
