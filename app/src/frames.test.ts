import { describe, expect, it } from "vitest";
import { covering, pick, type Footprint } from "./frames";

// Two 4x2 photos overlapping in columns 2-3; b does not cover its own top-left pixel.
const fp = (name: string, x: number, alpha: number[]): Footprint => ({
  name,
  box: [x, 0, 4, 2],
  alpha: Uint8ClampedArray.from(alpha),
});
const a = fp("a", 0, [255, 255, 255, 255, 255, 255, 255, 255]);
const b = fp("b", 2, [0, 255, 255, 255, 255, 255, 255, 255]);

describe("which photo is under the cursor", () => {
  it("reads each photo's own mask, not its box", () => {
    expect(covering([a, b], 2.5, 0.5).map((f) => f.name)).toEqual(["a"]); // b's hole
    expect(covering([a, b], 3.5, 0.5).map((f) => f.name)).toEqual(["a", "b"]);
    expect(covering([a, b], 6, 1)).toEqual([]);
    expect(covering([a, b], -1, 0)).toEqual([]);
  });

  it("in an overlap, picks the photo whose centre is nearest", () => {
    expect(pick([a, b], 2.2, 1.5)).toBe("a"); // a's centre x=2, b's x=4
    expect(pick([a, b], 3.8, 1.5)).toBe("b");
    expect(pick([a, b], 7, 0)).toBeNull();
  });
});
