import { describe, it, expect } from "vitest";
import { TabularData } from "../../../src/core/data.js";
import { Severity } from "../../../src/core/types.js";
import { TypeInferenceProfiler } from "../../../src/core/profilers/type-inference.js";
import { NullabilityProfiler } from "../../../src/core/profilers/nullability.js";
import { UniquenessProfiler } from "../../../src/core/profilers/uniqueness.js";
import { FormatDetectionProfiler } from "../../../src/core/profilers/format-detection.js";
import { RangeDistributionProfiler } from "../../../src/core/profilers/range-distribution.js";
import { CardinalityProfiler } from "../../../src/core/profilers/cardinality.js";
import { PatternConsistencyProfiler, generalize } from "../../../src/core/profilers/pattern-consistency.js";
import { EncodingDetectionProfiler } from "../../../src/core/profilers/encoding-detection.js";
import { SequenceDetectionProfiler } from "../../../src/core/profilers/sequence-detection.js";
import { DriftDetectionProfiler } from "../../../src/core/profilers/drift-detection.js";
import { COLUMN_PROFILERS } from "../../../src/core/profilers/index.js";

// --- Helpers ---

function makeRows(n: number, gen: (i: number) => Record<string, unknown>) {
  return Array.from({ length: n }, (_, i) => gen(i));
}

// --- TypeInferenceProfiler ---

describe("TypeInferenceProfiler", () => {
  const profiler = new TypeInferenceProfiler();

  it("flags string column that is mostly numeric", () => {
    const data = new TabularData(
      makeRows(100, (i) => ({ val: i < 85 ? String(i * 10) : `text_${i}` })),
    );
    const findings = profiler.profile(data, "val", {});
    expect(findings.length).toBeGreaterThan(0);
    expect(findings[0]!.check).toBe("type_inference");
    expect(findings[0]!.severity).toBe(Severity.WARNING);
  });

  it("flags numeric column with identifier-like name", () => {
    const data = new TabularData(
      makeRows(10, (i) => ({ zip_code: 10000 + i })),
    );
    const findings = profiler.profile(data, "zip_code");
    expect(findings.some((f) => f.message.includes("should be string"))).toBe(true);
  });

  it("returns empty for clean string column", () => {
    const data = new TabularData(
      makeRows(50, (i) => ({ name: `person_${i}` })),
    );
    expect(profiler.profile(data, "name")).toEqual([]);
  });
});

// --- NullabilityProfiler ---

describe("NullabilityProfiler", () => {
  const profiler = new NullabilityProfiler();

  it("flags entirely null column as ERROR", () => {
    const data = new TabularData(makeRows(10, () => ({ x: null })));
    const findings = profiler.profile(data, "x");
    expect(findings[0]!.severity).toBe(Severity.ERROR);
  });

  it("flags 0 nulls as likely required", () => {
    const data = new TabularData(makeRows(100, (i) => ({ x: i })));
    const findings = profiler.profile(data, "x");
    expect(findings.some((f) => f.message.includes("likely required"))).toBe(true);
  });

  it("flags high null rate with warning", () => {
    const data = new TabularData(
      makeRows(200, (i) => ({ x: i < 5 ? null : i })),
    );
    const findings = profiler.profile(data, "x");
    expect(findings.some((f) => f.severity === Severity.WARNING)).toBe(true);
  });
});

// --- UniquenessProfiler ---

describe("UniquenessProfiler", () => {
  const profiler = new UniquenessProfiler();

  it("flags 100% unique as likely primary key", () => {
    const data = new TabularData(makeRows(100, (i) => ({ id: i })));
    const findings = profiler.profile(data, "id");
    expect(findings.some((f) => f.message.includes("primary key"))).toBe(true);
  });

  it("flags near-unique identifier with duplicates", () => {
    const rows = makeRows(100, (i) => ({ user_id: i < 98 ? i : 0 }));
    const data = new TabularData(rows);
    const findings = profiler.profile(data, "user_id");
    expect(findings.some((f) => f.severity === Severity.WARNING)).toBe(true);
  });
});

// --- FormatDetectionProfiler ---

describe("FormatDetectionProfiler", () => {
  const profiler = new FormatDetectionProfiler();

  it("detects email format", () => {
    const data = new TabularData(
      makeRows(20, (i) => ({
        email: i < 18 ? `user${i}@example.com` : "not-an-email",
      })),
    );
    const findings = profiler.profile(data, "email");
    expect(findings.some((f) => f.message.includes("email"))).toBe(true);
  });

  it("detects URL format", () => {
    const data = new TabularData(
      makeRows(10, (i) => ({ url: `https://example.com/${i}` })),
    );
    const findings = profiler.profile(data, "url");
    expect(findings.some((f) => f.message.includes("url"))).toBe(true);
  });
});

// --- RangeDistributionProfiler ---

describe("RangeDistributionProfiler", () => {
  const profiler = new RangeDistributionProfiler();

  it("reports range info for numeric columns", () => {
    const data = new TabularData(makeRows(50, (i) => ({ val: i })));
    const findings = profiler.profile(data, "val");
    expect(findings.some((f) => f.message.includes("Range:"))).toBe(true);
  });

  it("detects outliers", () => {
    const rows = makeRows(100, (i) => ({ val: i < 99 ? 50 + Math.random() : 99999 }));
    const data = new TabularData(rows);
    const findings = profiler.profile(data, "val");
    expect(findings.some((f) => f.message.includes("outlier"))).toBe(true);
  });
});

// --- CardinalityProfiler ---

describe("CardinalityProfiler", () => {
  const profiler = new CardinalityProfiler();

  it("flags low cardinality", () => {
    const statuses = ["active", "inactive", "pending"];
    const data = new TabularData(
      makeRows(100, (i) => ({ status: statuses[i % 3] })),
    );
    const findings = profiler.profile(data, "status");
    expect(findings.some((f) => f.check === "cardinality")).toBe(true);
  });

  it("does not flag high cardinality", () => {
    const data = new TabularData(makeRows(100, (i) => ({ name: `person_${i}` })));
    const findings = profiler.profile(data, "name");
    expect(findings).toEqual([]);
  });
});

// --- PatternConsistencyProfiler ---

describe("PatternConsistencyProfiler", () => {
  const profiler = new PatternConsistencyProfiler();

  it("generalize replaces digits and letters", () => {
    expect(generalize("ABC-123")).toBe("LLL-DDD");
    expect(generalize("90210")).toBe("DDDDD");
    expect(generalize("hello@world.com")).toBe("LLLLL@LLLLL.LLL");
  });

  it("flags inconsistent patterns", () => {
    const rows = [
      ...makeRows(95, () => ({ code: "ABC-123" })),
      ...makeRows(5, () => ({ code: "12345" })),
    ];
    const data = new TabularData(rows);
    const findings = profiler.profile(data, "code");
    expect(findings.some((f) => f.check === "pattern_consistency")).toBe(true);
  });

  it("returns empty for consistent patterns", () => {
    const data = new TabularData(makeRows(50, () => ({ code: "ABC-123" })));
    const findings = profiler.profile(data, "code");
    expect(findings).toEqual([]);
  });
});

// --- EncodingDetectionProfiler ---

describe("EncodingDetectionProfiler", () => {
  const profiler = new EncodingDetectionProfiler();

  it("detects zero-width characters", () => {
    const data = new TabularData([
      { name: "Alice" },
      { name: "Bob\u200B" },
      { name: "Charlie" },
    ]);
    const findings = profiler.profile(data, "name");
    expect(findings.some((f) => f.message.includes("zero-width"))).toBe(true);
  });

  it("detects control characters", () => {
    const data = new TabularData([
      { val: "normal" },
      { val: "has\x01control" },
    ]);
    const findings = profiler.profile(data, "val");
    expect(findings.some((f) => f.message.includes("control characters"))).toBe(true);
  });
});

// --- SequenceDetectionProfiler ---

describe("SequenceDetectionProfiler", () => {
  const profiler = new SequenceDetectionProfiler();

  it("detects gaps in sequential column", () => {
    // 1,2,3,5,6,7 → gap at 4
    const data = new TabularData([
      { id: 1 }, { id: 2 }, { id: 3 }, { id: 5 }, { id: 6 }, { id: 7 },
      { id: 8 }, { id: 9 }, { id: 10 }, { id: 11 },
    ]);
    const findings = profiler.profile(data, "id");
    expect(findings.some((f) => f.check === "sequence_detection")).toBe(true);
  });

  it("returns empty for complete sequence", () => {
    const data = new TabularData(makeRows(10, (i) => ({ id: i + 1 })));
    const findings = profiler.profile(data, "id");
    expect(findings).toEqual([]);
  });
});

// --- DriftDetectionProfiler ---

describe("DriftDetectionProfiler", () => {
  const profiler = new DriftDetectionProfiler();

  it("requires minimum rows", () => {
    const data = new TabularData(makeRows(50, (i) => ({ val: i })));
    expect(profiler.profile(data, "val")).toEqual([]);
  });

  it("detects numeric drift", () => {
    const rows = [
      ...makeRows(600, () => ({ val: 10 + Math.random() * 2 })),
      ...makeRows(600, () => ({ val: 100 + Math.random() * 2 })),
    ];
    const data = new TabularData(rows);
    const findings = profiler.profile(data, "val");
    expect(findings.some((f) => f.check === "drift_detection")).toBe(true);
  });
});

// --- COLUMN_PROFILERS registry ---

describe("COLUMN_PROFILERS", () => {
  it("has exactly 10 profilers", () => {
    expect(COLUMN_PROFILERS.length).toBe(10);
  });

  it("all profilers run without error on simple data", () => {
    const data = new TabularData(makeRows(20, (i) => ({
      id: i,
      name: `person_${i}`,
      email: `user${i}@test.com`,
      age: 20 + i,
    })));
    for (const profiler of COLUMN_PROFILERS) {
      for (const col of data.columns) {
        // Should not throw
        const findings = profiler.profile(data, col, {});
        expect(Array.isArray(findings)).toBe(true);
      }
    }
  });
});
