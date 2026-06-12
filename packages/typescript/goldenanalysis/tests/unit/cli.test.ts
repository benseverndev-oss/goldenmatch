import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { renderReportFromFile } from "../../src/cli.js";
import { buildCustomersSmall } from "../fixtures/customersSmall.js";

describe("cli", () => {
  it("report from a JSON rows file -> markdown", () => {
    const dir = mkdtempSync(join(tmpdir(), "ga-"));
    const file = join(dir, "customers.json");
    writeFileSync(file, JSON.stringify(buildCustomersSmall()), "utf-8");
    const out = renderReportFromFile(file, { format: "markdown", analyzers: "frame.summary" });
    expect(out).toContain("frame.row_count");
  });

  it("report --format json parses back", () => {
    const dir = mkdtempSync(join(tmpdir(), "ga-"));
    const file = join(dir, "customers.json");
    writeFileSync(file, JSON.stringify(buildCustomersSmall()), "utf-8");
    const out = renderReportFromFile(file, { format: "json" });
    const report = JSON.parse(out) as { metrics: { key: string }[] };
    expect(report.metrics.some((m) => m.key === "frame.row_count")).toBe(true);
  });

  it("report from a CSV file", () => {
    const dir = mkdtempSync(join(tmpdir(), "ga-"));
    const file = join(dir, "data.csv");
    writeFileSync(file, "a,b\n1,x\n1,x\n2,y\n", "utf-8");
    const out = renderReportFromFile(file, {});
    expect(out).toContain("frame.duplicate_row_ratio");
  });
});
