import { describe, it, expect } from "vitest";
import { TabularData } from "../../../src/core/data.js";
import { Severity, makeFinding } from "../../../src/core/types.js";
import { classifyColumns, loadTypeDefs, matchByName } from "../../../src/core/semantic/classifier.js";
import { applySuppression } from "../../../src/core/semantic/suppression.js";
import { BASE_TYPES } from "../../../src/core/semantic/types.js";
import { listAvailableDomains } from "../../../src/core/semantic/domains/index.js";

describe("loadTypeDefs", () => {
  it("loads base types without domain", () => {
    const defs = loadTypeDefs();
    expect(Object.keys(defs)).toContain("email");
    expect(Object.keys(defs)).toContain("identifier");
    expect(Object.keys(defs)).toContain("person_name");
  });

  it("merges domain types over base", () => {
    const defs = loadTypeDefs("healthcare");
    expect(Object.keys(defs)).toContain("npi");
    expect(Object.keys(defs)).toContain("email"); // base still present
  });

  it("merges user types over all", () => {
    const userTypes = { custom: { nameHints: ["custom"], valueSignals: {}, suppress: [] } };
    const defs = loadTypeDefs(null, userTypes);
    expect(Object.keys(defs)).toContain("custom");
  });
});

describe("listAvailableDomains", () => {
  it("returns healthcare, finance, ecommerce", () => {
    const domains = listAvailableDomains();
    expect(domains).toContain("healthcare");
    expect(domains).toContain("finance");
    expect(domains).toContain("ecommerce");
  });
});

describe("matchByName", () => {
  it("matches substring hints", () => {
    const result = matchByName("user_email", BASE_TYPES);
    expect(result).not.toBeNull();
    expect(result!.typeName).toBe("email");
    expect(result!.source).toBe("name");
  });

  it("matches prefix hints (ending with _)", () => {
    // "is_" matches "is_active"
    const result = matchByName("is_active", BASE_TYPES);
    expect(result).not.toBeNull();
    expect(result!.typeName).toBe("boolean");
  });

  it("prefix hints do NOT match as substring", () => {
    // "is_" should NOT match "diagnosis_desc"
    const result = matchByName("diagnosis_desc", BASE_TYPES);
    // Should not be "boolean" — might match something else or be null
    if (result) {
      expect(result.typeName).not.toBe("boolean");
    }
  });

  it("matches suffix hints (starting with _)", () => {
    // "_at" matches "created_at"
    const result = matchByName("created_at", BASE_TYPES);
    expect(result).not.toBeNull();
    expect(result!.typeName).toBe("datetime");
  });

  it("returns null for unrecognized column", () => {
    const result = matchByName("xyzzy_unknown_col", BASE_TYPES);
    expect(result).toBeNull();
  });
});

describe("classifyColumns", () => {
  it("classifies columns by name", () => {
    const data = new TabularData([
      { email: "a@b.com", user_id: 1, name: "Alice" },
      { email: "c@d.com", user_id: 2, name: "Bob" },
    ]);
    const result = classifyColumns(data);
    expect(result["email"]?.typeName).toBe("email");
    expect(result["user_id"]?.typeName).toBe("identifier");
  });

  it("classifies by value when name doesn't match", () => {
    // Use duplicate emails so min_unique_pct < 0.95 (identifier won't match first)
    const emails = ["a@b.com", "c@d.com", "e@f.com", "a@b.com", "c@d.com"];
    const data = new TabularData(
      Array.from({ length: 100 }, (_, i) => ({
        contact: emails[i % emails.length],
      })),
    );
    const result = classifyColumns(data);
    // "contact" doesn't match any name hint, values are emails with duplicates
    // identifier signal fails (unique_pct < 0.95), email signal passes
    expect(result["contact"]?.typeName).toBe("email");
    expect(result["contact"]?.source).toBe("value");
  });
});

describe("applySuppression", () => {
  const defs = loadTypeDefs();

  it("downgrades suppressed WARNING to INFO", () => {
    const columnTypes = { user_email: { typeName: "email", source: "name" as const } };
    const findings = [
      makeFinding({
        severity: Severity.WARNING,
        column: "user_email",
        check: "pattern_consistency",
        message: "Inconsistent pattern",
        confidence: 0.7,
      }),
    ];
    const result = applySuppression(findings, columnTypes, defs);
    expect(result[0]!.severity).toBe(Severity.INFO);
    expect(result[0]!.message).toContain("suppressed");
  });

  it("does NOT suppress INFO findings", () => {
    const columnTypes = { user_email: { typeName: "email", source: "name" as const } };
    const findings = [
      makeFinding({
        severity: Severity.INFO,
        column: "user_email",
        check: "pattern_consistency",
        message: "Info finding",
      }),
    ];
    const result = applySuppression(findings, columnTypes, defs);
    expect(result[0]!.severity).toBe(Severity.INFO);
    expect(result[0]!.message).not.toContain("suppressed");
  });

  it("does NOT suppress LLM findings", () => {
    const columnTypes = { user_email: { typeName: "email", source: "name" as const } };
    const findings = [
      makeFinding({
        severity: Severity.WARNING,
        column: "user_email",
        check: "pattern_consistency",
        message: "LLM finding",
        source: "llm",
        confidence: 0.7,
      }),
    ];
    const result = applySuppression(findings, columnTypes, defs);
    expect(result[0]!.severity).toBe(Severity.WARNING);
  });

  it("does NOT suppress high-confidence findings", () => {
    const columnTypes = { user_email: { typeName: "email", source: "name" as const } };
    const findings = [
      makeFinding({
        severity: Severity.WARNING,
        column: "user_email",
        check: "pattern_consistency",
        message: "High confidence",
        confidence: 0.95,
      }),
    ];
    const result = applySuppression(findings, columnTypes, defs);
    expect(result[0]!.severity).toBe(Severity.WARNING);
  });

  it("preserves geo pattern_consistency for different-length digit patterns", () => {
    const columnTypes = { zip: { typeName: "geo", source: "name" as const } };
    const findings = [
      makeFinding({
        severity: Severity.WARNING,
        column: "zip",
        check: "pattern_consistency",
        message: "Inconsistent",
        confidence: 0.8,
        metadata: { dominant_pattern: "DDDDD", minority_pattern: "DDDDD-DDDD" },
      }),
    ];
    const result = applySuppression(findings, columnTypes, defs);
    // Should NOT be suppressed (real zip format issue)
    expect(result[0]!.severity).toBe(Severity.WARNING);
  });
});
