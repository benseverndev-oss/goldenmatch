import { defineConfig } from "tsup";

export default defineConfig({
  entry: {
    // Base entry: edge-safe types + the resolver + the registry. Pulls ZERO
    // wasm bytes — default consumers never load the kernel.
    index: "src/index.ts",
    // Heavy opt-in subpath (`goldenprofile/wasm`): the only module that embeds
    // the base64 wasm bytes + wasm-bindgen glue and registers the backend.
    "core/goldenprofileWasm": "src/core/goldenprofileWasm.ts",
  },
  format: ["esm", "cjs"],
  dts: true,
  sourcemap: true,
  clean: true,
  target: "node20",
  splitting: false,
  treeshake: true,
});
