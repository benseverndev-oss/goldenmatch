import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { handleTool } from "../../src/node/mcp/server.js";
import { ReportHistory } from "../../src/node/history.js";
import { scenarioReports } from "../fixtures/reports.js";

let dir: string;
let historyPath: string;

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "ga-mcp-"));
  historyPath = join(dir, "analysis.jsonl");
  const hist = new ReportHistory({ path: historyPath });
  for (const rep of scenarioReports()) hist.append(rep);
});
afterEach(() => {
  rmSync(dir, { recursive: true, force: true });
});

describe("goldenanalysis MCP handleTool", () => {
  it("list_analyzers returns the discoverable analyzers", () => {
    const result = handleTool("list_analyzers", {}) as { analyzers: string[] };
    expect(Array.isArray(result.analyzers)).toBe(true);
    expect(result.analyzers.length).toBeGreaterThan(0);
    expect(result.analyzers).toContain("frame.summary");
  });

  it("analyze_frame analyzes a .csv into a metrics report (json default)", () => {
    const csv = join(dir, "customers.csv");
    writeFileSync(csv, "id,name\n1,Alice\n2,Bob\n2,Bob\n", "utf-8");
    const result = handleTool("analyze_frame", { path: csv }) as {
      metrics?: unknown[];
      source?: Record<string, unknown>;
    };
    expect(Array.isArray(result.metrics)).toBe(true);
    expect(result.source?.["dataset"]).toBe("customers");
  });

  it("analyze_frame renders markdown when output_format=markdown", () => {
    const csv = join(dir, "c.csv");
    writeFileSync(csv, "id,name\n1,Alice\n2,Bob\n", "utf-8");
    const result = handleTool("analyze_frame", { path: csv, output_format: "markdown" }) as {
      markdown?: string;
    };
    expect(typeof result.markdown).toBe("string");
    expect(result.markdown!.length).toBeGreaterThan(0);
  });

  it("analyze_frame rejects an unsupported input type (.parquet)", () => {
    const result = handleTool("analyze_frame", { path: "data.parquet" }) as { error?: string };
    expect(result.error).toMatch(/unsupported input type/);
  });

  it("get_trend returns a snake_case series over the history", () => {
    const result = handleTool("get_trend", {
      history: historyPath,
      metric: "cluster.singleton_ratio",
      dataset: "customers",
    }) as { metric_key: string; dataset: string; points: [string, number][] };
    expect(result.metric_key).toBe("cluster.singleton_ratio");
    expect(result.dataset).toBe("customers");
    expect(Array.isArray(result.points)).toBe(true);
    expect(result.points.length).toBeGreaterThan(0);
  });

  it("detect_regressions returns a flagged list with snake_case fields", () => {
    const result = handleTool("detect_regressions", {
      history: historyPath,
      dataset: "customers",
    }) as { flagged: Array<Record<string, unknown>> };
    expect(Array.isArray(result.flagged)).toBe(true);
    for (const r of result.flagged) {
      expect(r).toHaveProperty("metric");
      expect(r).toHaveProperty("delta_pct");
      expect(r).toHaveProperty("direction");
    }
  });

  it("detect_regressions accepts a snake_case policy object", () => {
    const result = handleTool("detect_regressions", {
      history: historyPath,
      dataset: "customers",
      policy: { default_pct: 1, per_metric: { "cluster.singleton_ratio": 0.5 } },
    }) as { flagged: unknown[] };
    expect(Array.isArray(result.flagged)).toBe(true);
  });

  it("unknown tool returns an error", () => {
    const result = handleTool("nope", {}) as { error?: string };
    expect(result.error).toMatch(/Unknown tool/);
  });
});
