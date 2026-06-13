import { defineConfig } from "tsup";

export default defineConfig({
  entry: {
    index: "src/index.ts",
    "core/index": "src/core/index.ts",
    "node/index": "src/node/index.ts",
    cli: "src/cli.ts",
  },
  format: ["esm", "cjs"],
  dts: true,
  sourcemap: true,
  clean: true,
  target: "node20",
  splitting: false,
  treeshake: true,
  // Copy the opt-in WASM artifact (built by analysis-wasm/build_wasm.sh) into
  // dist so the loader's `new URL('./artifacts/analysis_wasm_bg.wasm',
  // import.meta.url)` resolves at runtime. Absent in a default checkout —
  // enableAnalysisWasm() then returns false and pure-TS is used.
  loader: { ".wasm": "copy" },
  publicDir: false,
  onSuccess: "node scripts/copy_wasm_artifact.mjs",
  external: [
    // The wasm-bindgen glue is loaded at RUNTIME (dynamic import inside
    // enableAnalysisWasm) and is absent in a default checkout. Mark it external
    // so esbuild never tries to resolve `./artifacts/analysis_wasm.js` at build
    // time (that would warn on every normal build).
    /analysis_wasm\.js$/,
  ],
});
