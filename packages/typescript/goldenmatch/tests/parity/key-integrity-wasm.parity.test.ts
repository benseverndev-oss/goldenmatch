/**
 * Cross-surface parity: the key-integrity-wasm kernel reproduces the shared
 * `key_integrity_golden.json` oracle, which was generated from the Python
 * reference `certify_key_integrity` (structural tier). The SAME fixture is
 * asserted by `key-integrity-core/tests/golden.rs`, so a green run here + there
 * proves the group-by uniqueness + fan-out reduction is identical on the Rust
 * kernel, the Python reference, and the TS/WASM surface.
 */
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { certifyStructural } from "../../src/core/keyIntegrityWasm.js";

interface FixtureCase {
  name: string;
  input: {
    n_rows: number;
    group_columns: unknown[][];
    measures: { name: string; values: unknown[] }[];
  };
  expected: {
    n_key_groups: number;
    duplicate_key_groups: number;
    max_fan_out: number;
    is_unique_at_grain: boolean;
    measure_fan_out: Record<string, number>;
  };
}

const fixturePath = fileURLToPath(
  new URL("./fixtures/key-integrity/key_integrity_golden.json", import.meta.url),
);
const fixture = JSON.parse(readFileSync(fixturePath, "utf-8")) as { cases: FixtureCase[] };

describe("key-integrity-wasm — parity with the Python certify_key_integrity oracle", () => {
  for (const c of fixture.cases) {
    it(`${c.name}: wasm structural result matches Python`, () => {
      const got = certifyStructural({
        nRows: c.input.n_rows,
        groupColumns: c.input.group_columns,
        measures: c.input.measures,
      });
      expect(got.nKeyGroups).toBe(c.expected.n_key_groups);
      expect(got.duplicateKeyGroups).toBe(c.expected.duplicate_key_groups);
      expect(got.maxFanOut).toBeCloseTo(c.expected.max_fan_out, 9);
      expect(got.isUniqueAtGrain).toBe(c.expected.is_unique_at_grain);

      const exp = c.expected.measure_fan_out;
      expect(Object.keys(got.measureFanOut).sort()).toEqual(Object.keys(exp).sort());
      for (const [k, v] of Object.entries(exp)) {
        expect(got.measureFanOut[k]!).toBeCloseTo(v, 9);
      }
    });
  }
});
