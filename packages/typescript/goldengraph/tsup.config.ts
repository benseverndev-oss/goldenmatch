import { defineConfig } from "tsup";

export default defineConfig({
  entry: {
    // Base entry: edge-safe types + the query API + the registry. Zero wasm bytes.
    index: "src/index.ts",
    // Heavy opt-in subpath (`goldengraph/wasm`): the only module that embeds the
    // base64 wasm bytes + glue and registers the backend.
    "core/goldengraphWasm": "src/core/goldengraphWasm.ts",
  },
  format: ["esm", "cjs"],
  dts: true,
  sourcemap: true,
  clean: true,
  target: "node20",
  splitting: false,
  treeshake: true,
});
