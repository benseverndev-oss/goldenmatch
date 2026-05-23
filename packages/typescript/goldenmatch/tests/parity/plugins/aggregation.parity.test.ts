/**
 * Aggregation plugin parity tests (Phase 5 Part 4/N -- goldenmatch #208).
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import type { GoldenStrategyPlugin } from "../../../src/core/plugins/base.js";
import {
  AgreementRateStrategy,
  CountDistinctStrategy,
  CountNonNullStrategy,
} from "../../../src/core/plugins/builtin/aggregation.js";

const FIXTURES_DIR = resolve(__dirname, "..", "fixtures");

interface FixtureCase {
  id: string;
  inputs: { values: unknown[] };
  expected: {
    value: unknown;
    confidence: number;
    idx: number | null;
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

function expectMatch(
  result: readonly [unknown, number] | readonly [unknown, number, number],
  expected: FixtureCase["expected"],
  caseId: string,
): void {
  const [value, confidence, idx] = [result[0], result[1], result.length > 2 ? result[2] : null];

  if (expected.value === null) {
    expect(value, `${caseId} value`).toBeNull();
  } else if (typeof expected.value === "number" && typeof value === "number") {
    expect(value, `${caseId} value`).toBeCloseTo(expected.value as number, 12);
  } else {
    expect(value, `${caseId} value`).toEqual(expected.value);
  }
  expect(confidence, `${caseId} confidence`).toBeCloseTo(expected.confidence, 12);
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
        const result = plugin.merge(c.inputs.values);
        expectMatch(result, c.expected, c.id);
      });
    }
  });
}

runFixtureSuite("count_distinct", new CountDistinctStrategy());
runFixtureSuite("count_non_null", new CountNonNullStrategy());
runFixtureSuite("agreement_rate", new AgreementRateStrategy());
