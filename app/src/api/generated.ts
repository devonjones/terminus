// Generated from src/terminus/server/schema.json by npm run gen:api. Do not edit.

/**
 * @minItems 2
 * @maxItems 2
 */
export type XY = [number, number];

/**
 * Every payload the sidecar sends. tests/test_server.py validates responses against it, and app/src/api/generated.ts is generated from it.
 */
export interface Api {
  health?: Health;
  state?: AppState;
  sites?: SiteList;
  horizon?: Horizon;
  disc?: Disc;
}
export interface Health {
  ok: true;
  version: string;
}
export interface AppState {
  version: string;
  site: SiteSummary | null;
  tab: string;
  tabs: string[];
  scope: {
    link: string;
  };
  sun_mode: "sun" | "shade";
  /**
   * rough yaw of an unoriented site, degrees
   */
  spin: number;
  job: Job | null;
  /**
   * only in /dev/state
   */
  log?: string[];
}
export interface SiteSummary {
  slug: string;
  photos: number;
  panorama: boolean;
  mask: boolean;
}
export interface Job {
  site: string;
  step: "mosaic" | "skymask" | null;
  status: "running" | "done" | "failed";
  log: string[];
  error: string | null;
}
export interface SiteList {
  sites: SiteSummary[];
}
export interface Horizon {
  mask: string;
  oriented: boolean;
  solution: Solution | null;
  columns: Column[];
  fit: FitColumn[];
  settled: boolean | null;
  /**
   * the detector that read the horizon (e.g. "heuristic": no segmentation model ran)
   */
  backend: string | null;
}
export interface Solution {
  yaw: number;
  pitch: number;
  tilt_mag: number;
  tilt_dir: number;
}
export interface Column {
  az: number;
  /**
   * the actual horizon
   */
  alt: number;
  type?: string;
  planning?: number;
  fuzz?: number;
  bound?: boolean;
  clipped?: boolean;
  pockets?: [number, number][];
}
export interface FitColumn {
  az: number;
  alt?: number;
  bound?: boolean;
  used?: boolean;
  residual?: number;
  reason?: string;
  excluded_by?: string;
}
/**
 * Overlays for the polar disc, in disc pixels (N up, E right), from polar.disc_xy.
 */
export interface Disc {
  size: number;
  floor: number;
  centre: number;
  rings: {
    alt: number;
    r: number;
  }[];
  cardinals: {
    label: string;
    xy: XY;
  }[];
  actual: XY[];
  planning: XY[];
  pockets: [XY, XY][];
  fiducials: {
    az: number;
    alt: number;
    bound: boolean;
    xy: XY;
  }[];
}
