import { describe, expect, it } from "vitest";
import { analyze } from "../../src/core/analyze.js";
import type { Regression } from "../../src/core/regressions.js";
import { toMarkdown } from "../../src/core/render.js";
import { buildCustomersSmall } from "../fixtures/customersSmall.js";
import { night } from "../fixtures/reports.js";

describe("toMarkdown regression callout", () => {
  it("is byte-identical to the 2-column form when no regressions are passed", () => {
    const report = analyze(buildCustomersSmall(), ["frame.summary"], { dataset: "customers" });
    expect(toMarkdown(report, [])).toBe(toMarkdown(report));
    const md = toMarkdown(report);
    expect(md).toContain("| Metric | Value |");
    expect(md).not.toContain("Δ vs baseline");
    expect(md).not.toContain("WARNING");
  });

  it("adds a callout + Δ column when regressions are flagged", () => {
    const report = night("r7", 0.89, 0.71, 1205);
    const regs: Regression[] = [
      { metric: "match.recall_safe_bound", baseline: 0.97, current: 0.89, deltaPct: -8.2, flagged: true, direction: "higher_better" },
    ];
    const md = toMarkdown(report, regs);
    expect(md).toContain("> WARNING: 1 regression(s) flagged.");
    expect(md).toContain("match.recall_safe_bound 0.97 -> 0.89 (-8.2%)");
    expect(md).toContain("| Metric | Value | Δ vs baseline |");
    expect(md).toContain("🔴 -8.2%");
    // a metric not in the regression set gets a blank delta cell
    expect(md).toContain("| cluster.singleton_ratio | 0.71 ratio |  |");
  });

  it("renders the Δ column without a callout for a considered-but-not-flagged metric", () => {
    const report = night("r7", 0.96, 0.58, 410);
    const regs: Regression[] = [
      { metric: "match.recall_safe_bound", baseline: 0.97, current: 0.96, deltaPct: -1.0, flagged: false, direction: "higher_better" },
    ];
    const md = toMarkdown(report, regs);
    expect(md).not.toContain("WARNING");
    expect(md).toContain("| Metric | Value | Δ vs baseline |");
    expect(md).toContain("| match.recall_safe_bound | 0.96 ratio | -1.0% |");
  });
});
