// Cross-language parity: the TS Hungarian (core/assignment/hungarian.ts) must
// match the SINGLE committed oracle assignment_parity.json, which lives in the
// Python package and is generated from the canonical pure-Python LAP reference
// (== the Rust infermap-core::linear_sum_assignment kernel, gated by
// test_native_parity.py). Reading the SAME file both sides (not a copy) means
// there is no second drift surface: if hungarian.ts ever diverges from the
// Rust/Python reference on any case (including the ties where scipy used to
// disagree), this test fails.
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { linearSumAssignment } from "../../src/core/assignment/hungarian.js";

const fixtureUrl = new URL(
  "../../../../python/infermap/tests/fixtures/assignment_parity.json",
  import.meta.url,
);
const cases: Array<{ cost: number[][]; pairs: number[][] }> = JSON.parse(
  readFileSync(fileURLToPath(fixtureUrl), "utf-8"),
);

describe("assignment cross-language parity (hungarian.ts == Rust/Python)", () => {
  it("loaded the shared oracle fixture", () => {
    expect(cases.length).toBeGreaterThan(0);
  });

  it.each(cases.map((c, i) => [i, c] as const))(
    "case %i matches the canonical LAP reference",
    (_i, c) => {
      const got = linearSumAssignment(c.cost).map((p) => [p.row, p.col]);
      expect(got).toEqual(c.pairs);
    },
  );
});
