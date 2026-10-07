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
  frames?: Frames;
  scope_status?: ScopeStatus;
  scopes?: ScopeList;
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
    link: "none" | "alpaca";
    host: string | null;
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
  /**
   * the user's name for the site (the slug until renamed)
   */
  name: string;
  /**
   * when anything in the site last changed (local time, ISO 8601, minutes)
   */
  updated: string;
}
export interface Job {
  site: string;
  step: "mosaic" | "reblend" | "skymask" | "horizon" | null;
  status: "running" | "done" | "failed";
  log: string[];
  error: string | null;
  /**
   * what the current step is doing
   */
  phase: "matching" | "solving" | "placing" | "segmenting" | "blending" | "judging" | null;
  canvas: [number, number] | null;
  frames: BuildFrame[];
  /**
   * the photos being worked on right now
   */
  active: string[];
  /**
   * photo pairs found to share matches, as cpfind reports them
   */
  pairs: Pair[];
  /**
   * photo pairs compared so far
   */
  compared: number;
  /**
   * what the phase is doing right now, in words (e.g. cpfind's own stages)
   */
  detail: string | null;
  /**
   * times the horizon so far has been redrawn (fetch /site/progress.png when it changes)
   */
  outline: number;
}
/**
 * One photo's progress through a build.
 */
export interface BuildFrame {
  name: string;
  state: "listed" | "matched" | "dropped" | "placed" | "off" | "judged";
  /**
   * control points tying it to the others
   */
  points?: number;
  /**
   * the photos it shares control points with
   */
  links?: string[];
  /**
   * why the stitch dropped it
   */
  reason?: string;
  /**
   * its remapped layer in work/
   */
  layer?: string;
  /**
   * [x, y, width, height] on the panorama
   *
   * @minItems 4
   * @maxItems 4
   */
  box?: [number, number, number, number];
}
export interface Pair {
  a: string;
  b: string;
  matches: number;
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
   * the vote that read the horizon, e.g. "segment (per-frame vote)"; a value starting "heuristic" means the frames were judged by colour
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
  planning: XY[];
  fiducials: {
    az: number;
    alt: number;
    bound: boolean;
    xy: XY;
  }[];
}
/**
 * The photos of a curatable site.
 */
export interface Frames {
  width: number;
  height: number;
  frames: Frame[];
}
export interface Frame {
  name: string;
  /**
   * its remapped layer in work/; null when the last stitch left it out
   */
  layer: string | null;
  box: [number, number, number, number] | null;
  /**
   * turned off by the user
   */
  off: boolean;
  /**
   * left out by the stitch: too few control points
   */
  dropped: boolean;
}
/**
 * The linked telescope, or {link: none}. Pointing comes from RA/Dec (EQ mode's own alt/az is unreliable).
 */
export interface ScopeStatus {
  link: "none" | "alpaca";
  host?: string;
  eq?: boolean;
  az?: number;
  alt?: number;
  /**
   * arm closed (Dec -90)
   */
  stowed?: boolean;
  moving?: boolean;
  sun?: {
    az: number;
    alt: number;
  };
  /**
   * degrees from the Sun no slew may come within
   */
  cone?: number;
}
export interface ScopeList {
  scopes: {
    host: string;
    port: number;
  }[];
}
