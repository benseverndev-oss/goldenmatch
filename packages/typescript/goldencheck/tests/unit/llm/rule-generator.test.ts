/**
 * Tests for the LLM rule generator's pure rule-application and (de)serialization.
 * (The LLM-call path needs network/API keys and is exercised in integration.)
 */
import { describe, it, expect } from "vitest";
import { TabularData } from "../../../src/core/data.js";
import { Severity } from "../../../src/core/types.js";
import {
  applyRules,
  serializeRules,
  deserializeRules,
  type GeneratedRule,
} from "../../../src/core/llm/rule-generator.js";

function rule(partial: Partial<GeneratedRule>): GeneratedRule {
  return {
    column: "x",
    ruleType: "regex",
    check: "invalid_values",
    description: "test rule",
    params: {},
    ...partial,
  };
}

describe("applyRules", () => {
  it("regex rule flags non-matching string values", () => {
    const data = new TabularData([
      { code: "AB12" },
      { code: "CD34" },
      { code: "bad!" },
      { code: "EF56" },
    ]);
    const findings = applyRules(data, [
      rule({ column: "code", ruleType: "regex", params: { pattern: "^[A-Z]{2}\\d{2}$" } }),
    ]);
    expect(findings.length).toBe(1);
    expect(findings[0]!.severity).toBe(Severity.WARNING);
    expect(findings[0]!.affectedRows).toBe(1);
    expect(findings[0]!.source).toBe("llm");
    expect(findings[0]!.sampleValues).toContain("bad!");
  });

  it("regex rule is suppressed when >=50% fail (likely wrong rule)", () => {
    const data = new TabularData([
      { code: "ok" },
      { code: "BAD" },
      { code: "BAD" },
    ]);
    const findings = applyRules(data, [
      rule({ column: "code", ruleType: "regex", params: { pattern: "^ok$" } }),
    ]);
    expect(findings).toEqual([]);
  });

  it("length rule flags out-of-bounds strings", () => {
    const data = new TabularData([
      { auth: "1234567890" },
      { auth: "1234567890" },
      { auth: "12345" },
      { auth: "1234567890" },
    ]);
    const findings = applyRules(data, [
      rule({ column: "auth", ruleType: "length", check: "format_detection", params: { minLength: 10, maxLength: 10 } }),
    ]);
    expect(findings.length).toBe(1);
    expect(findings[0]!.affectedRows).toBe(1);
    expect(findings[0]!.check).toBe("format_detection");
  });

  it("value_list rule flags invalid values", () => {
    const data = new TabularData([
      { country: "US" },
      { country: "XX" },
      { country: "GB" },
    ]);
    const findings = applyRules(data, [
      rule({ column: "country", ruleType: "value_list", params: { invalidValues: ["XX"] } }),
    ]);
    expect(findings.length).toBe(1);
    expect(findings[0]!.affectedRows).toBe(1);
    expect(findings[0]!.sampleValues).toContain("XX");
  });

  it("cross_column rule emits a finding when related column exists", () => {
    const data = new TabularData([
      { age: 30, dob: "1994-01-01" },
      { age: 31, dob: "1993-01-01" },
    ]);
    const findings = applyRules(data, [
      rule({ column: "age", ruleType: "cross_column", check: "cross_column", params: { relatedColumn: "dob", relationship: "age vs dob" } }),
    ]);
    expect(findings.length).toBe(1);
    expect(findings[0]!.confidence).toBeCloseTo(0.7);
  });

  it("skips rules referencing a missing column", () => {
    const data = new TabularData([{ a: 1 }]);
    const findings = applyRules(data, [rule({ column: "nonexistent", ruleType: "regex", params: { pattern: ".*" } })]);
    expect(findings).toEqual([]);
  });

  it("a broken regex rule does not crash the batch", () => {
    const data = new TabularData([{ a: "x" }, { a: "y" }]);
    const findings = applyRules(data, [
      rule({ column: "a", ruleType: "regex", params: { pattern: "(" } }), // invalid regex
    ]);
    expect(findings).toEqual([]);
  });
});

describe("serializeRules / deserializeRules", () => {
  it("round-trips rules through the snake_case wire format", () => {
    const rules: GeneratedRule[] = [
      rule({
        column: "country",
        ruleType: "value_list",
        check: "invalid_values",
        description: "no XX",
        params: { invalidValues: ["XX"], validValues: ["US", "GB"] },
      }),
    ];
    const json = serializeRules(rules);
    expect(json).toContain("\"invalid_values\"");
    expect(json).toContain("\"rule_type\"");
    const back = deserializeRules(json);
    expect(back.length).toBe(1);
    expect(back[0]!.column).toBe("country");
    expect(back[0]!.ruleType).toBe("value_list");
    expect(back[0]!.params.invalidValues).toEqual(["XX"]);
  });

  it("coerces numeric invalid_values to strings (Python parity)", () => {
    const back = deserializeRules(
      JSON.stringify([{ column: "x", rule_type: "value_list", check: "invalid_values", description: "d", params: { invalid_values: [0, 999] } }]),
    );
    expect(back[0]!.params.invalidValues).toEqual(["0", "999"]);
  });

  it("returns [] for empty or invalid input", () => {
    expect(deserializeRules("")).toEqual([]);
    expect(deserializeRules("not json")).toEqual([]);
    expect(deserializeRules("{}")).toEqual([]);
  });
});
