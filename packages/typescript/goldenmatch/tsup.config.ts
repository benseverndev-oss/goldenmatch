import { defineConfig } from "tsup";

export default defineConfig({
  entry: {
    index: "src/index.ts",
    "core/index": "src/core/index.ts",
    // Opt-in entry: the shared autoconfig wasm core. Carries the inlined wasm
    // (~1.7 MB base64), so it's a separate subpath — consumers pay that cost
    // only when they import `goldenmatch/core/autoconfig-wasm`.
    "core/autoconfigWasm": "src/core/autoconfigWasm.ts",
    // Opt-in entry: the suggest-wasm healer core. Carries the inlined wasm
    // (~220 KB base64), so it's a separate subpath — consumers pay that cost
    // only when they import `goldenmatch/core/suggest-wasm`.
    "core/suggestWasm": "src/core/suggestWasm.ts",
    "core/perceptualWasm": "src/core/perceptualWasm.ts",
    "node/index": "src/node/index.ts",
    "node/mcp/server": "src/node/mcp/server.ts",
    cli: "src/cli.ts",
    // Separate entry so piscina can load it at runtime from disk.
    "node/backends/score-worker": "src/node/backends/score-worker.ts",
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
  loader: { ".wasm": "copy" },
  onSuccess: "node scripts/copy_wasm_artifact.mjs",
  // Inline the tiny internal WASM plumbing (loader / enable-skeleton / registry)
  // so it is NOT a published runtime dep — it is not on npm and consumers never
  // import it directly. This bundles ONLY the plumbing; the wasm-bindgen glue
  // (score_wasm.js) and the .wasm artifact stay external (see `external`).
  noExternal: ["goldenmatch-wasm-runtime"],
  external: [
    // The opt-in WASM glue is loaded at RUNTIME (dynamic import inside
    // enableWasm) and is absent in a default checkout. Mark it external so
    // esbuild never tries to resolve `./artifacts/score_wasm.js` at build time
    // (that would warn on every normal build); it stays a runtime sibling load.
    /score_wasm\.js$/,
    "hnswlib-node",
    "@huggingface/transformers",
    "piscina",
    "ink",
    "ink-table",
    "ink-select-input",
    "ink-text-input",
    "ink-spinner",
    "ink-gradient",
    "react",
    "pg",
    "@duckdb/node-api",
    "snowflake-sdk",
    "@google-cloud/bigquery",
    "@databricks/sql",
    "yaml",
  ],
});
