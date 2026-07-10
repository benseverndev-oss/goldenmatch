import { defineConfig } from "tsup";

export default defineConfig({
  entry: {
    index: "src/index.ts",
    "core/index": "src/core/index.ts",
    "node/index": "src/node/index.ts",
    "node/mcp/server": "src/node/mcp/server.ts",
    cli: "src/cli.ts",
  },
  format: ["esm", "cjs"],
  dts: { resolve: ["goldenmatch-wasm-runtime"] },
  sourcemap: true,
  clean: true,
  target: "node20",
  splitting: false,
  treeshake: true,
  // Copy the opt-in WASM artifact into dist so the loader's
  // new URL('./artifacts/infermap_wasm_bg.wasm', import.meta.url) resolves at
  // runtime. Absent in a default checkout -> enableInfermapWasm() returns false.
  loader: { ".wasm": "copy" },
  publicDir: false,
  onSuccess: "node scripts/copy_wasm_artifact.mjs",
  // Inline the tiny WASM plumbing so it's not a published runtime dep.
  noExternal: ["goldenmatch-wasm-runtime"],
  external: [
    // Runtime-only wasm-bindgen glue (dynamic-imported in enableInfermapWasm);
    // absent in a default checkout. Mark external so esbuild never resolves it.
    /infermap_wasm\.js$/,
  ],
});
