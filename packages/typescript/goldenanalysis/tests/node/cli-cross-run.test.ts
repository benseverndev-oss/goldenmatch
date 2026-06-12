import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { parsePolicy, runRegressions, runTrend } from "../../src/cli.js";
import { ReportHistory } from "../../src/node/history.js";
import { scenarioReports } from "../fixtures/reports.js";

let dir: string;
let historyPath: string;
beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "ga-cli-"));
  historyPath = join(dir, "analysis.jsonl");
  const hist = new ReportHistory({ path: historyPath });
  for (const rep of scenarioReports()) hist.append(rep);
});
afterEach(() => {
  rmSync(dir, { recursive: true, force: true });
});

describe("parsePolicy", () => {
  it("parses JSON form", () => {
    expect(parsePolicy('{"defaultPct":5,"perMetric":{"a":2}}')).toEqual({ defaultPct: 5, perMetric: { a: 2 } });
  });
  it("parses key=pct form with a *=pct default", () => {
    expect(parsePolicy("match.recall_safe_bound=2,*=15")).toEqual({
      defaultPct: 15,
      perMetric: { "match.recall_safe_bound": 2 },
    });
  });
  it("returns undefined for an empty spec", () => {
    expect(parsePolicy(undefined)).toBeUndefined();
    expect(parsePolicy("")).toBeUndefined();
  });
});

describe("runTrend", () => {
  it("prints an ordered series for the metric", () => {
    const out = runTrend("cluster.singleton_ratio", { history: historyPath, dataset: "customers", last: 14 });
    expect(out).toContain("# Trend — cluster.singleton_ratio (customers)");
    expect(out).toContain("r7\t0.71");
    expect(out.trim().split("\n").length).toBe(9); // header + 8 points
  });

  it("reports no data for an unknown metric", () => {
    const out = runTrend("does.not.exist", { history: historyPath, dataset: "customers" });
    expect(out).toContain("(no data)");
  });
});

describe("runRegressions", () => {
  it("flags the scenario and renders a callout + delta column + narrative", () => {
    const result = runRegressions({
      history: historyPath,
      dataset: "customers",
      baseline: "rolling_median",
      policy: "match.recall_safe_bound=2,*=10",
    });
    expect(result.flaggedCount).toBeGreaterThanOrEqual(2);
    expect(result.text).toContain("> WARNING:");
    expect(result.text).toContain("Δ vs baseline");
    expect(result.text).toContain("🔴");
    expect(result.text).toContain("email_blanked"); // narrative names the top finding class
  });

  it("handles an empty history gracefully", () => {
    const result = runRegressions({ history: join(dir, "empty.jsonl"), dataset: "customers" });
    expect(result.flaggedCount).toBe(0);
    expect(result.text).toContain("No reports in history.");
  });
});
