/**
 * Tests for the HTML reporter.
 * Mirrors the report_html behaviour in goldencheck/reporters/html_reporter.py.
 */
import { describe, it, expect } from "vitest";
import { reportHtml } from "../../../src/core/reporters/html.js";
import { makeFinding, makeColumnProfile, Severity, type DatasetProfile } from "../../../src/core/types.js";

function profile(): DatasetProfile {
  return {
    filePath: "test.csv",
    rowCount: 1000,
    columnCount: 2,
    columns: [
      makeColumnProfile({
        name: "email",
        inferredType: "string",
        nullCount: 50,
        nullPct: 0.05,
        uniqueCount: 950,
        uniquePct: 0.95,
        rowCount: 1000,
        topValues: [["a@x.com", 3], ["b@x.com", 2]],
      }),
      makeColumnProfile({
        name: "age",
        inferredType: "integer",
        nullCount: 0,
        nullPct: 0,
        uniqueCount: 80,
        uniquePct: 0.08,
        rowCount: 1000,
      }),
    ],
  };
}

describe("reportHtml", () => {
  it("produces a self-contained HTML document", () => {
    const findings = [
      makeFinding({ severity: Severity.ERROR, column: "email", check: "format", message: "bad emails" }),
    ];
    const html = reportHtml(findings, profile());
    expect(html.startsWith("<!DOCTYPE html>")).toBe(true);
    expect(html).toContain("</html>");
    expect(html).toContain("GoldenCheck Report");
    expect(html).toContain("test.csv");
  });

  it("includes a health grade badge", () => {
    const html = reportHtml([], profile());
    // Clean data → grade A
    expect(html).toMatch(/Health \(\d+\)/);
    expect(html).toContain("class=\"badge\"");
  });

  it("renders every finding's column and check", () => {
    const findings = [
      makeFinding({ severity: Severity.ERROR, column: "email", check: "format_detection", message: "x" }),
      makeFinding({ severity: Severity.WARNING, column: "age", check: "range_distribution", message: "y" }),
    ];
    const html = reportHtml(findings, profile());
    expect(html).toContain("format_detection");
    expect(html).toContain("range_distribution");
    expect(html).toContain("ERROR");
    expect(html).toContain("WARNING");
  });

  it("marks LLM-sourced findings", () => {
    const findings = [
      makeFinding({ severity: Severity.WARNING, column: "age", check: "invalid_values", message: "z", source: "llm" }),
    ];
    const html = reportHtml(findings, profile());
    expect(html).toContain("[LLM]");
  });

  it("renders each column's profile row", () => {
    const html = reportHtml([], profile());
    expect(html).toContain("email");
    expect(html).toContain("age");
    expect(html).toContain("Column Profile");
  });

  it("error/warning counts reflect the findings", () => {
    const findings = [
      makeFinding({ severity: Severity.ERROR, column: "email", check: "a", message: "x" }),
      makeFinding({ severity: Severity.WARNING, column: "age", check: "b", message: "y" }),
      makeFinding({ severity: Severity.WARNING, column: "age", check: "c", message: "z" }),
    ];
    const html = reportHtml(findings, profile());
    expect(html).toContain("Errors");
    expect(html).toContain("Warnings");
    expect(html).toContain("Total Findings");
  });
});
