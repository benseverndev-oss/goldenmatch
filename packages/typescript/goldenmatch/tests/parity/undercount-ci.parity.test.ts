/**
 * Cross-language parity: the resolution-tier undercount 95% Wilson score
 * interval is bit-identical on Python and TS.
 *
 * `undercountEstimate = fragmented/resolved` is a point estimate; `wilsonInterval`
 * bounds its SAMPLING uncertainty (few resolved entities → wide interval). Pure
 * arithmetic with the same z-literal + op order both sides, so the fixture
 * (Python-generated) locks the outputs. Read directly by both this test and
 * `tests/test_undercount_ci.py` — no copy.
 */
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { wilsonInterval } from "../../src/core/semantic/keyIntegrity.js";

interface FixtureCase {
  name: string;
  fragmented: number;
  resolved: number;
  expected: {
    undercount_estimate: number;
    ci_low: number | null;
    ci_high: number | null;
  };
}

const fixturePath = fileURLToPath(
  new URL("./fixtures/key-integrity/undercount_ci_cases.json", import.meta.url),
);
const fixture = JSON.parse(readFileSync(fixturePath, "utf-8")) as { cases: FixtureCase[] };

describe("undercount Wilson CI — parity with the Python _wilson_interval oracle", () => {
  it("has cases", () => {
    expect(fixture.cases.length).toBeGreaterThan(0);
  });

  for (const c of fixture.cases) {
    it(`${c.name}: TS Wilson interval matches Python`, () => {
      const ci = wilsonInterval(c.fragmented, c.resolved);
      if (c.expected.ci_low === null) {
        expect(ci).toBeNull();
      } else {
        expect(ci).not.toBeNull();
        expect(ci![0]).toBeCloseTo(c.expected.ci_low, 12);
        expect(ci![1]).toBeCloseTo(c.expected.ci_high!, 12);
      }
    });
  }
});
