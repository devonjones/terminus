// The sidecar's payloads are generated from src/terminus/server/schema.json
// (`npm run gen:api`); only the preload's own API is written here.
import type {
  AppState,
  Columns,
  Disc,
  Frames,
  Horizon,
  ScopeFrame,
  ScopeList,
  ScopeStatus,
  SiteList,
  TelescopeColumn,
} from "./generated";

export type * from "./generated";

export type ImageName = "panorama" | "disc" | "disagree" | "outline" | "progress";
export type FrameImage = "footprint" | "thumb";
export type BuildImage = "layer" | "verdict";
export type ColumnTag = TelescopeColumn["tags"][number];
export type Pt = [number, number];

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
  // The telescope, over Alpaca. Every motion is Sun-guarded in the engine.
  discoverScopes(): Promise<ScopeList>;
  connectScope(host: string): Promise<AppState>;
  scopeStatus(): Promise<ScopeStatus>;
  parkScope(): Promise<ScopeStatus>;
  // Parks first; the link stays if the park fails.
  disconnectScope(): Promise<AppState>;
  // A Sun-guarded slew; the engine refuses a target or path near the Sun.
  pointScope(az: number, alt: number): Promise<ScopeStatus>;
  takeFrame(exposureMs: number): Promise<ScopeFrame>;
  // The last frame in colour; null before the first.
  framePreview(): Promise<Uint8Array | null>;
  // The open site's telescope columns and their fit; null when it has none.
  columns(): Promise<Columns | null>;
  // Include or exclude a column, or set its tags; the engine refits near the last fit, if any.
  editColumn(az: number, change: { included?: boolean; tags?: ColumnTag[] }): Promise<Columns>;
  // The full fit over every included column: minutes.
  fitColumns(): Promise<Columns>;
  columnFrame(az: number, name: string): Promise<Uint8Array | null>;
  // The edge clicked in a frame: two points on it and one in the sky, each [x, y]
  // in 0-1 image fractions. It becomes the column's measurement.
  clickEdge(az: number, name: string, p1: Pt, p2: Pt, sky: Pt): Promise<Columns>;
  // Make the fit the site's orientation and re-read its horizon with it (a job).
  applyOrientation(): Promise<AppState>;
}

declare global {
  interface Window {
    terminus: TerminusApi;
  }
}
