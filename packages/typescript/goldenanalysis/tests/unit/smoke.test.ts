import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { VERSION } from "../../src/index.js";

// This asserted a hardcoded "0.4.0", so EVERY release broke it -- the 2026-08-15
// repo-wide cut failed CI on this line alone. A literal also tests nothing: it
// restates the version rather than checking a relationship.
//
// What is worth asserting is that the exported constant stays in lockstep with
// package.json. That is the drift `scripts/check_version_consistency.py` exists
// to catch across every surface (it caught goldenflow shipping 1.1.2 in
// pyproject against 1.1.1 in __init__), and it needs no edit at release time.
describe("smoke", () => {
  it("exports a version that matches package.json", () => {
    const pkg = JSON.parse(
      readFileSync(fileURLToPath(new URL("../../package.json", import.meta.url)), "utf8"),
    ) as { version: string };
    expect(VERSION).toBe(pkg.version);
    expect(VERSION).toMatch(/^\d+\.\d+\.\d+$/);
  });
});
