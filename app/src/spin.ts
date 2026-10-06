// The disc is drawn N up, E right, azimuth increasing clockwise (as polar.py).
// A pointer's bearing from the centre in that same convention: 0 = up, 90 = right.
export function bearing(cx: number, cy: number, x: number, y: number): number {
  return ((Math.atan2(x - cx, cy - y) * 180) / Math.PI + 360) % 360;
}

// Dragging the disc from one point to another turns the spin by the change in
// bearing: clockwise drags increase it. Result in [0, 360).
export function dragSpin(
  spin: number,
  cx: number,
  cy: number,
  from: [number, number],
  to: [number, number],
): number {
  const turn = bearing(cx, cy, to[0], to[1]) - bearing(cx, cy, from[0], from[1]);
  return (((spin + turn) % 360) + 360) % 360;
}
