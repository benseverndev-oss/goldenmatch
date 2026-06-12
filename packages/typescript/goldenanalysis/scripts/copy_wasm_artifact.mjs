// Copy the built WASM artifact from src into dist next to the loader output.
// No-op (warns) when the artifact is absent (default checkout / no toolchain).
import { cp, mkdir, access } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const src = join(here, "..", "src", "core", "wasm", "artifacts");
const dst = join(here, "..", "dist", "core", "wasm", "artifacts");
const files = ["analysis_wasm_bg.wasm", "analysis_wasm.js"];
try {
  await access(join(src, files[0]));
} catch {
  console.warn("[copy_wasm_artifact] no WASM artifact in src — skipping (pure-TS default).");
  process.exit(0);
}
await mkdir(dst, { recursive: true });
for (const f of files) await cp(join(src, f), join(dst, f));
console.log("[copy_wasm_artifact] copied", files.join(", "), "->", dst);
