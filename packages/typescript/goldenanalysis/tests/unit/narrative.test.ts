import { describe, expect, it } from "vitest";
import { buildNarrative } from "../../src/core/narrative.js";
import type { Regression } from "../../src/core/regressions.js";
import { metric, report } from "../fixtures/reports.js";

function scenarioReport() {
  return report(
    "r7",
    [
      metric("match.recall_safe_bound", 0.89, "higher_better"),
      metric("cluster.singleton_ratio", 0.71, "neutral"),
      metric("quality.findings_total", 1205, "lower_better", "findings"),
    ],
    {
      tables: [
        {
          name: "findings_by_class",
          columns: ["class", "count"],
          rows: [
            ["email_blanked", 1188],
            ["phone_unparseable", 12],
          ],
        },
      ],
    },
  );
}

describe("buildNarrative", () => {
  it("leads with the largest-magnitude regression and lists co-movers + top finding", () => {
    const regs: Regression[] = [
      { metric: "match.recall_safe_bound", baseline: 0.97, current: 0.89, deltaPct: -8.2, flagged: true, direction: "higher_better" },
      { metric: "cluster.singleton_ratio", baseline: 0.58, current: 0.71, deltaPct: 22.4, flagged: true, direction: "neutral" },
    ];
    const text = buildNarrative(scenarioReport(), regs).toLowerCase();
    // largest |delta| is singleton (+22.4%) -> it leads; recall is a co-mover
    expect(text).toContain("singleton");
    expect(text).toContain("0.71");
    expect(text).toContain("recall safe bound");
    expect(text).toContain("0.89");
    expect(text).toContain("email_blanked");
  });

  it("no-regression path is a neutral metric summary", () => {
    const text = buildNarrative(scenarioReport(), []);
    expect(text).toContain("No regressions flagged");
  });

  it("handles a report with no numeric metrics", () => {
    const text = buildNarrative(report("r0", [metric("note", "n/a", "neutral", null)]), []);
    expect(text).toBe("No metrics to summarize.");
  });
});
