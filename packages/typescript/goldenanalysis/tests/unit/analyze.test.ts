import { describe, expect, it } from "vitest";
import { analyze } from "../../src/core/analyze.js";
import { toJson, toMarkdown } from "../../src/core/render.js";
import type { AnalysisReport } from "../../src/core/types.js";
import { buildCustomersSmall } from "../fixtures/customersSmall.js";

describe("analyze", () => {
  it("explicit analyzer", () => {
    const report = analyze(buildCustomersSmall(), ["frame.summary"], { dataset: "customers" });
    expect(report.analyzers_run).toEqual(["frame.summary"]);
    expect(report.source["dataset"]).toBe("customers");
    expect(report.schema_version).toBe(1);
    expect(report.metrics.some((m) => m.key === "frame.row_count")).toBe(true);
  });

  it("defaults to frame-compatible analyzers", () => {
    const report = analyze([{ a: 1 }, { a: 1 }, { a: null }]);
    expect(report.analyzers_run).toEqual(["frame.summary"]);
  });

  it("records unavailable analyzers", () => {
    const report = analyze([{ a: 1 }], ["frame.summary", "does.not.exist"]);
    expect(report.analyzers_run).toEqual(["frame.summary"]);
    expect(report.source["unavailable"]).toContain("does.not.exist");
  });

  it("toJson round-trips", () => {
    const report = analyze(buildCustomersSmall(), ["frame.summary"]);
    const again = JSON.parse(toJson(report)) as AnalysisReport;
    expect(again.metrics.length).toBe(report.metrics.length);
  });

  it("toMarkdown contains header + keys", () => {
    const report = analyze(buildCustomersSmall(), ["frame.summary"]);
    const md = toMarkdown(report);
    expect(md).toContain("| Metric | Value |");
    for (const m of report.metrics) expect(md).toContain(m.key);
  });
});
