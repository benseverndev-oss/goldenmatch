/**
 * Cross-language parity: the ER-resolution fragmentation reduction (cluster
 * membership → resolved/fragmented/undercount) is identical on Python and TS.
 *
 * `reduceFragmentation` has NO shared kernel — it's a scalar loop, not
 * Arrow-bulk muscle, so kernelizing it would pay FFI marshaling on a small call
 * (against the architecture frame). Instead it's single-sourced by this shared
 * fixture (the goldenanalysis quality_rollup / regressions precedent): both
 * surfaces run their reduction over the SAME synthetic clusters and must produce
 * identical counts. The fixture is generated from the Python reference
 * (`goldenmatch.semantic.key_integrity._reduce_fragmentation`) and read directly
 * by both this test and `tests/test_fragmentation_reduction.py` — no copy.
 */
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { reduceFragmentation } from "../../src/core/semantic/keyIntegrity.js";

interface FixtureCase {
  name: string;
  member_lists: number[][];
  keyvals: unknown[];
  expected: {
    resolved_entities: number;
    fragmented_entities: number;
    undercount_estimate: number;
  };
}

const fixturePath = fileURLToPath(
  new URL("./fixtures/key-integrity/fragmentation_reduction_cases.json", import.meta.url),
);
const fixture = JSON.parse(readFileSync(fixturePath, "utf-8")) as { cases: FixtureCase[] };

describe("fragmentation reduction — parity with the Python _reduce_fragmentation oracle", () => {
  it("has cases", () => {
    expect(fixture.cases.length).toBeGreaterThan(0);
  });

  for (const c of fixture.cases) {
    it(`${c.name}: TS reduction matches Python`, () => {
      const got = reduceFragmentation(c.member_lists, c.keyvals);
      expect(got.resolvedEntities).toBe(c.expected.resolved_entities);
      expect(got.fragmentedEntities).toBe(c.expected.fragmented_entities);
      expect(got.undercountEstimate).toBe(c.expected.undercount_estimate);
    });
  }
});
