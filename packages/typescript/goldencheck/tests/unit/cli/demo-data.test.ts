/**
 * Tests for the demo data generator.
 * Mirrors the intent of tests/cli/test_demo.py (the demo produces scannable
 * data with real quality issues) and goldencheck/cli/demo_data.py's schema.
 */
import { describe, it, expect } from "vitest";
import { generateDemoRecords } from "../../../src/core/cli/demo-data.js";
import { TabularData } from "../../../src/core/data.js";
import { scanData } from "../../../src/core/engine/scanner.js";
import { applyConfidenceDowngrade } from "../../../src/core/engine/confidence.js";
import { Severity } from "../../../src/core/types.js";

describe("generateDemoRecords", () => {
  it("produces 200 rows with the expected schema", () => {
    const rows = generateDemoRecords();
    expect(rows.length).toBe(200);
    expect(Object.keys(rows[0]!).sort()).toEqual(
      ["age", "customer_id", "email", "name", "phone", "purchase_amount", "status"].sort(),
    );
  });

  it("is deterministic for a fixed seed", () => {
    const a = generateDemoRecords();
    const b = generateDemoRecords();
    expect(a).toEqual(b);
  });

  it("injects the documented quality issues at fixed indices", () => {
    const rows = generateDemoRecords();
    expect(rows[3]!.email).toBe("not-an-email");
    expect(rows[17]!.email).toBe("also bad");
    expect(rows[42]!.email).toBe("");
    expect(rows[5]!.age).toBe(-3);
    expect(rows[88]!.age).toBe(200);
    expect(rows[120]!.age).toBe(null);
    expect(rows[10]!.phone).toBe("12345");
    expect(rows[30]!.phone).toBe("abc-def-ghij");
    expect(rows[50]!.status).toBe("Active");
    expect(rows[51]!.status).toBe("ACTIVE");
    expect(rows[0]!.purchase_amount).toBe(999999.99);
    expect(rows[15]!.name).toBe(null);
    expect(rows[16]!.name).toBe(null);
    expect(rows[99]!.name).toBe("");
  });

  it("customer_id is sequential and unique", () => {
    const rows = generateDemoRecords();
    expect(rows[0]!.customer_id).toBe(1);
    expect(rows[199]!.customer_id).toBe(200);
  });

  it("scanning the demo data surfaces quality findings", () => {
    const data = new TabularData(generateDemoRecords());
    const result = scanData(data);
    const findings = applyConfidenceDowngrade(result.findings, false);
    expect(findings.length).toBeGreaterThan(0);
    // There should be at least one WARNING/ERROR among the injected issues.
    expect(findings.some((f) => f.severity >= Severity.WARNING)).toBe(true);
  });

  it("honours a custom row count", () => {
    const rows = generateDemoRecords({ rows: 10 });
    expect(rows.length).toBe(10);
    // The outlier injection at index 0 still applies for small datasets.
    expect(rows[0]!.purchase_amount).toBe(999999.99);
  });
});
