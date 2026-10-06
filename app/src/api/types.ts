// The sidecar's payloads are generated from src/terminus/server/schema.json
// (`npm run gen:api`); only the preload's own API is written here.
import type { AppState, Disc, Frames, Horizon, SiteList } from "./generated";

export type * from "./generated";

export type ImageName = "panorama" | "disc" | "disagree" | "outline" | "progress";
export type FrameImage = "footprint" | "thumb";
export type BuildImage = "layer" | "verdict";

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
  renameSite(slug: string, name: string): Promise<AppState>;
  // Removes the site and everything in it. The page asks the user first.
  deleteSite(slug: string): Promise<AppState>;
  setSpin(deg: number): Promise<AppState>;
  // null: the site has nothing to show here yet.
  horizon(): Promise<Horizon | null>;
  disc(): Promise<Disc | null>;
  image(name: ImageName): Promise<Uint8Array | null>;
  // The photos of the site and where each sits; null until it can be curated.
  frames(): Promise<Frames | null>;
  frameImage(kind: FrameImage, name: string): Promise<Uint8Array | null>;
  // A build's remapped photo, or its sky verdict, by layer file (layer0003.tif).
  buildImage(kind: BuildImage, layer: string): Promise<Uint8Array | null>;
  // Rebuild without the `off` photos: re-blend the stitched ones, or stitch afresh.
  curate(off: string[], restitch: boolean): Promise<AppState>;
}

declare global {
  interface Window {
    terminus: TerminusApi;
  }
}
