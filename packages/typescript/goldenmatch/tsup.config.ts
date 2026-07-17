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
    // Opt-in entry: the documents-core kernels (schema/parse/prompt/normalize)
    // compiled to wasm, so JS/TS shares ONE document-ingest kernel with Python.
    // Carries the inlined wasm base64; a separate subpath
    // (`goldenmatch/core/documents-wasm`), out of the default core graph.
    "core/documentsWasm": "src/core/documentsWasm.ts",
    "core/perceptualWasm": "src/core/perceptualWasm.ts",
    // Opt-in entry: the native HNSW ANN kernel (goldenhnsw) compiled to wasm.
    // Carries the inlined wasm (~62 KB base64) as a separate subpath, so the
    // default bundle stays lean; consumers pay it only when they import
    // `goldenmatch/core/hnsw-wasm`.
    "core/hnswWasm": "src/core/hnswWasm.ts",
    // Opt-in entry: the sketch (MinHash + LSH) kernel compiled to wasm, so the
    // MinHash-LSH blocker runs the shared sketch-core. ~65 KB inlined base64 as
    // a separate subpath (`goldenmatch/core/sketch-wasm`), out of the default
    // core bundle.
    "core/sketchWasm": "src/core/sketchWasm.ts",
    // Opt-in entry: the graph-core connected-components kernel compiled to wasm,
    // so the clustering step runs the shared core. ~37 KB inlined base64 as a
    // separate subpath (`goldenmatch/core/graph-wasm`), out of the default core
    // bundle.
    "core/graphWasm": "src/core/graphWasm.ts",
    // Opt-in entry: the fingerprint-core canonical record-hash kernel compiled
    // to wasm, so `recordFingerprint` runs the shared core. ~155 KB inlined
    // base64 as a separate subpath (`goldenmatch/core/fingerprint-wasm`), out of
    // the default core bundle.
    "core/fingerprintWasm": "src/core/fingerprintWasm.ts",
    // Opt-in entry: the in-house embedder (goldenembed-core) compiled to wasm, so
    // char-n-gram featurize + the projection head run at the edge (closes P10 —
    // the `ort`-linked native runtime can't compile to wasm). ~80 KB inlined
    // base64 as a separate subpath (`goldenmatch/core/goldenembed-wasm`).
    "core/goldenembedWasm": "src/core/goldenembedWasm.ts",
    // Opt-in entry: the Fellegi-Sunter block-scoring kernel (fs-core) compiled to
    // wasm, so the FS scoring path runs the SAME kernel as the Python native
    // wheel. ~187 KB inlined base64 as a separate subpath
    // (`goldenmatch/core/fs-wasm`), out of the default core bundle.
    "core/fsWasm": "src/core/fsWasm.ts",
    "node/index": "src/node/index.ts",
    "node/mcp/server": "src/node/mcp/server.ts",
    "node/a2a/server": "src/node/a2a/server.ts",
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
