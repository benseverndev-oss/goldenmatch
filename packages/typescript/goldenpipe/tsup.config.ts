import { defineConfig } from "tsup";

export default defineConfig({
  entry: {
    index: "src/index.ts",
    "core/index": "src/core/index.ts",
    "node/index": "src/node/index.ts",
    "node/mcp/server": "src/node/mcp/server.ts",
    "node/a2a/server": "src/node/a2a/server.ts",
    cli: "src/cli.ts",
  },
  format: ["esm", "cjs"],
  // resolve: roll the bundled goldenmatch-wasm-runtime types up INTO our .d.ts
  // (noExternal inlines the JS, but tsup keeps a bare `import ... from
  // 'goldenmatch-wasm-runtime'` in the dts otherwise — unresolvable for a
  // downstream TS consumer now that the package is a devDependency).
  dts: { resolve: ["goldenmatch-wasm-runtime"] },
  sourcemap: true,
  clean: true,
  target: "node20",
  splitting: false,
  treeshake: true,
  // Copy the opt-in WASM artifact (built by goldenpipe-wasm/build_wasm.sh) into
  // dist so the loader's `new URL('./artifacts/goldenpipe_wasm_bg.wasm',
  // import.meta.url)` resolves at runtime. Absent in a default checkout —
  // enableWasm() then returns false and pure-TS is used.
  loader: { ".wasm": "copy" },
  publicDir: false,
  onSuccess: "node scripts/copy_wasm_artifact.mjs",
  // Inline the tiny internal WASM plumbing (loader / enable-skeleton / registry)
  // so it is NOT a published runtime dep — it is not on npm and consumers never
  // import it directly. This bundles ONLY the plumbing; the wasm-bindgen glue
  // (goldenpipe_wasm.js) and the .wasm artifact stay external (see `external`).
  noExternal: ["goldenmatch-wasm-runtime"],
  external: [
    // The wasm-bindgen glue is loaded at RUNTIME (dynamic import inside
    // enableWasm) and is absent in a default checkout. Mark it external so
    // esbuild never tries to resolve `./artifacts/goldenpipe_wasm.js` at build
    // time (that would warn on every normal build).
    /goldenpipe_wasm\.js$/,
  ],
});
