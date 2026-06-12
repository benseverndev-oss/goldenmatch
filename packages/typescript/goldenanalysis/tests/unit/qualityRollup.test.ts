import { describe, expect, it } from "vitest";
import { QualityRollupAnalyzer } from "../../src/core/analyzers/qualityRollup.js";
import type { AnalyzerInput, Metric } from "../../src/core/types.js";

const FINDINGS = [
  { severity: "WARNING", column: "email", check: "email_blanked" },
  { severity: "WARNING", column: "email", check: "email_blanked" },
  { severity: "ERROR", column: "phone", check: "phone_unparseable" },
];
const MANIFEST = {
  records: [
    { column: "email", transform: "blank_malformed", affected_rows: 1188, total_rows: 4000 },
    { column: "phone", transform: "e164", affected_rows: 12, total_rows: 4000 },
  ],
};

function run(artifacts: Record<string, unknown>) {
  const inp: AnalyzerInput = { dataset: "customers", artifacts };
  return new QualityRollupAnalyzer().run(inp);
}

function byKey(metrics: readonly Metric[]): Map<string, Metric> {
  return new Map(metrics.map((m) => [m.key, m]));
}

describe("quality.rollup", () => {
  it("quality + flow metrics, findings_by_class table", () => {
    const r = run({ findings: FINDINGS, manifest: MANIFEST });
    const m = byKey(r.metrics);
    expect(m.get("quality.findings_total")!.value).toBe(3);
    expect(m.get("quality.findings_total")!.direction).toBe("lower_better");
    expect(m.get("quality.columns_with_findings")!.value).toBe(2);
    expect(m.get("flow.rows_changed")!.value).toBe(1200);
    expect(m.get("flow.rules_fired")!.value).toBe(2);
    expect(m.has("quality.score")).toBe(false); // no profile
    const tbl = r.tables.find((t) => t.name === "findings_by_class")!;
    const rows = new Map(tbl.rows.map((row) => [row[0], row[1]]));
    expect(rows.get("email_blanked")).toBe(2);
    expect(rows.get("phone_unparseable")).toBe(1);
  });

  it("quality.score from a duck-typed profile.healthScore", () => {
    const profile = { healthScore: () => ["B", 80] };
    const m = byKey(run({ findings: FINDINGS, profile }).metrics);
    expect(m.get("quality.score")!.value).toBe(0.8);
    expect(m.get("quality.score")!.direction).toBe("higher_better");
  });

  it("degrades to findings-only", () => {
    const m = byKey(run({ findings: FINDINGS }).metrics);
    expect(m.has("quality.findings_total")).toBe(true);
    expect(m.has("flow.rows_changed")).toBe(false);
  });

  it("degrades to manifest-only", () => {
    const m = byKey(run({ manifest: MANIFEST }).metrics);
    expect(m.has("flow.rules_fired")).toBe(true);
    expect(m.has("quality.findings_total")).toBe(false);
  });
});
