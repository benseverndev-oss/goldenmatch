import { describe, it, expect } from "vitest";
import { TabularData } from "../../../src/core/data.js";
import { CompositeKeyProfiler } from "../../../src/core/relations/composite-key.js";

function orderLines(): TabularData {
  // (order_id, line_no) is a composite key; neither column is unique alone.
  return new TabularData([
    { order_id: 1, line_no: 1, sku: "a", qty: 2 },
    { order_id: 1, line_no: 2, sku: "b", qty: 1 },
    { order_id: 1, line_no: 3, sku: "c", qty: 5 },
    { order_id: 2, line_no: 1, sku: "a", qty: 1 },
    { order_id: 2, line_no: 2, sku: "d", qty: 1 },
    { order_id: 3, line_no: 1, sku: "e", qty: 9 },
  ]);
}

describe("CompositeKeyProfiler", () => {
  const profiler = new CompositeKeyProfiler();

  it("discovers a composite key", () => {
    const findings = profiler.profile(orderLines());
    expect(findings.length).toBeGreaterThan(0);
    const keys = new Set(
      findings.map((f) => (f.metadata["key_columns"] as string[]).join("+")),
    );
    expect(keys.has("order_id+line_no")).toBe(true);
    const f = findings.find(
      (x) => (x.metadata["key_columns"] as string[]).join("+") === "order_id+line_no",
    )!;
    expect(f.check).toBe("composite_key");
    expect(f.column).toBe("order_id"); // anchored on first key column
  });

  it("is silent when a single-column key exists", () => {
    const rows = orderLines().rows.map((r, i) => ({ ...r, pk: i }));
    expect(profiler.profile(new TabularData(rows))).toEqual([]);
  });

  it("is silent on trivial frames", () => {
    expect(profiler.profile(new TabularData([{ a: 1 }, { a: 2 }, { a: 3 }]))).toEqual([]);
    expect(profiler.profile(new TabularData([{ a: 1, b: 2 }]))).toEqual([]);
  });
});
