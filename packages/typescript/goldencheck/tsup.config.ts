import { defineConfig } from "tsup";

export default defineConfig({
  entry: {
    index: "src/index.ts",
    "core/index": "src/core/index.ts",
    // Opt-in entry: the goldencheck-core deep-profiling kernels compiled to wasm.
    // Carries the inlined wasm (~234 KB base64) as a separate subpath, so the
    // default bundle stays wasm-free; consumers pay it only when they import
    // `goldencheck/core/wasm` and call enableGoldencheckWasm().
    "core/goldencheckWasm": "src/core/goldencheckWasm.ts",
    "node/index": "src/node/index.ts",
    "node/mcp/server": "src/node/mcp/server.ts",
    cli: "src/cli.ts",
  },
  format: ["esm", "cjs"],
  dts: true,
  sourcemap: true,
  clean: true,
  target: "node20",
  splitting: false,
  treeshake: true,
});
