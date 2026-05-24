/**
 * End-to-end tests wiring the three real TS siblings through the pipeline.
 */

import { describe, it, expect, afterAll } from "vitest";
import { writeFileSync, rmSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { runDf, parseCsv, type Row } from "../../src/node/index.js";
import { run } from "../../src/node/run.js";
import { csv as sample } from "./_sample.js";

const dir = mkdtempSync(join(tmpdir(), "gp-e2e-"));
afterAll(() => rmSync(dir, { recursive: true, force: true }));

describe("runDf end-to-end", () => {
  it("runs the full check -> flow -> dedupe chain on rows", async () => {
    const rows = parseCsv(sample);
    const result = await runDf(rows);

    expect(result.status).toBe("success");
    expect(result.inputRows).toBe(5);
    expect(Object.keys(result.stages)).toEqual([
      "load",
      "goldencheck.scan",
      "goldenflow.transform",
      "goldenmatch.dedupe",
    ]);
    for (const sr of Object.values(result.stages)) {
      expect(sr.status).toBe("success");
    }
    // Dedupe artifacts present and array-shaped.
    expect(Array.isArray(result.artifacts["unique"])).toBe(true);
    expect(Array.isArray(result.artifacts["golden"])).toBe(true);
    expect(result.artifacts["findings"]).toBeDefined();
    expect(result.artifacts["manifest"]).toBeDefined();
    expect(result.artifacts["column_contexts"]).toBeDefined();
  });

  it("collapses an obvious duplicate pair into a golden record", async () => {
    const rows: Row[] = [
      { first_name: "John", last_name: "Smith", email: "john@example.com" },
      { first_name: "John", last_name: "Smith", email: "john@example.com" },
      { first_name: "Jane", last_name: "Doe", email: "jane@example.com" },
    ];
    const result = await runDf(rows);
    expect(result.status).toBe("success");
    // One golden record for the duplicate cluster.
    expect((result.artifacts["golden"] as Row[]).length).toBe(1);
  });
});

describe("node run(source) on a CSV file", () => {
  it("loads a CSV and runs the chain", async () => {
    const p = join(dir, "people.csv");
    writeFileSync(p, sample);
    const result = await run(p);
    expect(result.status).toBe("success");
    expect(result.source).toBe(p);
    expect(result.inputRows).toBe(5);
  });

  it("returns FAILED on a missing source file", async () => {
    const result = await run(join(dir, "missing.csv"));
    expect(result.status).toBe("failed");
    expect(result.errors[0]).toMatch(/Failed to load data/);
  });
});
