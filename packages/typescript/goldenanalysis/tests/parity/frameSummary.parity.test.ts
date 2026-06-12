/**
 * Cross-surface parity — the TypeScript `frame.summary` report must match the
 * Python-locked `report_frame_summary.json` on the ENGINE-INDEPENDENT metrics.
 *
 * `report_frame_summary.json` is a byte-identical copy of
 * `packages/python/goldenanalysis/tests/fixtures/report_frame_summary.json` (the
 * file Python's `test_report_schema.py` locks). Excluded from the parity contract
 * (engine-specific, see frameSummary.ts): `frame.memory_bytes` (polars
 * `estimated_size()`) and the `per_column` `dtype` column (polars dtype names).
 */

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { analyze } from "../../src/core/analyze.js";
import type { AnalysisReport } from "../../src/core/types.js";
import { buildCustomersSmall } from "../fixtures/customersSmall.js";

const __dirname = dirname(fileURLToPath(import.meta.url));
const FIXTURE = join(__dirname, "..", "fixtures", "report_frame_summary.json");

/** Project a report onto the cross-surface parity contract. */
function parityProjection(report: AnalysisReport) {
  return {
    schema_version: report.schema_version,
    source: report.source,
    metrics: report.metrics
      .filter((m) => m.key !== "frame.memory_bytes")
      .map((m) => ({ key: m.key, value: m.value, unit: m.unit ?? undefined, direction: m.direction })),
    tables: report.tables.map((t) => ({
      name: t.name,
      columns: t.columns.filter((c) => c !== "dtype"),
      // Drop the dtype cell (index 1) from each row.
      rows: t.rows.map((row) => row.filter((_, i) => i !== 1)),
    })),
    narrative: report.narrative,
    analyzers_run: report.analyzers_run,
  };
}

describe("parity: frame.summary vs python", () => {
  it("engine-independent metrics + per_column match the python-locked report", () => {
    const expected = JSON.parse(readFileSync(FIXTURE, "utf-8")) as AnalysisReport;
    const got = analyze(buildCustomersSmall(), ["frame.summary"], { dataset: "customers" });

    // The Python fixture has no generated_at/run_id (volatile, stripped at gen time).
    expect(parityProjection(got)).toEqual(parityProjection(expected));
  });
});
