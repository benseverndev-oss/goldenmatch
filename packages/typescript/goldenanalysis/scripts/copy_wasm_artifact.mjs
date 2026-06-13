// Copy the built WASM artifact from src into the dist locations the bundled
// loader might resolve `new URL('./artifacts/analysis_wasm_bg.wasm', import.meta.url)`
// to. tsup bundling can land the loader code at dist/core/index.js, a
// dist/core/wasm/ module, or a hoisted chunk — and `import.meta.url` then points
// at whichever, so `./artifacts/` resolves to a DIFFERENT parent in each case.
// Copying to every plausible `./artifacts/` parent (a few KB, harmless) makes
// enableAnalysisWasm() resolve the artifact in the published package regardless
// of how tsup bundles. No-op (warns) when the artifact is absent.
import { cp, mkdir, access } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const src = join(here, "..", "src", "core", "wasm", "artifacts");
const files = ["analysis_wasm_bg.wasm", "analysis_wasm.js"];
const dsts = [
  join(here, "..", "dist", "core", "wasm", "artifacts"),
  join(here, "..", "dist", "core", "artifacts"),
  join(here, "..", "dist", "artifacts"),
];

try {
  await access(join(src, files[0]));
} catch {
  console.warn("[copy_wasm_artifact] no WASM artifact in src — skipping (pure-TS default).");
  process.exit(0);
}
for (const dst of dsts) {
  await mkdir(dst, { recursive: true });
  for (const f of files) await cp(join(src, f), join(dst, f));
}
console.log("[copy_wasm_artifact] copied", files.join(", "), "to", dsts.length, "dist locations");
