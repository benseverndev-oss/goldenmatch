import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    include: ["tests/**/*.test.ts"],
    // The cross-JS-target WASM harnesses run under their OWN runtimes
    // (vitest.browser.config.ts / vitest.workers.config.ts / `deno test`), NOT
    // the default Node pool — `atob`/the Workers env aren't this pool's globals.
    // Keep them out of the normal `vitest run` so the Node lane stays green.
    exclude: [
      "**/node_modules/**",
      "tests/spike/browser-kernel-equivalence.test.ts",
      "tests/spike/workers-kernel-equivalence.test.ts",
    ],
    coverage: {
      include: ["src/**/*.ts"],
      exclude: ["src/cli.ts", "src/node/**"],
    },
  },
});
