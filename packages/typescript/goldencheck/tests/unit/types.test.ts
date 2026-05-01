import { describe, it, expect } from "vitest";
import {
  Severity,
  severityLabel,
  makeFinding,
  replaceFinding,
  makeColumnProfile,
  healthScore,
  defaultConfig,
  defaultSettings,
} from "../../src/core/types.js";

describe("Severity", () => {
  it("has correct numeric values matching Python IntEnum", () => {
    expect(Severity.INFO).toBe(1);
    expect(Severity.WARNING).toBe(2);
    expect(Severity.ERROR).toBe(3);
  });

  it("labels match Python repr", () => {
    expect(severityLabel(Severity.ERROR)).toBe("ERROR");
    expect(severityLabel(Severity.WARNING)).toBe("WARNING");
    expect(severityLabel(Severity.INFO)).toBe("INFO");
  });
});

describe("makeFinding", () => {
  it("creates a Finding with defaults", () => {
    const f = makeFinding({
      severity: Severity.WARNING,
      column: "email",
      check: "format_detection",
      message: "Invalid emails found",
    });
    expect(f.severity).toBe(Severity.WARNING);
    expect(f.column).toBe("email");
    expect(f.check).toBe("format_detection");
    expect(f.message).toBe("Invalid emails found");
    expect(f.affectedRows).toBe(0);
    expect(f.sampleValues).toEqual([]);
    expect(f.suggestion).toBeNull();
    expect(f.pinned).toBe(false);
    expect(f.source).toBeNull();
    expect(f.confidence).toBe(1.0);
    expect(f.metadata).toEqual({});
  });

  it("allows overriding defaults", () => {
    const f = makeFinding({
      severity: Severity.ERROR,
      column: "age",
      check: "range_distribution",
      message: "Out of range",
      affectedRows: 5,
      confidence: 0.85,
      source: "llm",
    });
    expect(f.affectedRows).toBe(5);
    expect(f.confidence).toBe(0.85);
    expect(f.source).toBe("llm");
  });
});

describe("replaceFinding", () => {
  it("returns a new Finding with overrides", () => {
    const original = makeFinding({
      severity: Severity.WARNING,
      column: "x",
      check: "test",
      message: "msg",
      confidence: 0.6,
    });
    const updated = replaceFinding(original, { confidence: 0.9, severity: Severity.INFO });
    expect(updated.confidence).toBe(0.9);
    expect(updated.severity).toBe(Severity.INFO);
    // Original unchanged
    expect(original.confidence).toBe(0.6);
    expect(original.severity).toBe(Severity.WARNING);
  });
});

describe("makeColumnProfile", () => {
  it("creates a profile with defaults", () => {
    const cp = makeColumnProfile({
      name: "email",
      inferredType: "string",
      nullCount: 3,
      nullPct: 0.03,
      uniqueCount: 95,
      uniquePct: 0.95,
      rowCount: 100,
    });
    expect(cp.name).toBe("email");
    expect(cp.minValue).toBeNull();
    expect(cp.topValues).toEqual([]);
  });
});

describe("healthScore", () => {
  it("returns A for no issues", () => {
    const result = healthScore(undefined, 0, 0);
    expect(result.grade).toBe("A");
    expect(result.points).toBe(100);
  });

  it("deducts 10 per error, 3 per warning", () => {
    const result = healthScore(undefined, 2, 3);
    expect(result.points).toBe(100 - 20 - 9);
    expect(result.grade).toBe("C");
  });

  it("caps at 0", () => {
    const result = healthScore(undefined, 20, 0);
    expect(result.points).toBe(0);
    expect(result.grade).toBe("F");
  });

  it("uses per-column cap of 20 when findingsByColumn provided", () => {
    const result = healthScore({
      col1: { errors: 5, warnings: 0 }, // 50 → capped at 20
      col2: { errors: 0, warnings: 2 }, // 6
    });
    expect(result.points).toBe(100 - 20 - 6); // 74
    expect(result.grade).toBe("C"); // 74 is in C range (70-79)
  });

  it("grades boundaries match Python", () => {
    expect(healthScore(undefined, 1, 0).grade).toBe("A"); // 90
    expect(healthScore(undefined, 1, 1).grade).toBe("B"); // 87
    expect(healthScore(undefined, 2, 1).grade).toBe("C"); // 77
    expect(healthScore(undefined, 3, 1).grade).toBe("D"); // 67
    expect(healthScore(undefined, 5, 0).grade).toBe("F"); // 50
  });
});

describe("defaultConfig", () => {
  it("creates config matching Python defaults", () => {
    const cfg = defaultConfig();
    expect(cfg.version).toBe(1);
    expect(cfg.settings.sampleSize).toBe(100_000);
    expect(cfg.settings.failOn).toBe("error");
    expect(cfg.columns).toEqual({});
    expect(cfg.relations).toEqual([]);
    expect(cfg.ignore).toEqual([]);
  });
});

describe("defaultSettings", () => {
  it("matches Python Settings() defaults", () => {
    const s = defaultSettings();
    expect(s.sampleSize).toBe(100_000);
    expect(s.severityThreshold).toBe("warning");
    expect(s.failOn).toBe("error");
  });
});
