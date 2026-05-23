/**
 * Format plugin parity tests (Phase 5 Part 2 of N -- goldenmatch #208).
 *
 * Same fixture-driven harness as numeric.parity.test.ts. Each plugin
 * runs 16 cases curated to cover happy path + tie semantics + null
 * handling + adapter-specific quirks (URL https-upgrade, email plus-
 * addressing, bool tie-break-to-true, etc.).
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import type { GoldenStrategyPlugin } from "../../../src/core/plugins/base.js";
import {
  BooleanNormalizeStrategy,
  ConcatUniqueStrategy,
  EmailNormalizeStrategy,
  PhoneDigitsOnlyStrategy,
  ShortestValueStrategy,
  UrlCanonicalStrategy,
  WhitespaceNormalizeStrategy,
} from "../../../src/core/plugins/builtin/format.js";

const FIXTURES_DIR = resolve(__dirname, "..", "fixtures");

interface FixtureCase {
  id: string;
  inputs: {
    values: unknown[];
    quality_weights?: number[];
    rule_kwargs?: Record<string, unknown>;
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
        const opts: Record<string, unknown> = {};
        if (c.inputs.quality_weights !== undefined) {
          opts["qualityWeights"] = c.inputs.quality_weights;
        }
        if (c.inputs.rule_kwargs !== undefined) {
          opts["ruleKwargs"] = c.inputs.rule_kwargs;
        }
        const result = plugin.merge(
          c.inputs.values,
          Object.keys(opts).length ? opts : undefined,
        );
        expectMatch(result, c.expected, c.id);
      });
    }
  });
}

runFixtureSuite("shortest_value", new ShortestValueStrategy());
runFixtureSuite("concat_unique", new ConcatUniqueStrategy());
runFixtureSuite("email_normalize", new EmailNormalizeStrategy());
runFixtureSuite("phone_digits_only", new PhoneDigitsOnlyStrategy());
runFixtureSuite("url_canonical", new UrlCanonicalStrategy());
runFixtureSuite("whitespace_normalize", new WhitespaceNormalizeStrategy());
runFixtureSuite("boolean_normalize", new BooleanNormalizeStrategy());
