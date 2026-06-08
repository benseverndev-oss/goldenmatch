import { describe, expect, it } from "vitest";
import { FrameSummaryAnalyzer } from "../../src/core/analyzers/frameSummary.js";
import type { Metric } from "../../src/core/types.js";
import { buildCustomersSmall } from "../fixtures/customersSmall.js";

function metrics(): Record<string, Metric> {
  const result = new FrameSummaryAnalyzer().run({
    dataset: "customers",
    frame: buildCustomersSmall(),
    artifacts: {},
  });
  return Object.fromEntries(result.metrics.map((m) => [m.key, m]));
}

describe("frame.summary", () => {
  it("exact metric values (parity with python)", () => {
    const m = metrics();
    expect(m["frame.row_count"]!.value).toBe(20);
    expect(m["frame.column_count"]!.value).toBe(4);
    expect(m["frame.null_ratio_mean"]!.value).toBe(0.275);
    expect(m["frame.duplicate_row_ratio"]!.value).toBe(0.1);
    expect(typeof m["frame.memory_bytes"]!.value).toBe("number");
  });

  it("directions", () => {
    const m = metrics();
    expect(m["frame.row_count"]!.direction).toBe("neutral");
    expect(m["frame.null_ratio_mean"]!.direction).toBe("lower_better");
    expect(m["frame.duplicate_row_ratio"]!.direction).toBe("lower_better");
  });

  it("per_column table", () => {
    const result = new FrameSummaryAnalyzer().run({ dataset: "c", frame: buildCustomersSmall(), artifacts: {} });
    const table = result.tables.find((t) => t.name === "per_column");
    expect(table).toBeDefined();
    expect(table!.columns).toEqual(["column", "dtype", "null_ratio", "n_unique"]);
    expect(table!.rows.length).toBe(4);
  });
});
