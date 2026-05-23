/**
 * Numeric plugin parity tests (Phase 5 Part 1 of N -- goldenmatch #208).
 *
 * Loads JSON fixtures emitted by
 * `scripts/generate_parity_fixtures.py` and asserts byte-equal output
 * from the TS port's numeric builtins.
 *
 * Regenerate fixtures with:
 *
 *   .venv/Scripts/python.exe \
 *     packages/python/goldenmatch/scripts/generate_parity_fixtures.py \
 *     --out packages/typescript/goldenmatch/tests/parity/fixtures/
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import type { GoldenStrategyPlugin } from "../../../src/core/plugins/base.js";
import {
  NumericMaxStrategy,
  NumericMeanStrategy,
  NumericMedianStrategy,
  NumericMinStrategy,
  NumericSumStrategy,
  NumericWeightedAverageStrategy,
} from "../../../src/core/plugins/builtin/numeric.js";

const FIXTURES_DIR = resolve(__dirname, "..", "fixtures");

interface FixtureCase {
  id: string;
  inputs: {
    values: unknown[];
    quality_weights?: number[];
  };
  expected: {
    value: unknown;
    confidence: number;
    idx: number | null;
    error?: string;
  };
}

interface Fixture {
  name: string;
  schema_version: number;
  cases: FixtureCase[];
}

function loadFixture(name: string): Fixture {
  const path = resolve(FIXTURES_DIR, `${name}.json`);
  return JSON.parse(readFileSync(path, "utf-8")) as Fixture;
}

/**
 * Compare TS result to expected. Floats use a 12-decimal tolerance
 * (matches Python's repr precision for the common cases; tight enough
 * to catch real divergence, loose enough to absorb IEEE-754 rounding
 * differences between JS and Python).
 */
function expectMatch(
  result: readonly [unknown, number] | readonly [unknown, number, number],
  expected: FixtureCase["expected"],
  caseId: string,
): void {
  const [value, confidence, idx] = [result[0], result[1], result.length > 2 ? result[2] : null];

  // Value comparison: null === null, else either deep-equal or
  // numeric near-equal.
  if (expected.value === null) {
    expect(value, `${caseId} value`).toBeNull();
  } else if (typeof expected.value === "number" && typeof value === "number") {
    expect(value, `${caseId} value`).toBeCloseTo(expected.value as number, 12);
  } else {
    expect(value, `${caseId} value`).toEqual(expected.value);
  }

  expect(confidence, `${caseId} confidence`).toBeCloseTo(expected.confidence, 12);

  // idx may be omitted in the result (length=2). The Python emitter
  // writes idx=null for that case to keep schema consistent.
  if (expected.idx === null) {
    expect(idx === null || idx === undefined, `${caseId} idx absent`).toBe(true);
  } else {
    expect(idx, `${caseId} idx`).toBe(expected.idx);
  }
}

function runFixtureSuite(name: string, plugin: GoldenStrategyPlugin): void {
  const fixture = loadFixture(name);
  describe(`${name} parity`, () => {
    for (const c of fixture.cases) {
      it(c.id, () => {
        const opts =
          c.inputs.quality_weights !== undefined
            ? { qualityWeights: c.inputs.quality_weights }
            : undefined;
        const result = plugin.merge(c.inputs.values, opts);
        expectMatch(result, c.expected, c.id);
      });
    }
  });
}

runFixtureSuite("numeric_max", new NumericMaxStrategy());
runFixtureSuite("numeric_min", new NumericMinStrategy());
runFixtureSuite("numeric_mean", new NumericMeanStrategy());
runFixtureSuite("numeric_median", new NumericMedianStrategy());
runFixtureSuite("numeric_sum", new NumericSumStrategy());
runFixtureSuite("numeric_weighted_average", new NumericWeightedAverageStrategy());
