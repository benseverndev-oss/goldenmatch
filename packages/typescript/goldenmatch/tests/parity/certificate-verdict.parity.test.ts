/**
 * Cross-language parity: the certificate trust-verdict block (`certificateVerdict`)
 * — the `key_integrity` metadata the Cube / OSI / MetricFlow emitters write back —
 * is field-identical on Python and TS.
 *
 * `certificateVerdict` is a pure projection of a `KeyIntegrityCertificate`; the
 * fixture (Python-generated) reconstructs the same certificate from each case's
 * `init` (+ optional `resolution` mutation) and pins the emitted block. Read
 * directly by both this test and `tests/test_certificate_verdict.py` — no copy.
 */
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import {
  KeyIntegrityCertificate,
  certificateVerdict,
} from "../../src/core/semantic/keyIntegrity.js";

interface FixtureCase {
  name: string;
  init: {
    keyColumns: string[];
    grain: string[] | null;
    nRows: number;
    nKeyGroups: number;
    isUniqueAtGrain: boolean;
    duplicateKeyGroups: number;
    maxFanOut: number;
    measureFanOut: Record<string, number>;
  };
  resolution: {
    resolved_entities: number;
    fragmented_entities: number;
    undercount_estimate: number;
    undercount_ci_low: number | null;
    undercount_ci_high: number | null;
  } | null;
  expected: Record<string, unknown>;
}

const fixturePath = fileURLToPath(
  new URL("./fixtures/key-integrity/certificate_verdict_cases.json", import.meta.url),
);
const fixture = JSON.parse(readFileSync(fixturePath, "utf-8")) as { cases: FixtureCase[] };

function certFromCase(c: FixtureCase): KeyIntegrityCertificate {
  const cert = new KeyIntegrityCertificate({
    keyColumns: c.init.keyColumns,
    grain: c.init.grain,
    nRows: c.init.nRows,
    nKeyGroups: c.init.nKeyGroups,
    isUniqueAtGrain: c.init.isUniqueAtGrain,
    duplicateKeyGroups: c.init.duplicateKeyGroups,
    maxFanOut: c.init.maxFanOut,
    measureFanOut: { ...c.init.measureFanOut },
  });
  if (c.resolution) {
    cert.resolvedEntities = c.resolution.resolved_entities;
    cert.fragmentedEntities = c.resolution.fragmented_entities;
    cert.undercountEstimate = c.resolution.undercount_estimate;
    cert.undercountCiLow = c.resolution.undercount_ci_low;
    cert.undercountCiHigh = c.resolution.undercount_ci_high;
  }
  return cert;
}

describe("certificateVerdict — parity with the Python certificate_verdict oracle", () => {
  it("has cases", () => {
    expect(fixture.cases.length).toBeGreaterThan(0);
  });

  for (const c of fixture.cases) {
    it(`${c.name}: TS verdict block matches Python`, () => {
      const block = certificateVerdict(certFromCase(c));
      // Same keys (order-independent) + same values. Numbers compare to 12 dp
      // (pure arithmetic single-sourced with Python, same as the undercount CI).
      expect(Object.keys(block).sort()).toEqual(Object.keys(c.expected).sort());
      for (const [k, v] of Object.entries(c.expected)) {
        if (k === "undercount_ci") {
          const got = block[k] as number[];
          const want = v as number[];
          expect(got[0]).toBeCloseTo(want[0]!, 12);
          expect(got[1]).toBeCloseTo(want[1]!, 12);
        } else if (k === "measure_fan_out") {
          const got = block[k] as Record<string, number>;
          const want = v as Record<string, number>;
          expect(Object.keys(got).sort()).toEqual(Object.keys(want).sort());
          for (const [mk, mv] of Object.entries(want)) expect(got[mk]).toBeCloseTo(mv, 12);
        } else if (typeof v === "number") {
          expect(block[k]).toBeCloseTo(v, 12);
        } else {
          expect(block[k]).toEqual(v);
        }
      }
    });
  }
});
