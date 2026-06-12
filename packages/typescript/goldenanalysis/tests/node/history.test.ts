import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { ReportHistory } from "../../src/node/history.js";
import { metric, night, report, scenarioReports } from "../fixtures/reports.js";

let dir: string;
beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "ga-hist-"));
});
afterEach(() => {
  rmSync(dir, { recursive: true, force: true });
});

function seed(hist: ReportHistory): void {
  for (const rep of scenarioReports()) hist.append(rep);
}

describe("ReportHistory (jsonl)", () => {
  it("appends and reads back in insertion order", () => {
    const hist = new ReportHistory({ path: join(dir, "a.jsonl") });
    hist.append(report("r0", [metric("m", 1, "neutral")]));
    hist.append(report("r1", [metric("m", 2, "neutral")]));
    expect(hist.reports("customers").map((r) => r.run_id)).toEqual(["r0", "r1"]);
  });

  it("upserts idempotently per (analysisName, dataset, runId)", () => {
    const hist = new ReportHistory({ path: join(dir, "a.jsonl") });
    hist.append(report("r0", [metric("m", 1, "neutral")]));
    hist.append(report("r0", [metric("m", 9, "neutral")])); // same key replaces
    const reps = hist.reports("customers");
    expect(reps.length).toBe(1);
    expect(reps[0]!.metrics[0]!.value).toBe(9);
  });

  it("separates by analysis name", () => {
    const hist = new ReportHistory({ path: join(dir, "a.jsonl") });
    hist.append(report("r0", [metric("m", 1, "neutral")]), "nightly");
    hist.append(report("r0", [metric("m", 2, "neutral")]), "adhoc");
    expect(hist.reports("customers", { analysisName: "nightly" }).length).toBe(1);
    expect(hist.reports("customers", { analysisName: "adhoc" })[0]!.metrics[0]!.value).toBe(2);
  });

  it("builds an ordered trend trimmed to lastN", () => {
    const hist = new ReportHistory({ path: join(dir, "a.jsonl") });
    seed(hist);
    const series = hist.trend("cluster.singleton_ratio", "customers", { lastN: 14 });
    expect(series.points.length).toBe(8);
    expect(series.points[series.points.length - 1]).toEqual(["r7", 0.71]);
  });

  it("flags the worked scenario (2% recall gate catches what a 10% gate misses)", () => {
    const hist = new ReportHistory({ path: join(dir, "a.jsonl") });
    seed(hist);
    const flagged = hist.detectRegressions("customers", {
      baseline: "rolling_median",
      policy: { defaultPct: 10, perMetric: { "match.recall_safe_bound": 2 } },
    });
    const keys = new Set(flagged.map((r) => r.metric));
    expect(keys.has("match.recall_safe_bound")).toBe(true);
    expect(keys.has("cluster.singleton_ratio")).toBe(true);
  });

  it("previous baseline over a post-step pair flags nothing", () => {
    const hist = new ReportHistory({ path: join(dir, "a.jsonl") });
    hist.append(report("a", [metric("match.recall_safe_bound", 0.89, "higher_better")]));
    hist.append(report("b", [metric("match.recall_safe_bound", 0.89, "higher_better")]));
    const flagged = hist.detectRegressions("customers", {
      baseline: "previous",
      policy: { defaultPct: 10, perMetric: { "match.recall_safe_bound": 2 } },
    });
    expect(flagged).toEqual([]);
  });

  it("persists durably — a fresh handle on the same file sees the reports", () => {
    const path = join(dir, "a.jsonl");
    const writer = new ReportHistory({ path });
    seed(writer);
    const reader = new ReportHistory({ path });
    expect(reader.reports("customers").length).toBe(8);
    expect(reader.reports("customers").map((r) => r.run_id)).toContain("r7");
  });

  it("returns an empty list for an unseen file", () => {
    const hist = new ReportHistory({ path: join(dir, "missing.jsonl") });
    expect(hist.reports("customers")).toEqual([]);
    expect(hist.detectRegressions("customers")).toEqual([]);
  });

  it("creates parent directories as needed", () => {
    const hist = new ReportHistory({ path: join(dir, "nested", "deep", "a.jsonl") });
    hist.append(night("r0", 0.9, 0.5, 1));
    expect(hist.reports("customers").length).toBe(1);
  });
});
