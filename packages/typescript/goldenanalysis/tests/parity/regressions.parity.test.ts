/**
 * Cross-surface lock for the regression decision logic — parity with the Python
 * reference (`test_regressions_parity.py`) against the SAME data-driven fixture.
 *
 * `regressions_cases.json` is a byte-identical copy of
 * `packages/python/goldenanalysis/tests/fixtures/regressions_cases.json`. Each case
 * carries its `input` and the Python-locked `expected` {baseline, delta_pct, flagged}.
 * Floats are identical IEEE-754 doubles on both surfaces (same ops), so exact `toEqual`.
 *
 * Adversarial coverage: even/odd median, baseline==0, negative baseline, threshold
 * boundary (inclusive), all three directions, window>history, empty history.
 */

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { baselineValue, deltaPct, isRegression } from "../../src/core/regressions.js";
import type { Baseline } from "../../src/core/regressions.js";
import type { Direction } from "../../src/core/types.js";

const __dirname = dirname(fileURLToPath(import.meta.url));
const FIXTURE = join(__dirname, "..", "fixtures", "regressions_cases.json");

interface Case {
  input: {
    history: number[];
    strategy: string;
    window: number;
    current: number;
    direction: string;
    threshold_pct: number;
  };
  expected: { baseline: number | null; delta_pct?: number; flagged?: boolean };
}

describe("parity: regression logic vs python", () => {
  it("baseline / delta_pct / flagged match the python-locked fixture exactly", () => {
    const cases = JSON.parse(readFileSync(FIXTURE, "utf-8")) as Record<string, Case>;
    for (const [name, { input, expected }] of Object.entries(cases)) {
      const base = baselineValue(input.history, input.strategy as Baseline, input.window);
      const got: { baseline: number | null; delta_pct?: number; flagged?: boolean } = { baseline: base };
      if (base !== null) {
        got.delta_pct = deltaPct(base, input.current);
        got.flagged = isRegression(input.direction as Direction, base, input.current, input.threshold_pct);
      }
      expect(got, name).toEqual(expected);
    }
  });
});
