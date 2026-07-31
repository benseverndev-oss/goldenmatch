/**
 * `key.integrity` analyzer — parity with the Python sibling.
 *
 * Mirrors `packages/python/goldenanalysis/tests/test_key_integrity_analyzer.py`
 * case-for-case: the same certificate dict (as it crosses the JSON wire) must
 * project to the same metrics + measure_fan_out table. The certificate carries no
 * `estimate` field over the wire, so uniqueness is recomputed as 1 - dupes/groups.
 */

import { describe, expect, it } from "vitest";
import { KeyIntegrityAnalyzer } from "../src/core/analyzers/keyIntegrity.js";
import { availableAnalyzers, loadAnalyzer } from "../src/core/registry.js";
import type { AnalyzerInput } from "../src/core/types.js";

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

function run(artifacts: Record<string, unknown>) {
  const input: AnalyzerInput = { dataset: "t", artifacts };
  const res = new KeyIntegrityAnalyzer().run(input);
  const byKey: Record<string, number | string> = {};
  for (const m of res.metrics) byKey[m.key] = m.value;
  return { metrics: res.metrics, byKey, tables: res.tables };
}

describe("key.integrity analyzer (parity with python)", () => {
  it("is registered", () => {
    expect(availableAnalyzers()).toContain("key.integrity");
    expect(loadAnalyzer("key.integrity").info.name).toBe("key.integrity");
  });

  it("projects structural metrics from a certificate dict", () => {
    const { byKey, tables } = run({ key_certificate: cert() });
    expect(byKey["key.uniqueness"]).toBe(0.5); // 1 - 1/2 (no `estimate` on the wire)
    expect(byKey["key.duplicate_groups"]).toBe(1);
    expect(byKey["key.max_fan_out"]).toBe(2.0);
    // undercount/fragmented omitted when resolution wasn't run
    expect("key.undercount_estimate" in byKey).toBe(false);
    expect("key.fragmented_entities" in byKey).toBe(false);
    const fan = tables.find((t) => t.name === "measure_fan_out");
    expect(fan?.rows).toEqual([["revenue", 1.6667]]);
  });

  it("emits undercount + fragmented when resolution ran", () => {
    const { byKey } = run({
      key_certificate: cert({ resolved_entities: 1, fragmented_entities: 1, undercount_estimate: 1.0 }),
    });
    expect(byKey["key.undercount_estimate"]).toBe(1.0);
    expect(byKey["key.fragmented_entities"]).toBe(1);
  });

  it("prefers an explicit `estimate` when the wire carries one", () => {
    const { byKey } = run({ key_certificate: cert({ estimate: 0.9 }) });
    expect(byKey["key.uniqueness"]).toBe(0.9);
  });

  it("returns empty when no certificate is supplied", () => {
    const { metrics, tables } = run({});
    expect(metrics).toEqual([]);
    expect(tables).toEqual([]);
  });
});
