// Bundle main, preload and renderer with esbuild; tsc only type-checks.
import { build } from "esbuild";
import { cpSync } from "node:fs";

const common = { bundle: true, sourcemap: true, logLevel: "warning" };
const node = { ...common, platform: "node", format: "cjs", external: ["electron"] };
await Promise.all([
  build({ ...node, entryPoints: ["main/main.ts"], outfile: "dist/main.js" }),
  // A sandboxed preload must be a single CommonJS file.
  build({ ...node, entryPoints: ["preload/preload.ts"], outfile: "dist/preload.js" }),
  build({ ...common, entryPoints: ["src/index.ts"], outfile: "dist/renderer.js", format: "iife" }),
]);
for (const f of ["index.html", "style.css"]) cpSync(`src/${f}`, `dist/${f}`);
