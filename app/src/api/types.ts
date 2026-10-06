// The sidecar's payloads are generated from src/terminus/server/schema.json
// (`npm run gen:api`); only the preload's own API is written here.
import type { AppState, Disc, Horizon, SiteList } from "./generated";

export type * from "./generated";

export type ImageName = "panorama" | "disc";

// What the preload exposes on window.terminus. Keep it narrow: named calls only.
export interface TerminusApi {
  getState(): Promise<AppState>;
  setTab(tab: string): Promise<AppState>;
  listSites(): Promise<SiteList>;
  openSite(slug: string): Promise<AppState>;
  // Shows the file dialog; null if the user cancelled.
  pickPhotos(): Promise<AppState | null>;
  // Photos dropped on the window.
  createSite(files: File[]): Promise<AppState>;
  setSpin(deg: number): Promise<AppState>;
  // null: the site has nothing to show here yet.
  horizon(): Promise<Horizon | null>;
  disc(): Promise<Disc | null>;
  image(name: ImageName): Promise<Uint8Array | null>;
}

declare global {
  interface Window {
    terminus: TerminusApi;
  }
}
