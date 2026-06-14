/**
 * Cloudflare Workers (workerd) vitest config for the cross-JS-target WASM
 * equivalence harness (R1 Workstream A) — THE edge target. Runs
 * `tests/spike/workers-kernel-equivalence.test.ts` inside the real Workers
 * runtime via `@cloudflare/vitest-pool-workers` (which embeds workerd /
 * Miniflare), so WASM instantiation + the universal base64 loader are exercised
 * in actual workerd, not a Node shim.
 *
 * Standalone (not merged into the default config): the default Node lane stays
 * untouched. Invoked explicitly by the r1-kernel-js-targets.yml `workers` job:
 *   pnpm exec vitest run --config vitest.workers.config.ts
 *
 * The pool is a devDependency installed only in that CI job (and locally when
 * you want to run Workers); the import is intentionally not resolved by the
 * default Node lane.
 *
 * vitest 4 wiring: `@cloudflare/vitest-pool-workers` ^0.16 (the vitest-4-compatible
 * line) exposes `cloudflareTest()` — a Vite PLUGIN that installs the workerd pool
 * runner — rather than the old `defineWorkersConfig` helper. We add it to
 * `plugins` and keep the test config in `defineConfig`.
 */
// @ts-nocheck — depends on @cloudflare/vitest-pool-workers, installed only in the
// `workers` CI job; the default typecheck does not have it and must not require it.
import { defineConfig } from "vitest/config";
import { cloudflareTest } from "@cloudflare/vitest-pool-workers";

export default defineConfig({
  plugins: [
    cloudflareTest({
      miniflare: {
        // The kernel needs no bindings; nodejs_compat keeps Buffer-ish globals
        // available. The `.wasm` is compiled at build time (no runtime codegen).
        compatibilityFlags: ["nodejs_compat"],
        compatibilityDate: "2024-09-23",
        // Treat every `.wasm` as a CompiledWasm module so a `.wasm?module` import
        // resolves to a precompiled WebAssembly.Module in workerd (the only
        // Workers-legal path; runtime codegen is banned).
        modulesRules: [{ type: "CompiledWasm", include: ["**/*.wasm"] }],
      },
    }),
  ],
  test: {
    include: ["tests/spike/workers-kernel-equivalence.test.ts"],
    server: {
      deps: {
        // Inline the artifacts so the pool's worker module graph (not vite's
        // host transform) resolves the `.wasm?module` import — that's where the
        // CompiledWasm rule applies.
        inline: [/score_wasm/],
      },
    },
  },
});
