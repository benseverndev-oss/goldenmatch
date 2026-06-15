/**
 * Browser-mode vitest config for the cross-JS-target WASM equivalence harness
 * (R1 Workstream A). Runs `tests/spike/browser-kernel-equivalence.test.ts` in a
 * REAL headless chromium via the Playwright provider — the same Playwright
 * toolchain the repo's `web_ui_e2e` CI lane already uses.
 *
 * Standalone (not merged into the default config): the default Node lane stays
 * untouched. Invoked explicitly by the r1-kernel-js-targets.yml `browser` job:
 *   pnpm exec vitest run --config vitest.browser.config.ts
 */
// @ts-nocheck — the playwright provider factory comes from @vitest/browser +
// @vitest/browser-playwright, devDependencies installed only in the `browser` CI
// job; the default typecheck must not require them. Validated by the CI job that
// actually runs this config.
import { defineConfig } from "vitest/config";
// vitest 4 moved browser.provider from a string to a factory import.
import { playwright } from "@vitest/browser-playwright";

export default defineConfig({
  test: {
    include: ["tests/spike/browser-kernel-equivalence.test.ts"],
    browser: {
      enabled: true,
      provider: playwright(),
      headless: true,
      instances: [{ browser: "chromium" }],
    },
  },
});
