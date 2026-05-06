import { defineConfig } from "@playwright/test";

// Boots `goldenmatch serve-ui` (no --dev) against the pytest fixture
// project on :5050. With the prebuilt frontend mirrored into
// goldenmatch/web/static/ (via scripts/build_web.py before this runs),
// FastAPI serves both /api/* and the SPA from a single origin — the
// actual production path users hit. Playwright drives that single port
// rather than the dev-only Vite proxy.
//
// CI must run `python scripts/build_web.py` (or equivalent) before
// invoking playwright test, otherwise serve-ui has no static assets and
// the SPA never loads.
export default defineConfig({
  testDir: "e2e",
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  use: {
    baseURL: "http://127.0.0.1:5050",
    trace: "retain-on-failure",
  },
  webServer: {
    command:
      "goldenmatch serve-ui ../../tests/web/fixtures/sample_project --no-open --port 5050",
    port: 5050,
    reuseExistingServer: false,
    timeout: 60_000,
  },
});
