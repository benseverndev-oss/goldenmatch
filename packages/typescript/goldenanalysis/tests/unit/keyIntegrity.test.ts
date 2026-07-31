import { describe, expect, it } from "vitest";
import { KeyIntegrityAnalyzer } from "../../src/core/analyzers/keyIntegrity.js";
import { availableAnalyzers, loadAnalyzer } from "../../src/core/registry.js";
import type { AnalyzerInput, Metric } from "../../src/core/types.js";

function input(artifacts: Record<string, unknown>): AnalyzerInput {
  return { dataset: "t", artifacts };
}

function byKey(metrics: readonly Metric[]): Map<string, Metric> {
  return new Map(metrics.map((m) => [m.key, m]));
}

// Mirror of packages/python/goldenanalysis/tests/test_key_integrity_analyzer.py::_cert.
function cert(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    key_columns: ["customer_id"],
    grain: null,
    n_rows: 3,
    n_key_groups: 2,
    is_unique_at_grain: false,
    duplicate_key_groups: 1,
    max_fan_out: 2.0,
    measure_fan_out: { revenue: 1.6667 },
    resolved_entities: null,
    fragmented_entities: null,
    undercount_estimate: null,
    estimable: true,
    note: "",
    ...overrides,
  };
}

describe("key.integrity", () => {
  it("is registered", () => {
    expect(availableAnalyzers()).toContain("key.integrity");
    expect(loadAnalyzer("key.integrity")).toBeInstanceOf(KeyIntegrityAnalyzer);
  });

  it("projects structural metrics from a serialized certificate", () => {
    const r = new KeyIntegrityAnalyzer().run(input({ key_certificate: cert() }));
    const m = byKey(r.metrics);
    expect(m.get("key.uniqueness")!.value).toBe(0.5); // 1 - 1/2
    expect(m.get("key.uniqueness")!.direction).toBe("higher_better");
    expect(m.get("key.duplicate_groups")!.value).toBe(1);
    expect(m.get("key.max_fan_out")!.value).toBe(2.0);
    // undercount/fragmented omitted when resolution wasn't run
    expect(m.has("key.undercount_estimate")).toBe(false);
    expect(m.has("key.fragmented_entities")).toBe(false);
    const fan = new Map(r.tables.map((t) => [t.name, t.rows]));
    expect(fan.get("measure_fan_out")).toEqual([["revenue", 1.6667]]);
  });

  it("emits undercount + fragmented when resolution ran", () => {
    const r = new KeyIntegrityAnalyzer().run(
      input({ key_certificate: cert({ resolved_entities: 1, fragmented_entities: 1, undercount_estimate: 1.0 }) }),
    );
    const m = byKey(r.metrics);
    expect(m.get("key.undercount_estimate")!.value).toBe(1.0);
    expect(m.get("key.fragmented_entities")!.value).toBe(1);
  });

  it("prefers an explicit estimate when the serialized cert carries one", () => {
    const r = new KeyIntegrityAnalyzer().run(input({ key_certificate: cert({ estimate: 0.83 }) }));
    expect(byKey(r.metrics).get("key.uniqueness")!.value).toBe(0.83);
  });

  it("uniqueness is 1.0 when there are no key groups", () => {
    const r = new KeyIntegrityAnalyzer().run(
      input({ key_certificate: cert({ n_key_groups: 0, duplicate_key_groups: 0 }) }),
    );
    expect(byKey(r.metrics).get("key.uniqueness")!.value).toBe(1.0);
  });

  it("max_fan_out falls back to 1.0 on a falsy value (mirrors `or 1.0`)", () => {
    const r = new KeyIntegrityAnalyzer().run(input({ key_certificate: cert({ max_fan_out: 0 }) }));
    expect(byKey(r.metrics).get("key.max_fan_out")!.value).toBe(1.0);
  });

  it("no certificate -> empty result", () => {
    const r = new KeyIntegrityAnalyzer().run(input({}));
    expect(r.metrics).toEqual([]);
    expect(r.tables).toEqual([]);
  });
});
