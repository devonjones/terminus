import { describe, expect, it } from "vitest";
import { bearing, dragSpin } from "./spin";

// The disc's convention (polar.py): north up, east right, azimuth clockwise.
describe("bearing", () => {
  it.each([
    [100, 0, 0], // straight up: north
    [200, 100, 90], // right: east
    [100, 200, 180], // down: south
    [0, 100, 270], // left: west
  ])("point (%i, %i) from centre (100, 100) is %i deg", (x, y, want) => {
    expect(bearing(100, 100, x, y)).toBeCloseTo(want, 9);
  });
});

describe("dragSpin", () => {
  it("a clockwise quarter turn adds 90", () => {
    expect(dragSpin(10, 100, 100, [100, 0], [200, 100])).toBeCloseTo(100, 9);
  });
  it("an anticlockwise drag subtracts, wrapping below zero", () => {
    expect(dragSpin(10, 100, 100, [200, 100], [100, 0])).toBeCloseTo(280, 9);
  });
  it("wraps past 360", () => {
    expect(dragSpin(350, 100, 100, [100, 0], [200, 100])).toBeCloseTo(80, 9);
  });
});
