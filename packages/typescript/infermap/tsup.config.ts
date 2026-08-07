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
  // `resolve` must name the PACKAGE, not the subpath specifier — it rolls the
  // referenced .d.ts into ours so the published types don't point at
  // `goldenmatch`, which is only a devDependency here.
  dts: { resolve: ["goldenmatch-wasm-runtime", "goldenmatch"] },
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
  // Inline the tiny WASM plumbing so it's not a published runtime dep. Same for
  // the goldenmatch edit-distance leaf (`goldenmatch/core/string-distance`):
  // infermap single-sources those primitives rather than vendoring a second
  // copy, but goldenmatch stays a devDependency — the leaf has zero imports, so
  // inlining it costs ~150 lines, not the goldenmatch package.
  noExternal: ["goldenmatch-wasm-runtime", "goldenmatch"],
  external: [
    // Runtime-only wasm-bindgen glue (dynamic-imported in enableInfermapWasm);
    // absent in a default checkout. Mark external so esbuild never resolves it.
    /infermap_wasm\.js$/,
  ],
});
