import { describe, it, expect } from "vitest";
import { mkdtempSync, existsSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type { DedupeResult } from "../../src/core/types.js";

// Wave 2.3: the Export tab now writes real files (was a setTimeout stub).
// writeExports is the extracted, testable core of that path.

function fakeResult(): DedupeResult {
  return {
    goldenRecords: [{ id: 1, name: "Alice" }],
    dupes: [{ id: 2, name: "Alicia" }],
    unique: [{ id: 3, name: "Bob" }],
  } as unknown as DedupeResult;
}

describe("writeExports", () => {
  it("writes golden/dupes/unique as real CSV files", async () => {
    const { writeExports } = await import("../../src/node/tui/app.js");
    const dir = mkdtempSync(join(tmpdir(), "gm-tui-csv-"));
    const paths = writeExports(fakeResult(), "csv", dir);

    expect(paths).toHaveLength(3);
    for (const p of paths) expect(existsSync(p)).toBe(true);

    const golden = readFileSync(join(dir, "golden.csv"), "utf8");
    expect(golden).toContain("name");
    expect(golden).toContain("Alice");
  });

  it("writes JSON when format is json", async () => {
    const { writeExports } = await import("../../src/node/tui/app.js");
    const dir = mkdtempSync(join(tmpdir(), "gm-tui-json-"));
    writeExports(fakeResult(), "json", dir);

    const parsed = JSON.parse(readFileSync(join(dir, "dupes.json"), "utf8"));
    expect(Array.isArray(parsed)).toBe(true);
    expect(parsed[0].name).toBe("Alicia");
  });
});
