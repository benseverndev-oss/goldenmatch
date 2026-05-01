import { describe, it, expect } from "vitest";
import { TabularData } from "../../src/core/data.js";
import { Severity } from "../../src/core/types.js";
import { scanData } from "../../src/core/engine/scanner.js";
import { applyCorroborationBoost, applyConfidenceDowngrade } from "../../src/core/engine/confidence.js";
import { maybeSample } from "../../src/core/engine/sampler.js";
import { makeFinding } from "../../src/core/types.js";

describe("maybeSample", () => {
  it("returns data unchanged if <= maxRows", () => {
    const data = new TabularData([{ a: 1 }, { a: 2 }]);
    expect(maybeSample(data, 100).rowCount).toBe(2);
  });

  it("samples down to maxRows", () => {
    const data = new TabularData(Array.from({ length: 200 }, (_, i) => ({ a: i })));
    const sampled = maybeSample(data, 50);
    expect(sampled.rowCount).toBe(50);
  });
});

describe("applyCorroborationBoost", () => {
  it("boosts +0.1 for 2 distinct checks on same column", () => {
    const findings = [
      makeFinding({ severity: Severity.WARNING, column: "x", check: "nullability", message: "a", confidence: 0.6 }),
      makeFinding({ severity: Severity.WARNING, column: "x", check: "format_detection", message: "b", confidence: 0.7 }),
    ];
    const result = applyCorroborationBoost(findings);
    expect(result[0]!.confidence).toBeCloseTo(0.7, 10); // 0.6 + 0.1
    expect(result[1]!.confidence).toBeCloseTo(0.8, 10); // 0.7 + 0.1
  });

  it("boosts +0.2 for 3+ distinct checks", () => {
    const findings = [
      makeFinding({ severity: Severity.WARNING, column: "x", check: "a", message: "a", confidence: 0.5 }),
      makeFinding({ severity: Severity.ERROR, column: "x", check: "b", message: "b", confidence: 0.6 }),
      makeFinding({ severity: Severity.WARNING, column: "x", check: "c", message: "c", confidence: 0.7 }),
    ];
    const result = applyCorroborationBoost(findings);
    expect(result[0]!.confidence).toBeCloseTo(0.7, 10); // 0.5 + 0.2
    expect(result[2]!.confidence).toBeCloseTo(0.9, 10); // 0.7 + 0.2
  });

  it("caps at 1.0", () => {
    const findings = [
      makeFinding({ severity: Severity.WARNING, column: "x", check: "a", message: "a", confidence: 0.95 }),
      makeFinding({ severity: Severity.WARNING, column: "x", check: "b", message: "b", confidence: 0.95 }),
    ];
    const result = applyCorroborationBoost(findings);
    expect(result[0]!.confidence).toBe(1.0);
  });

  it("does not boost INFO findings", () => {
    const findings = [
      makeFinding({ severity: Severity.INFO, column: "x", check: "a", message: "a", confidence: 0.5 }),
      makeFinding({ severity: Severity.WARNING, column: "x", check: "b", message: "b", confidence: 0.5 }),
    ];
    const result = applyCorroborationBoost(findings);
    expect(result[0]!.confidence).toBe(0.5); // INFO unchanged
  });
});

describe("applyConfidenceDowngrade", () => {
  it("downgrades low-confidence to INFO when llmBoost=false", () => {
    const findings = [
      makeFinding({ severity: Severity.WARNING, column: "x", check: "test", message: "msg", confidence: 0.3 }),
    ];
    const result = applyConfidenceDowngrade(findings, false);
    expect(result[0]!.severity).toBe(Severity.INFO);
    expect(result[0]!.message).toContain("low confidence");
  });

  it("does not downgrade when llmBoost=true", () => {
    const findings = [
      makeFinding({ severity: Severity.WARNING, column: "x", check: "test", message: "msg", confidence: 0.3 }),
    ];
    const result = applyConfidenceDowngrade(findings, true);
    expect(result[0]!.severity).toBe(Severity.WARNING);
  });

  it("does not downgrade high-confidence findings", () => {
    const findings = [
      makeFinding({ severity: Severity.WARNING, column: "x", check: "test", message: "msg", confidence: 0.8 }),
    ];
    const result = applyConfidenceDowngrade(findings, false);
    expect(result[0]!.severity).toBe(Severity.WARNING);
  });
});

describe("scanData", () => {
  it("scans simple data and returns findings + profile", () => {
    const data = new TabularData(
      Array.from({ length: 100 }, (_, i) => ({
        id: i,
        name: `person_${i}`,
        email: i < 95 ? `user${i}@test.com` : "invalid-email",
        status: ["active", "inactive", "pending"][i % 3],
      })),
    );

    const result = scanData(data);
    expect(result.findings).toBeDefined();
    expect(result.profile).toBeDefined();
    expect(result.profile.rowCount).toBe(100);
    expect(result.profile.columnCount).toBe(4);
    expect(result.profile.columns.length).toBe(4);
    // Should find some issues (email format, cardinality, etc.)
    expect(result.findings.length).toBeGreaterThan(0);
  });

  it("sorts findings by severity descending", () => {
    const data = new TabularData(
      Array.from({ length: 100 }, (_, i) => ({
        id: i,
        email: i < 80 ? `user${i}@test.com` : "bad",
      })),
    );
    const result = scanData(data);
    for (let i = 1; i < result.findings.length; i++) {
      expect(result.findings[i]!.severity).toBeLessThanOrEqual(result.findings[i - 1]!.severity);
    }
  });

  it("supports domain option", () => {
    const data = new TabularData([
      { npi: "1234567890", patient_name: "Alice" },
      { npi: "9876543210", patient_name: "Bob" },
    ]);
    const result = scanData(data, { domain: "healthcare" });
    expect(result.findings).toBeDefined();
  });

  it("returns sample when returnSample=true", () => {
    const data = new TabularData([{ a: 1 }, { a: 2 }]);
    const result = scanData(data, { returnSample: true });
    expect("sample" in result).toBe(true);
  });
});
