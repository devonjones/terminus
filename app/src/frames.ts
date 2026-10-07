// Which photo is under the cursor on the panorama. Each footprint is the photo's
// own coverage mask from the sidecar, so the answer is exact, not a bounding box.

export interface Footprint {
  name: string;
  box: [number, number, number, number]; // [x, y, width, height] on the panorama, in its pixels
  alpha: Uint8ClampedArray; // box width * box height, row by row; non-zero where the photo covers
}

// The photos covering panorama pixel (x, y).
export function covering(fps: Footprint[], x: number, y: number): Footprint[] {
  return fps.filter(({ box: [bx, by, w, h], alpha }) => {
    const [i, j] = [Math.floor(x - bx), Math.floor(y - by)];
    return i >= 0 && j >= 0 && i < w && j < h && alpha[j * w + i] > 0;
  });
}

// The photo to highlight at (x, y): of those covering it, the one whose centre is
// nearest, so the pick follows the cursor across an overlap rather than sticking.
export function pick(fps: Footprint[], x: number, y: number): string | null {
  let best: string | null = null;
  let bestD = Infinity;
  for (const { name, box } of covering(fps, x, y)) {
    const d = Math.hypot(box[0] + box[2] / 2 - x, box[1] + box[3] / 2 - y);
    if (d < bestD) [best, bestD] = [name, d];
  }
  return best;
}

// A footprint PNG's alpha channel. Needs a real canvas (Electron), not jsdom.
export async function decode(
  name: string,
  box: [number, number, number, number],
  png: Uint8Array,
): Promise<Footprint> {
  const bitmap = await createImageBitmap(new Blob([png as BlobPart], { type: "image/png" }));
  const canvas = new OffscreenCanvas(bitmap.width, bitmap.height);
  const ctx = canvas.getContext("2d")!;
  ctx.drawImage(bitmap, 0, 0);
  const rgba = ctx.getImageData(0, 0, bitmap.width, bitmap.height).data;
  const alpha = new Uint8ClampedArray(bitmap.width * bitmap.height);
  for (let k = 0; k < alpha.length; k++) alpha[k] = rgba[k * 4 + 3];
  return { name, box: [box[0], box[1], bitmap.width, bitmap.height], alpha };
}
