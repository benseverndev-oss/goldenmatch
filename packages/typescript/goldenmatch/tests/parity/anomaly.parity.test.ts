/**
 * anomaly.parity.test.ts -- TS `detectAnomalies` == Python `detect_anomalies`.
 *
 * The fixture is AUTHORED by the Python oracle
 * (`packages/python/goldenmatch/scripts/emit_anomaly_fixture.py`), which runs the
 * real `core/anomaly.py` over an adversarial row set. Python is the reference;
 * this file asserts the TS port reproduces it exactly.
 *
 * Regenerate after any change to `core/anomaly.py`:
 *   cd packages/python/goldenmatch && python scripts/emit_anomaly_fixture.py
 */
import { describe, it, expect } from "vitest";
import { detectAnomalies } from "../../src/core/anomaly.js";
import type { Row } from "../../src/core/types.js";
import fixture from "./fixtures/anomaly.json" with { type: "json" };

interface Case {
  name: string;
  rows: Row[];
  sensitivity: string;
  expected: Array<Record<string, unknown>>;
}

describe("anomaly detection: Python parity", () => {
  const cases = fixture.cases as unknown as Case[];

  it("covers every sensitivity level plus the explicit-row_id shape", () => {
    expect(cases.map((c) => c.name)).toEqual([
      "adversarial_low",
      "adversarial_medium",
      "adversarial_high",
      "explicit_row_id",
    ]);
  });

  for (const c of cases) {
    it(`matches Python on ${c.name}`, () => {
      expect(detectAnomalies(c.rows, c.sensitivity)).toEqual(c.expected);
    });
  }

  it("the fixture actually exercises every detector (guards a vacuous pass)", () => {
    const high = cases.find((c) => c.name === "adversarial_high")!;
    const types = new Set(high.expected.map((a) => a["type"]));
    expect(types).toEqual(
      new Set([
        "fake_email",
        "fake_phone",
        "suspicious_zip",
        "placeholder",
        "exact_duplicate_row",
      ]),
    );
  });

  it("severity filtering is strictly nested low <= medium <= high", () => {
    const n = (name: string) => cases.find((c) => c.name === name)!.expected.length;
    expect(n("adversarial_low")).toBeLessThan(n("adversarial_medium"));
    expect(n("adversarial_medium")).toBeLessThanOrEqual(n("adversarial_high"));
  });
});

describe("anomaly detection: contracts the fixture can't express", () => {
  const dup = (n: number): Row[] =>
    Array.from({ length: n }, () => ({ a: "x", b: "y" }));

  it("flags 3+ identical rows but NOT exactly 2 (Python's >2 boundary)", () => {
    expect(detectAnomalies(dup(2), "high")).toEqual([]);
    const three = detectAnomalies(dup(3), "high");
    expect(three).toHaveLength(3);
    expect(three[0]!.type).toBe("exact_duplicate_row");
    expect(three[0]!.value).toBe("3 identical copies");
  });

  it("rejects an invalid sensitivity instead of silently being most-sensitive", () => {
    // The Python fix this mirrors: "Low"/"lo" used to fall through every branch
    // to NO filtering -- the inverse of what the caller asked for.
    expect(() => detectAnomalies([{ a: "test" }], "lo")).toThrow(/Invalid anomaly sensitivity/);
    expect(() => detectAnomalies([{ a: "test" }], "Low")).not.toThrow(); // case/space normalized
  });

  it("skips __-internal columns when scanning for placeholders", () => {
    const out = detectAnomalies([{ __internal__: "tbd", real: "fine" }], "high");
    expect(out).toEqual([]);
  });
});
