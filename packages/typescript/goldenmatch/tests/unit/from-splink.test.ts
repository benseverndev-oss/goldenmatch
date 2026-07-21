import { describe, it, expect } from "vitest";
import {
  recognizeLevel,
  ConversionReport,
  SplinkConversionError,
  convertComparison,
  convertBlocking,
  detectTrained,
  importEm,
  convertScalars,
  fromSplink,
  type RecognizedLevel,
} from "../../src/core/config/from-splink.js";
import { getMatchkeys } from "../../src/core/index.js";
import type { MatchkeyField, ProbabilisticMatchkey } from "../../src/core/types.js";

// ---------------------------------------------------------------------------
// Fixtures (mirroring the Python tests' helper functions)
// ---------------------------------------------------------------------------

function jwComparison(): Record<string, unknown> {
  return {
    output_column_name: "first_name",
    comparison_levels: [
      { sql_condition: '"first_name_l" IS NULL OR "first_name_r" IS NULL', is_null_level: true },
      { sql_condition: '"first_name_l" = "first_name_r"' },
      { sql_condition: 'jaro_winkler_similarity("first_name_l", "first_name_r") >= 0.92' },
      { sql_condition: 'jaro_winkler_similarity("first_name_l", "first_name_r") >= 0.88' },
      { sql_condition: "ELSE" },
    ],
  };
}

function exactOnlyComparison(column = "surname"): Record<string, unknown> {
  return {
    output_column_name: column,
    comparison_levels: [
      { sql_condition: `"${column}_l" IS NULL OR "${column}_r" IS NULL`, is_null_level: true },
      { sql_condition: `"${column}_l" = "${column}_r"` },
      { sql_condition: "ELSE" },
    ],
  };
}

function trainedJwComparison(): Record<string, unknown> {
  return {
    output_column_name: "first_name",
    comparison_levels: [
      { sql_condition: '"first_name_l" IS NULL OR "first_name_r" IS NULL', is_null_level: true },
      { sql_condition: '"first_name_l" = "first_name_r"', m_probability: 0.5, u_probability: 0.02 },
      {
        sql_condition: 'jaro_winkler_similarity("first_name_l", "first_name_r") >= 0.92',
        m_probability: 0.3,
        u_probability: 0.08,
      },
      {
        sql_condition: 'jaro_winkler_similarity("first_name_l", "first_name_r") >= 0.88',
        m_probability: 0.15,
        u_probability: 0.2,
      },
      { sql_condition: "ELSE", m_probability: 0.05, u_probability: 0.7 },
    ],
  };
}

function trainedSettings(comparisons: unknown[]): Record<string, unknown> {
  return { comparisons, probability_two_random_records_match: 0.0002 };
}

function fullSettings(opts?: { comparisons?: unknown[]; blockingRules?: unknown[] }): Record<string, unknown> {
  return {
    comparisons: opts?.comparisons ?? [jwComparison(), exactOnlyComparison("surname")],
    blocking_rules_to_generate_predictions: opts?.blockingRules ?? [
      'l."first_name" = r."first_name"',
      'l."surname" = r."surname"',
    ],
  };
}

// ---------------------------------------------------------------------------
// test_from_splink_recognizers.py -> recognizeLevel
// ---------------------------------------------------------------------------

describe("recognizeLevel", () => {
  it.each([
    '"first_name_l" IS NULL OR "first_name_r" IS NULL',
    "first_name_l IS NULL OR first_name_r IS NULL",
    '"first_name_l"   is   null   or   "first_name_r"   is   null',
  ])("recognizes a null level: %s", (sql) => {
    expect(recognizeLevel(sql)).toEqual({
      kind: "null",
      column: "first_name",
      simThreshold: null,
      approx: false,
    } satisfies RecognizedLevel);
  });

  it("is_null_level flag forces null regardless of sql", () => {
    const result = recognizeLevel('"amount_l" > "amount_r"', true);
    expect(result).not.toBeNull();
    expect(result?.kind).toBe("null");
  });

  it.each([
    '"first_name_l" = "first_name_r"',
    "first_name_l = first_name_r",
    '"first_name_l"    =    "first_name_r"',
  ])("recognizes an exact level: %s", (sql) => {
    expect(recognizeLevel(sql)).toEqual({
      kind: "exact",
      column: "first_name",
      simThreshold: 1.0,
      approx: false,
    } satisfies RecognizedLevel);
  });

  it.each([
    'jaro_winkler_similarity("first_name_l", "first_name_r") >= 0.92',
    "JARO_WINKLER_SIMILARITY(first_name_l, first_name_r) >= 0.92",
    '  jaro_winkler_similarity(  "first_name_l" ,  "first_name_r"  )   >=   0.92  ',
  ])("recognizes a jaro_winkler_similarity level: %s", (sql) => {
    expect(recognizeLevel(sql)).toEqual({
      kind: "jaro_winkler",
      column: "first_name",
      simThreshold: 0.92,
      approx: false,
    } satisfies RecognizedLevel);
  });

  it("handles a leading-dot float threshold", () => {
    const result = recognizeLevel('jaro_winkler_similarity("first_name_l", "first_name_r") >= .92');
    expect(result).toEqual({ kind: "jaro_winkler", column: "first_name", simThreshold: 0.92, approx: false });
  });

  it("rejects strict greater-than", () => {
    expect(recognizeLevel('jaro_winkler_similarity("first_name_l", "first_name_r") > 0.92')).toBeNull();
  });

  it.each(['jaro_winkler("a_l","a_r") >= 0.9', "JARO_WINKLER(a_l, a_r) >= 0.9"])(
    "recognizes the Spark-dialect jaro_winkler alias: %s",
    (sql) => {
      expect(recognizeLevel(sql)).toEqual({ kind: "jaro_winkler", column: "a", simThreshold: 0.9, approx: false });
    },
  );

  it("approximates jaro_similarity as jaro_winkler", () => {
    const result = recognizeLevel('jaro_similarity("x_l", "x_r") >= 0.9');
    expect(result).toEqual({ kind: "jaro_winkler", column: "x", simThreshold: 0.9, approx: true });
  });

  it("recognizes levenshtein as an approximate similarity", () => {
    const result = recognizeLevel('levenshtein("dob_l", "dob_r") <= 1');
    expect(result).not.toBeNull();
    expect(result?.kind).toBe("levenshtein");
    expect(result?.column).toBe("dob");
    expect(result?.simThreshold).toBeCloseTo(1 - 1 / 10, 9);
    expect(result?.approx).toBe(true);
  });

  it("recognizes damerau_levenshtein", () => {
    const result = recognizeLevel('damerau_levenshtein("dob_l", "dob_r") <= 2');
    expect(result).not.toBeNull();
    expect(result?.kind).toBe("levenshtein");
    expect(result?.simThreshold).toBeCloseTo(1 - 2 / 10, 9);
    expect(result?.approx).toBe(true);
  });

  it("recognizes jaccard", () => {
    expect(recognizeLevel('jaccard("email_l", "email_r") >= 0.9')).toEqual({
      kind: "jaccard",
      column: "email",
      simThreshold: 0.9,
      approx: false,
    });
  });

  it("recognizes ELSE (case-insensitive)", () => {
    expect(recognizeLevel("ELSE")).toEqual({ kind: "else", column: null, simThreshold: null, approx: false });
    expect(recognizeLevel("else")).toEqual({ kind: "else", column: null, simThreshold: null, approx: false });
  });

  it("returns null for cross-column comparisons", () => {
    expect(recognizeLevel('"first_name_l" = "surname_r" AND "surname_l" = "first_name_r"')).toBeNull();
  });

  it("returns null for mismatched columns in a function call", () => {
    expect(recognizeLevel('jaro_winkler_similarity("a_l", "b_r") >= 0.9')).toBeNull();
  });

  it("returns null for arbitrary SQL", () => {
    expect(recognizeLevel('abs("amount_l" - "amount_r") < 5')).toBeNull();
  });

  it("clamps the levenshtein distance floor to zero", () => {
    const result = recognizeLevel('levenshtein("dob_l", "dob_r") <= 15');
    expect(result).not.toBeNull();
    expect(result?.kind).toBe("levenshtein");
    expect(result?.simThreshold).toBe(0.0);
    expect(result?.approx).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// test_from_splink_report.py -> ConversionReport
// ---------------------------------------------------------------------------

describe("ConversionReport", () => {
  it("filters by severity", () => {
    const r = new ConversionReport();
    r.info("settings.sql_dialect", "ignored (engine infra)", null);
    r.warn("comparisons[0].levels[2]", "unrecognized SQL, level dropped", null);
    expect(r.findings.length).toBe(2);
    expect(r.hasWarnings).toBe(true);
    expect(r.hasErrors).toBe(false);
    expect(r.summary().toLowerCase()).toContain("warning");
  });

  it("tracks error findings", () => {
    const r = new ConversionReport();
    r.error("blocking_rules", "no blocking rule could be converted", null);
    expect(r.hasErrors).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// test_from_splink_comparisons.py -> convertComparison
// ---------------------------------------------------------------------------

describe("convertComparison", () => {
  it("converts a JW comparison with exact + two bands to a 4-level field", () => {
    const report = new ConversionReport();
    const field = convertComparison(jwComparison(), 0, report);

    expect(field).not.toBeNull();
    expect(field?.field).toBe("first_name");
    expect(field?.scorer).toBe("jaro_winkler");
    expect(field?.levels).toBe(4);
    expect(field?.levelThresholds).toEqual([1.0, 0.92, 0.88]);

    const infos = report.findings.filter((f) => f.severity === "info");
    expect(infos.some((f) => f.message.toLowerCase().includes("null"))).toBe(true);
  });

  it("converts a pure-exact comparison to a legacy 2-level field", () => {
    const report = new ConversionReport();
    const field = convertComparison(exactOnlyComparison(), 0, report);

    expect(field).not.toBeNull();
    expect(field?.field).toBe("surname");
    expect(field?.scorer).toBe("exact");
    expect(field?.levels).toBe(2);
    expect(field?.levelThresholds).toBeUndefined();
  });

  it("converts exact + one JW band to a 3-level field", () => {
    const comp = {
      output_column_name: "first_name",
      comparison_levels: [
        { sql_condition: '"first_name_l" IS NULL OR "first_name_r" IS NULL', is_null_level: true },
        { sql_condition: '"first_name_l" = "first_name_r"' },
        { sql_condition: 'jaro_winkler_similarity("first_name_l", "first_name_r") >= 0.92' },
        { sql_condition: "ELSE" },
      ],
    };
    const report = new ConversionReport();
    const field = convertComparison(comp, 0, report);

    expect(field).not.toBeNull();
    expect(field?.scorer).toBe("jaro_winkler");
    expect(field?.levels).toBe(3);
    expect(field?.levelThresholds).toEqual([1.0, 0.92]);
  });

  it("drops a comparison with mixed comparator families", () => {
    const comp = {
      output_column_name: "dob",
      comparison_levels: [
        { sql_condition: '"dob_l" IS NULL OR "dob_r" IS NULL', is_null_level: true },
        { sql_condition: 'jaro_winkler_similarity("dob_l", "dob_r") >= 0.92' },
        { sql_condition: 'levenshtein("dob_l", "dob_r") <= 1' },
        { sql_condition: "ELSE" },
      ],
    };
    const report = new ConversionReport();
    const field = convertComparison(comp, 0, report);

    expect(field).toBeNull();
    expect(report.hasWarnings).toBe(true);
    expect(report.findings.some((f) => f.message.includes("mixed comparator families"))).toBe(true);
  });

  it("re-derives thresholds when one level is unrecognized and dropped", () => {
    const comp = {
      output_column_name: "first_name",
      comparison_levels: [
        { sql_condition: '"first_name_l" IS NULL OR "first_name_r" IS NULL', is_null_level: true },
        { sql_condition: 'jaro_winkler_similarity("first_name_l", "first_name_r") >= 0.92' },
        // cross-column condition between two JW bands, unrecognized
        { sql_condition: 'jaro_winkler_similarity("first_name_l", "surname_r") >= 0.85' },
        { sql_condition: 'jaro_winkler_similarity("first_name_l", "first_name_r") >= 0.80' },
        { sql_condition: "ELSE" },
      ],
    };
    const report = new ConversionReport();
    const field = convertComparison(comp, 0, report);

    expect(field).not.toBeNull();
    expect(field?.scorer).toBe("jaro_winkler");
    expect(field?.levelThresholds).toEqual([0.92, 0.8]);
    expect(field?.levels).toBe(3);
    expect(report.findings.some((f) => f.message.includes("unrecognized sql_condition"))).toBe(true);
  });

  it("drops a comparison with inconsistent columns", () => {
    const comp = {
      output_column_name: "first_name",
      comparison_levels: [
        { sql_condition: '"first_name_l" IS NULL OR "first_name_r" IS NULL', is_null_level: true },
        { sql_condition: 'jaro_winkler_similarity("first_name_l", "first_name_r") >= 0.92' },
        { sql_condition: 'jaro_winkler_similarity("surname_l", "surname_r") >= 0.88' },
        { sql_condition: "ELSE" },
      ],
    };
    const report = new ConversionReport();
    const field = convertComparison(comp, 0, report);

    expect(field).toBeNull();
    expect(report.findings.some((f) => f.message.includes("inconsistent columns"))).toBe(true);
  });

  it("sets tfAdjustment when tf_adjustment_column matches the field column", () => {
    const comp = {
      output_column_name: "first_name",
      comparison_levels: [
        { sql_condition: '"first_name_l" IS NULL OR "first_name_r" IS NULL', is_null_level: true },
        { sql_condition: '"first_name_l" = "first_name_r"', tf_adjustment_column: "first_name" },
        { sql_condition: "ELSE" },
      ],
    };
    const report = new ConversionReport();
    const field = convertComparison(comp, 0, report);

    expect(field).not.toBeNull();
    expect(field?.tfAdjustment).toBe(true);
    expect(report.hasWarnings).toBe(false);
  });

  it("drops tf_adjustment_weight != 1 with a warning", () => {
    const comp = {
      output_column_name: "first_name",
      comparison_levels: [
        { sql_condition: '"first_name_l" IS NULL OR "first_name_r" IS NULL', is_null_level: true },
        {
          sql_condition: '"first_name_l" = "first_name_r"',
          tf_adjustment_column: "first_name",
          tf_adjustment_weight: 0.5,
        },
        { sql_condition: "ELSE" },
      ],
    };
    const report = new ConversionReport();
    const field = convertComparison(comp, 0, report);

    expect(field).not.toBeNull();
    expect(field?.tfAdjustment).toBe(true);
    expect(report.findings.some((f) => f.message.includes("tf_adjustment_weight"))).toBe(true);
  });

  it("dedupes duplicate thresholds (spark-dialect alias)", () => {
    const comp = {
      output_column_name: "first_name",
      comparison_levels: [
        { sql_condition: '"first_name_l" IS NULL OR "first_name_r" IS NULL', is_null_level: true },
        { sql_condition: 'jaro_winkler_similarity("first_name_l", "first_name_r") >= 0.92' },
        { sql_condition: 'jaro_winkler("first_name_l", "first_name_r") >= 0.92' },
        { sql_condition: "ELSE" },
      ],
    };
    const report = new ConversionReport();
    const field = convertComparison(comp, 0, report);

    expect(field).not.toBeNull();
    expect(field?.levels).toBe(2);
    expect(field?.partialThreshold).toBe(0.92);
    expect(field?.levelThresholds).toBeUndefined();
  });

  it("drops an out-of-range levenshtein band among surviving bands", () => {
    const comp = {
      output_column_name: "address",
      comparison_levels: [
        { sql_condition: '"address_l" IS NULL OR "address_r" IS NULL', is_null_level: true },
        { sql_condition: 'levenshtein("address_l", "address_r") <= 10' },
        { sql_condition: 'levenshtein("address_l", "address_r") <= 2' },
        { sql_condition: "ELSE" },
      ],
    };
    const report = new ConversionReport();
    const field = convertComparison(comp, 0, report);

    expect(field).not.toBeNull();
    expect(field?.scorer).toBe("levenshtein");
    expect(field?.levels).toBe(2);
    expect(field?.partialThreshold).toBeCloseTo(0.8, 9);
    expect(report.findings.some((f) => f.message.includes("out of range") && f.message.includes("<= 10"))).toBe(
      true,
    );
  });

  it("returns null when the only band is out of range", () => {
    const comp = {
      output_column_name: "address",
      comparison_levels: [
        { sql_condition: '"address_l" IS NULL OR "address_r" IS NULL', is_null_level: true },
        { sql_condition: 'levenshtein("address_l", "address_r") <= 10' },
        { sql_condition: "ELSE" },
      ],
    };
    const report = new ConversionReport();
    const field = convertComparison(comp, 0, report);

    expect(field).toBeNull();
    expect(report.findings.some((f) => f.message.includes("out of range"))).toBe(true);
    expect(report.findings.some((f) => f.message.includes("no usable agree levels"))).toBe(true);
  });

  it("drops an out-of-range jw threshold (>= 1.5)", () => {
    const comp = {
      output_column_name: "first_name",
      comparison_levels: [
        { sql_condition: 'jaro_winkler_similarity("first_name_l", "first_name_r") >= 1.5' },
        { sql_condition: 'jaro_winkler_similarity("first_name_l", "first_name_r") >= 0.9' },
        { sql_condition: "ELSE" },
      ],
    };
    const report = new ConversionReport();
    const field = convertComparison(comp, 0, report);

    expect(field).not.toBeNull();
    expect(field?.levels).toBe(2);
    expect(field?.partialThreshold).toBe(0.9);
    expect(report.findings.some((f) => f.message.includes("out of range") && f.message.includes("1.5"))).toBe(true);
  });

  it("includes the formula + source in approx warnings", () => {
    const comp = {
      output_column_name: "dob",
      comparison_levels: [{ sql_condition: 'levenshtein("dob_l", "dob_r") <= 2' }, { sql_condition: "ELSE" }],
    };
    const report = new ConversionReport();
    convertComparison(comp, 0, report);
    const levWarns = report.findings.filter((f) => f.message.includes("approximate mapping"));
    expect(levWarns.length).toBe(1);
    expect(levWarns[0]!.message).toContain("distance <= 2");
    expect(levWarns[0]!.message).toContain("sim = 1 - distance/10");
    expect(levWarns[0]!.message).toContain('levenshtein("dob_l", "dob_r") <= 2');

    const compJaro = {
      output_column_name: "x",
      comparison_levels: [{ sql_condition: 'jaro_similarity("x_l", "x_r") >= 0.9' }, { sql_condition: "ELSE" }],
    };
    const report2 = new ConversionReport();
    convertComparison(compJaro, 0, report2);
    const jaroWarns = report2.findings.filter((f) => f.message.includes("approximate mapping"));
    expect(jaroWarns.length).toBe(1);
    expect(jaroWarns[0]!.message).toContain("jaro_similarity");
    expect(jaroWarns[0]!.message).toContain("jaro_winkler");
  });

  it("returns null when all levels are unrecognized", () => {
    const comp = {
      output_column_name: "amount",
      comparison_levels: [
        { sql_condition: 'abs("amount_l" - "amount_r") < 5' },
        { sql_condition: '"amount_l" > "amount_r"' },
      ],
    };
    const report = new ConversionReport();
    const field = convertComparison(comp, 0, report);

    expect(field).toBeNull();
    expect(report.hasWarnings).toBe(true);
    expect(report.findings.some((f) => f.message.includes("unrecognized sql_condition"))).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// test_from_splink_blocking.py -> convertBlocking
// ---------------------------------------------------------------------------

describe("convertBlocking", () => {
  it("converts a single equality rule to static", () => {
    const report = new ConversionReport();
    const config = convertBlocking(['l."postcode" = r."postcode"'], report);

    expect(config).not.toBeNull();
    expect(config?.strategy).toBe("static");
    expect(config?.keys.length).toBe(1);
    expect(config?.keys[0]!.fields).toEqual(["postcode"]);
    expect(config?.keys[0]!.transforms).toEqual([]);
    expect(report.hasWarnings).toBe(false);
    expect(report.hasErrors).toBe(false);
  });

  it("accepts bare unquoted columns", () => {
    const report = new ConversionReport();
    const config = convertBlocking(["l.postcode = r.postcode"], report);

    expect(config).not.toBeNull();
    expect(config?.strategy).toBe("static");
    expect(config?.keys[0]!.fields).toEqual(["postcode"]);
    expect(report.hasWarnings).toBe(false);
  });

  it("maps a mixed equality+SUBSTR conjunction to per-field transforms (#1832)", () => {
    // Pre-#1832 this widened the SUBSTR onto the plain-equality field and warned.
    // Now each field derives its OWN block-key component via fieldTransforms, so
    // the mapping is exact (no widening, no precision loss) and info-only.
    const rule = 'l."surname" = r."surname" AND SUBSTR(l."dob", 1, 4) = SUBSTR(r."dob", 1, 4)';
    const report = new ConversionReport();
    const config = convertBlocking([rule], report);

    expect(config).not.toBeNull();
    expect(config?.strategy).toBe("static");
    expect(config?.keys.length).toBe(1);
    const key = config!.keys[0]!;
    expect(key.fields).toEqual(["surname", "dob"]);
    // Key-level transforms empty; per-field chains carry the exact mapping.
    expect(key.transforms).toEqual([]);
    expect(key.fieldTransforms).toEqual({ surname: [], dob: ["substring:0:4"] });
    expect(report.hasWarnings).toBe(false);
    expect(report.findings.filter((f) => f.severity === "info").length).toBe(1);
  });

  it("drops a rule with conflicting SUBSTR offsets on the SAME field (#1832)", () => {
    const rule = 'SUBSTR(l."dob", 1, 4) = SUBSTR(r."dob", 1, 4) AND SUBSTR(l."dob", 1, 6) = SUBSTR(r."dob", 1, 6)';
    const report = new ConversionReport();
    const config = convertBlocking([rule], report);

    expect(config).toBeNull();
    const warnings = report.findings.filter((f) => f.severity === "warning");
    expect(warnings.length).toBe(1);
    expect(warnings[0]!.message).toContain("conflicting SUBSTR offsets on field dob");
  });

  it("treats a pure SUBSTR rule as info-only", () => {
    const rule = 'SUBSTR(l."dob", 1, 4) = SUBSTR(r."dob", 1, 4)';
    const report = new ConversionReport();
    const config = convertBlocking([rule], report);

    expect(config).not.toBeNull();
    expect(config?.keys[0]!.fields).toEqual(["dob"]);
    expect(config?.keys[0]!.transforms).toEqual(["substring:0:4"]);
    expect(report.hasWarnings).toBe(false);
    expect(report.findings.filter((f) => f.severity === "info").length).toBe(1);
  });

  it("treats a pure equality conjunction as info-only", () => {
    const rule = 'l."surname" = r."surname" AND l."city" = r."city"';
    const report = new ConversionReport();
    const config = convertBlocking([rule], report);

    expect(config).not.toBeNull();
    expect(config?.keys[0]!.fields).toEqual(["surname", "city"]);
    expect(config?.keys[0]!.transforms).toEqual([]);
    expect(report.hasWarnings).toBe(false);
    expect(report.findings.filter((f) => f.severity === "info").length).toBe(1);
  });

  it("produces multi_pass for two rules with keys === passes", () => {
    const rules = ['l."postcode" = r."postcode"', 'l."surname" = r."surname"'];
    const report = new ConversionReport();
    const config = convertBlocking(rules, report);

    expect(config).not.toBeNull();
    expect(config?.strategy).toBe("multi_pass");
    expect(config?.keys.length).toBe(2);
    expect(config?.keys[0]!.fields).toEqual(["postcode"]);
    expect(config?.keys[1]!.fields).toEqual(["surname"]);
    expect(config?.passes).toBe(config?.keys);
  });

  it("keeps a single rule as static, not multi_pass", () => {
    const report = new ConversionReport();
    const config = convertBlocking(['l."postcode" = r."postcode"'], report);
    expect(config?.strategy).toBe("static");
    expect(config?.passes).toBeUndefined();
  });

  it("handles the Splink 4 dict form the same as a string", () => {
    const rule = { blocking_rule: 'l."postcode" = r."postcode"', sql_dialect: "duckdb" };
    const report = new ConversionReport();
    const config = convertBlocking([rule], report);

    expect(config).not.toBeNull();
    expect(config?.strategy).toBe("static");
    expect(config?.keys[0]!.fields).toEqual(["postcode"]);
    expect(report.hasWarnings).toBe(false);
  });

  it("drops an arithmetic rule with a warning and errors overall", () => {
    const report = new ConversionReport();
    const config = convertBlocking(["l.amount / r.amount > 0.7"], report);

    expect(config).toBeNull();
    expect(report.hasErrors).toBe(true);
    const warnings = report.findings.filter((f) => f.severity === "warning");
    expect(warnings.length).toBe(1);
    expect(warnings[0]!.message).toContain("amount");
  });

  it("drops an OR rule with a warning", () => {
    const rule = 'l."postcode" = r."postcode" OR l."dob" = r."dob"';
    const report = new ConversionReport();
    const config = convertBlocking([rule], report);

    expect(config).toBeNull();
    expect(report.findings.filter((f) => f.severity === "warning").length).toBe(1);
  });

  it("converts a paren-wrapped rule", () => {
    const rule = '(l."postcode" = r."postcode")';
    const report = new ConversionReport();
    const config = convertBlocking([rule], report);

    expect(config).not.toBeNull();
    expect(config?.keys[0]!.fields).toEqual(["postcode"]);
    expect(report.hasWarnings).toBe(false);
  });

  it("returns null and errors when all rules are dropped", () => {
    const rules = ["l.amount / r.amount > 0.7", '(l."x" = r."x") OR (l."y" = r."y")'];
    const report = new ConversionReport();
    const config = convertBlocking(rules, report);

    expect(config).toBeNull();
    expect(report.hasErrors).toBe(true);
    const errors = report.findings.filter((f) => f.severity === "error");
    expect(errors.length).toBe(1);
    expect(errors[0]!.splinkPath).toBe("blocking_rules");
  });

  it("drops a dict rule missing the blocking_rule key", () => {
    const rule = { sql_dialect: "duckdb" };
    const report = new ConversionReport();
    const config = convertBlocking([rule], report);

    expect(config).toBeNull();
    const warnings = report.findings.filter((f) => f.severity === "warning");
    expect(warnings.length).toBe(1);
    expect(warnings[0]!.message).toContain("not a SQL string");
  });

  it("drops a null rule", () => {
    const report = new ConversionReport();
    const config = convertBlocking([null], report);

    expect(config).toBeNull();
    const warnings = report.findings.filter((f) => f.severity === "warning");
    expect(warnings.length).toBe(1);
    expect(warnings[0]!.message).toContain("not a SQL string");
  });

  it("maps different-field SUBSTR offsets to per-field transforms (#1832)", () => {
    // Pre-#1832 different SUBSTR offsets across fields were unrepresentable
    // (one key-level chain) so the whole rule was dropped. Per-field chains now
    // carry each offset exactly.
    const rule = "SUBSTR(l.a, 1, 4) = SUBSTR(r.a, 1, 4) AND SUBSTR(l.b, 1, 2) = SUBSTR(r.b, 1, 2)";
    const report = new ConversionReport();
    const config = convertBlocking([rule], report);

    expect(config).not.toBeNull();
    const key = config!.keys[0]!;
    expect(key.fields).toEqual(["a", "b"]);
    expect(key.transforms).toEqual([]);
    expect(key.fieldTransforms).toEqual({ a: ["substring:0:4"], b: ["substring:0:2"] });
    expect(report.hasWarnings).toBe(false);
  });

  it("drops SUBSTR with start=0", () => {
    const report = new ConversionReport();
    const config = convertBlocking(["SUBSTR(l.x, 0, 3) = SUBSTR(r.x, 0, 3)"], report);

    expect(config).toBeNull();
    const warnings = report.findings.filter((f) => f.severity === "warning");
    expect(warnings.length).toBe(1);
    expect(warnings[0]!.message).toContain("unrecognized");
  });

  it("drops SUBSTR with length=0", () => {
    const report = new ConversionReport();
    const config = convertBlocking(["SUBSTR(l.x, 1, 0) = SUBSTR(r.x, 1, 0)"], report);

    expect(config).toBeNull();
    const warnings = report.findings.filter((f) => f.severity === "warning");
    expect(warnings.length).toBe(1);
    expect(warnings[0]!.message).toContain("unrecognized");
  });

  it("dedupes a repeated field order-preserving", () => {
    const rule = "l.a = r.a AND l.a = r.a";
    const report = new ConversionReport();
    const config = convertBlocking([rule], report);

    expect(config).not.toBeNull();
    expect(config?.keys[0]!.fields).toEqual(["a"]);
    expect(report.hasWarnings).toBe(false);
  });

  it("drops cross-column equality with a warning", () => {
    const rule = 'l."first_name" = r."surname"';
    const report = new ConversionReport();
    const config = convertBlocking([rule], report);

    expect(config).toBeNull();
    const warnings = report.findings.filter((f) => f.severity === "warning");
    expect(warnings.length).toBe(1);
    expect(warnings[0]!.message.includes("first_name") || warnings[0]!.message.includes("surname")).toBe(true);
  });

  it("converts Splink 4 serialized paren-wrapped conjuncts", () => {
    const rule = '(l."surname" = r."surname") AND (SUBSTRING(l.dob, 1, 4) = SUBSTRING(r.dob, 1, 4))';
    const report = new ConversionReport();
    const config = convertBlocking([rule], report);

    expect(config).not.toBeNull();
    expect(config?.keys[0]!.fields).toEqual(["surname", "dob"]);
    // #1832: mixed rule -> per-field chains, not a widened key-level transform.
    expect(config?.keys[0]!.transforms).toEqual([]);
    expect(config?.keys[0]!.fieldTransforms).toEqual({ surname: [], dob: ["substring:0:4"] });
  });

  it("strips whole-rule paren wrapping", () => {
    const report = new ConversionReport();
    const config = convertBlocking(['((l."a" = r."a") AND (l."b" = r."b"))'], report);

    expect(config).not.toBeNull();
    expect(config?.keys[0]!.fields).toEqual(["a", "b"]);
  });

  it("still drops unbalanced parens", () => {
    const report = new ConversionReport();
    const config = convertBlocking(['(l."a" = r."a"'], report);

    expect(config).toBeNull();
    expect(report.hasErrors).toBe(true);
  });

  it("drops a paren-wrapped unrecognizable OR conjunct", () => {
    const report = new ConversionReport();
    const config = convertBlocking(['(l."a" = r."a" OR l."b" = r."b")'], report);

    expect(config).toBeNull();
    const warnings = report.findings.filter((f) => f.severity === "warning");
    expect(warnings.length).toBe(1);
    expect(warnings[0]!.message).toContain("unrecognized");
  });
});

// ---------------------------------------------------------------------------
// test_from_splink_model_import.py -> detectTrained / importEm / convertScalars
// ---------------------------------------------------------------------------

describe("detectTrained / importEm", () => {
  it("reverses level order and copies m/u exactly", () => {
    const comp = trainedJwComparison();
    const settings = trainedSettings([comp]);
    const report = new ConversionReport();

    const field = convertComparison(comp, 0, report);
    expect(field).not.toBeNull();
    expect(field?.levels).toBe(4);

    const em = importEm([{ comp, compIdx: 0, field: field! }], settings, report);
    expect(em).not.toBeNull();

    const m = em!.m["first_name"]!;
    expect(m[3]).toBeCloseTo(0.5, 9);
    expect(m[2]).toBeCloseTo(0.3, 9);
    expect(m[1]).toBeCloseTo(0.15, 9);
    expect(m[0]).toBeCloseTo(0.05, 9);

    const u = em!.u["first_name"]!;
    expect(u[3]).toBeCloseTo(0.02, 9);
    expect(u[2]).toBeCloseTo(0.08, 9);
    expect(u[1]).toBeCloseTo(0.2, 9);
    expect(u[0]).toBeCloseTo(0.7, 9);
  });

  it("computes match weights as log2(m/u)", () => {
    const comp = trainedJwComparison();
    const settings = trainedSettings([comp]);
    const report = new ConversionReport();
    const field = convertComparison(comp, 0, report)!;
    const em = importEm([{ comp, compIdx: 0, field }], settings, report)!;

    const m = em.m["first_name"]!;
    const u = em.u["first_name"]!;
    const w = em.matchWeights["first_name"]!;
    for (let i = 0; i < 4; i++) {
      expect(w[i]).toBeCloseTo(Math.log2(m[i]! / u[i]!), 9);
    }
  });

  it("reads proportionMatched from settings", () => {
    const comp = trainedJwComparison();
    const settings = trainedSettings([comp]);
    const report = new ConversionReport();
    const field = convertComparison(comp, 0, report)!;
    const em = importEm([{ comp, compIdx: 0, field }], settings, report)!;

    expect(em.proportionMatched).toBeCloseTo(0.0002, 9);
  });

  it("marks converged=true, iterations=0, tfFreqs=null", () => {
    const comp = trainedJwComparison();
    const settings = trainedSettings([comp]);
    const report = new ConversionReport();
    const field = convertComparison(comp, 0, report)!;
    const em = importEm([{ comp, compIdx: 0, field }], settings, report)!;

    expect(em.converged).toBe(true);
    expect(em.iterations).toBe(0);
    expect(em.tfFreqs ?? null).toBeNull();
  });

  it("renormalizes and warns when a level is dropped", () => {
    const comp = {
      output_column_name: "first_name",
      comparison_levels: [
        { sql_condition: '"first_name_l" IS NULL OR "first_name_r" IS NULL', is_null_level: true },
        { sql_condition: '"first_name_l" = "first_name_r"', m_probability: 0.5, u_probability: 0.02 },
        {
          // unrecognized cross-column condition, but carries m/u anyway
          sql_condition: 'jaro_winkler_similarity("first_name_l", "surname_r") >= 0.85',
          m_probability: 0.2,
          u_probability: 0.1,
        },
        {
          sql_condition: 'jaro_winkler_similarity("first_name_l", "first_name_r") >= 0.88',
          m_probability: 0.2,
          u_probability: 0.18,
        },
        { sql_condition: "ELSE", m_probability: 0.1, u_probability: 0.7 },
      ],
    };
    const settings = trainedSettings([comp]);
    const report = new ConversionReport();
    const field = convertComparison(comp, 0, report);
    expect(field).not.toBeNull();
    expect(field?.levels).toBe(3);

    const em = importEm([{ comp, compIdx: 0, field: field! }], settings, report);
    expect(em).not.toBeNull();

    const m = em!.m["first_name"]!;
    expect(m.reduce((a, b) => a + b, 0)).toBeCloseTo(1.0, 9);
    const u = em!.u["first_name"]!;
    expect(u.reduce((a, b) => a + b, 0)).toBeCloseTo(1.0, 9);

    // surviving mass: m = 0.5 (exact) + 0.2 (jw 0.88) + 0.1 (ELSE) = 0.8
    expect(m[2]).toBeCloseTo(0.5 / 0.8, 9);
    expect(m[1]).toBeCloseTo(0.2 / 0.8, 9);
    expect(m[0]).toBeCloseTo(0.1 / 0.8, 9);

    expect(report.findings.some((f) => f.message.toLowerCase().includes("re-normaliz"))).toBe(true);
  });

  it("ignores m/u on null levels", () => {
    const comp = {
      output_column_name: "first_name",
      comparison_levels: [
        {
          sql_condition: '"first_name_l" IS NULL OR "first_name_r" IS NULL',
          is_null_level: true,
          m_probability: 0.9,
          u_probability: 0.9,
        },
        { sql_condition: '"first_name_l" = "first_name_r"', m_probability: 0.8, u_probability: 0.1 },
        { sql_condition: "ELSE", m_probability: 0.2, u_probability: 0.9 },
      ],
    };
    const settings = trainedSettings([comp]);
    const report = new ConversionReport();
    const field = convertComparison(comp, 0, report);
    expect(field).not.toBeNull();
    expect(field?.levels).toBe(2);

    const em = importEm([{ comp, compIdx: 0, field: field! }], settings, report)!;
    const m = em.m["first_name"]!;
    const u = em.u["first_name"]!;
    expect(m[1]).toBeCloseTo(0.8, 9);
    expect(m[0]).toBeCloseTo(0.2, 9);
    expect(u[1]).toBeCloseTo(0.1, 9);
    expect(u[0]).toBeCloseTo(0.9, 9);
  });

  it("sums collapsed duplicate levels and warns", () => {
    const comp = {
      output_column_name: "first_name",
      comparison_levels: [
        { sql_condition: '"first_name_l" IS NULL OR "first_name_r" IS NULL', is_null_level: true },
        {
          sql_condition: 'jaro_winkler_similarity("first_name_l", "first_name_r") >= 0.9',
          m_probability: 0.5,
          u_probability: 0.05,
        },
        // Same threshold via the spark-dialect alias: dedupe collapses this
        // onto the same GoldenMatch level.
        {
          sql_condition: 'jaro_winkler("first_name_l", "first_name_r") >= 0.9',
          m_probability: 0.3,
          u_probability: 0.15,
        },
        { sql_condition: "ELSE", m_probability: 0.2, u_probability: 0.8 },
      ],
    };
    const settings = trainedSettings([comp]);
    const report = new ConversionReport();
    const field = convertComparison(comp, 0, report);
    expect(field).not.toBeNull();
    expect(field?.levels).toBe(2);

    const em = importEm([{ comp, compIdx: 0, field: field! }], settings, report);
    expect(em).not.toBeNull();

    const m = em!.m["first_name"]!;
    const u = em!.u["first_name"]!;
    expect(m[1]).toBeCloseTo(0.8, 9);
    expect(m[0]).toBeCloseTo(0.2, 9);
    expect(u[1]).toBeCloseTo(0.2, 9);
    expect(u[0]).toBeCloseTo(0.8, 9);

    const collapseWarns = report.findings.filter(
      (f) => f.severity === "warning" && f.message.includes("collapsed") && f.message.includes("summed"),
    );
    expect(collapseWarns.length).toBe(1);
  });

  it.each(["m_probability", "u_probability"] as const)(
    "fills partial data (%s missing) with epsilon and warns",
    (missingSide) => {
      const exactLevel: Record<string, unknown> = {
        sql_condition: '"first_name_l" = "first_name_r"',
        m_probability: 0.8,
        u_probability: 0.1,
      };
      delete exactLevel[missingSide];
      const comp = {
        output_column_name: "first_name",
        comparison_levels: [
          { sql_condition: '"first_name_l" IS NULL OR "first_name_r" IS NULL', is_null_level: true },
          exactLevel,
          { sql_condition: "ELSE", m_probability: 0.2, u_probability: 0.9 },
        ],
      };
      const settings = trainedSettings([comp]);
      const report = new ConversionReport();
      const field = convertComparison(comp, 0, report);
      expect(field).not.toBeNull();

      const em = importEm([{ comp, compIdx: 0, field: field! }], settings, report);
      expect(em).not.toBeNull();

      const epsilon = 1e-6;
      if (missingSide === "m_probability") {
        const vals = em!.m["first_name"]!;
        expect(vals[1]).toBeCloseTo(epsilon / (epsilon + 0.2), 6);
        expect(vals[1]!).toBeGreaterThan(0.0);
      } else {
        const vals = em!.u["first_name"]!;
        expect(vals[1]).toBeCloseTo(epsilon / (epsilon + 0.9), 6);
        expect(vals[1]!).toBeGreaterThan(0.0);
      }

      const partialWarns = report.findings.filter(
        (f) => f.severity === "warning" && f.message.includes("partial trained data") && f.message.includes(missingSide),
      );
      expect(partialWarns.length).toBe(1);
      expect(partialWarns[0]!.splinkPath).toContain("comparison_levels[1]");
    },
  );

  it("returns null for bare settings with no m_probability", () => {
    const comp = {
      output_column_name: "surname",
      comparison_levels: [
        { sql_condition: '"surname_l" IS NULL OR "surname_r" IS NULL', is_null_level: true },
        { sql_condition: '"surname_l" = "surname_r"' },
        { sql_condition: "ELSE" },
      ],
    };
    const settings = { comparisons: [comp] };
    const report = new ConversionReport();
    const field = convertComparison(comp, 0, report);
    expect(field).not.toBeNull();

    expect(detectTrained(settings)).toBe(false);
    expect(importEm([{ comp, compIdx: 0, field: field! }], settings, report)).toBeNull();
  });

  it("detects trained when any level has m_probability", () => {
    const settings = trainedSettings([trainedJwComparison()]);
    expect(detectTrained(settings)).toBe(true);
  });

  it("fills an unassigned level with epsilon and warns", () => {
    const comp = {
      output_column_name: "first_name",
      comparison_levels: [
        { sql_condition: '"first_name_l" IS NULL OR "first_name_r" IS NULL', is_null_level: true },
        { sql_condition: '"first_name_l" = "first_name_r"', m_probability: 0.5, u_probability: 0.02 },
        // Recognized (jw >= 0.92) but carries no m/u at all.
        { sql_condition: 'jaro_winkler_similarity("first_name_l", "first_name_r") >= 0.92' },
        { sql_condition: "ELSE", m_probability: 0.1, u_probability: 0.7 },
      ],
    };
    const settings = trainedSettings([comp]);
    const report = new ConversionReport();
    const field = convertComparison(comp, 0, report);
    expect(field).not.toBeNull();
    expect(field?.levels).toBe(3);

    const em = importEm([{ comp, compIdx: 0, field: field! }], settings, report);
    expect(em).not.toBeNull();

    const m = em!.m["first_name"]!;
    const u = em!.u["first_name"]!;
    const epsilon = 1e-6;
    const totalM = 0.5 + epsilon + 0.1;
    const totalU = 0.02 + epsilon + 0.7;
    expect(m[1]).toBeCloseTo(epsilon / totalM, 6);
    expect(u[1]).toBeCloseTo(epsilon / totalU, 6);

    const fillWarns = report.findings.filter(
      (f) => f.severity === "warning" && f.message.includes("no m/u probability"),
    );
    expect(fillWarns.length).toBe(1);
  });

  it("drops and renormalizes an out-of-range band that still carries m/u", () => {
    const comp = {
      output_column_name: "surname",
      comparison_levels: [
        { sql_condition: '"surname_l" IS NULL OR "surname_r" IS NULL', is_null_level: true },
        { sql_condition: '"surname_l" = "surname_r"', m_probability: 0.6, u_probability: 0.05 },
        {
          // levenshtein <= 20 converts to sim = 1 - 20/10 = -1.0, out of
          // range -> dropped by convertComparison, but still carries m/u.
          sql_condition: 'levenshtein("surname_l", "surname_r") <= 20',
          m_probability: 0.3,
          u_probability: 0.15,
        },
        { sql_condition: "ELSE", m_probability: 0.1, u_probability: 0.8 },
      ],
    };
    const settings = trainedSettings([comp]);
    const report = new ConversionReport();
    const field = convertComparison(comp, 0, report);
    expect(field).not.toBeNull();
    expect(field?.levels).toBe(2);

    const em = importEm([{ comp, compIdx: 0, field: field! }], settings, report);
    expect(em).not.toBeNull();

    const m = em!.m["surname"]!;
    const u = em!.u["surname"]!;
    expect(m.reduce((a, b) => a + b, 0)).toBeCloseTo(1.0, 9);
    expect(u.reduce((a, b) => a + b, 0)).toBeCloseTo(1.0, 9);
    expect(m[1]).toBeCloseTo(0.6 / 0.7, 9);
    expect(m[0]).toBeCloseTo(0.1 / 0.7, 9);

    const lostWarns = report.findings.filter(
      (f) => f.severity === "warning" && f.message.includes("does not match any"),
    );
    expect(lostWarns.length).toBe(1);
  });

  it("skips a bare comparison mixed with a trained one and warns coverage", () => {
    const trainedComp = trainedJwComparison();
    const bareComp = {
      output_column_name: "surname",
      comparison_levels: [
        { sql_condition: '"surname_l" IS NULL OR "surname_r" IS NULL', is_null_level: true },
        { sql_condition: '"surname_l" = "surname_r"' },
        { sql_condition: "ELSE" },
      ],
    };
    const settings = trainedSettings([trainedComp, bareComp]);
    const report = new ConversionReport();

    const trainedField = convertComparison(trainedComp, 0, report);
    const bareField = convertComparison(bareComp, 1, report);
    expect(trainedField).not.toBeNull();
    expect(bareField).not.toBeNull();

    const em = importEm(
      [
        { comp: trainedComp, compIdx: 0, field: trainedField! },
        { comp: bareComp, compIdx: 1, field: bareField! },
      ],
      settings,
      report,
    );

    expect(em).not.toBeNull();
    expect("first_name" in em!.m).toBe(true);
    expect("surname" in em!.m).toBe(false);
    expect("surname" in em!.u).toBe(false);
    const partial = report.findings.filter(
      (f) => f.severity === "warning" && f.message.includes("will NOT cover field 'surname'"),
    );
    expect(partial.length).toBe(1);
    expect(partial[0]!.message).toContain("model_path");
  });

  it("infos that TF tables are absent for an imported tfAdjustment field", () => {
    const comp = trainedJwComparison();
    (comp["comparison_levels"] as Record<string, unknown>[])[1]!["tf_adjustment_column"] = "first_name";
    const settings = trainedSettings([comp]);
    const report = new ConversionReport();

    const field = convertComparison(comp, 0, report);
    expect(field).not.toBeNull();
    expect(field?.tfAdjustment).toBe(true);

    const em = importEm([{ comp, compIdx: 0, field: field! }], settings, report);

    expect(em).not.toBeNull();
    expect(em!.tfFreqs ?? null).toBeNull();
    const infos = report.findings.filter((f) => f.severity === "info" && f.message.includes("term-frequency"));
    expect(infos.length).toBe(1);
    expect(infos[0]!.message).toContain("first_name");
    expect(infos[0]!.message).toContain("retraining");
  });
});

describe("convertScalars", () => {
  it("maps em_convergence and max_iterations", () => {
    const settings = { em_convergence: 0.0001, max_iterations: 15 };
    const report = new ConversionReport();
    const kwargs = convertScalars(settings, report);

    expect(kwargs).toEqual({ convergenceThreshold: 0.0001, emIterations: 15 });
    const infos = report.findings.filter((f) => f.severity === "info");
    expect(infos.some((f) => f.message.includes("em_convergence"))).toBe(true);
    expect(infos.some((f) => f.message.includes("max_iterations"))).toBe(true);
  });

  it("treats unique_id_column_name as advisory only", () => {
    const settings = { unique_id_column_name: "record_id" };
    const report = new ConversionReport();
    const kwargs = convertScalars(settings, report);

    expect(Object.keys(kwargs).length).toBe(0);
    expect(report.findings.some((f) => f.message.includes("record_id") && f.message.includes("id_column"))).toBe(
      true,
    );
  });

  it("warns on link_and_dedupe", () => {
    const settings = { link_type: "link_and_dedupe" };
    const report = new ConversionReport();
    convertScalars(settings, report);

    expect(report.hasWarnings).toBe(true);
    expect(report.findings.some((f) => f.message.includes("link_and_dedupe"))).toBe(true);
  });

  it.each([
    ["dedupe_only", "dedupe()"],
    ["link_only", "match()"],
  ] as const)("infos dedupe_only/link_only (%s -> %s)", (linkType, expectedEntryPoint) => {
    const settings = { link_type: linkType };
    const report = new ConversionReport();
    convertScalars(settings, report);

    expect(report.hasWarnings).toBe(false);
    expect(report.findings.some((f) => f.message.includes(expectedEntryPoint))).toBe(true);
  });

  it("infos and ignores infra keys", () => {
    const settings = {
      sql_dialect: "duckdb",
      retain_matching_columns: true,
      retain_intermediate_calculation_columns: false,
      bayes_factor_column_prefix: "bf_",
    };
    const report = new ConversionReport();
    const kwargs = convertScalars(settings, report);

    expect(kwargs).toEqual({});
    const infos = report.findings.filter((f) => f.severity === "info");
    expect(infos.length).toBe(4);
    for (const key of [
      "sql_dialect",
      "retain_matching_columns",
      "retain_intermediate_calculation_columns",
      "bayes_factor_column_prefix",
    ]) {
      expect(infos.some((f) => f.message.includes(key) && f.message.includes("ignored"))).toBe(true);
    }
  });
});

// ---------------------------------------------------------------------------
// test_from_splink_api.py -> fromSplink
// ---------------------------------------------------------------------------

describe("fromSplink", () => {
  it("produces a valid config from a full settings object", () => {
    const settings = fullSettings();
    const conversion = fromSplink(settings);

    const mks = getMatchkeys(conversion.config);
    expect(mks.length).toBe(1);
    const mk = mks[0] as ProbabilisticMatchkey;
    expect(mk.name).toBe("splink_import");
    expect(mk.type).toBe("probabilistic");
    const fieldNames = new Set(mk.fields.map((f: MatchkeyField) => f.field));
    expect(fieldNames).toEqual(new Set(["first_name", "surname"]));

    expect(conversion.config.blocking).toBeDefined();
    expect(conversion.config.blocking!.strategy).toBe("multi_pass");
    expect(conversion.config.blocking!.passes?.length).toBe(2);
  });

  it("does not mutate the input settings object", () => {
    const settings = fullSettings();
    const original = JSON.parse(JSON.stringify(settings));
    fromSplink(settings);
    expect(settings).toEqual(original);
  });

  it("produces an em_model for trained settings", () => {
    const settings = fullSettings({ comparisons: [trainedJwComparison()] });
    settings["probability_two_random_records_match"] = 0.0002;
    const conversion = fromSplink(settings);

    expect(conversion.emModel).not.toBeNull();
    expect("first_name" in conversion.emModel!.m).toBe(true);
  });

  it("has a null em_model for bare settings", () => {
    const settings = fullSettings();
    const conversion = fromSplink(settings);
    expect(conversion.emModel).toBeNull();
  });

  it("raises in strict mode on an unmappable level", () => {
    const comp = jwComparison();
    (comp["comparison_levels"] as unknown[]).splice(4, 0, {
      sql_condition: 'jaro_winkler_similarity("first_name_l", "surname_r") >= 0.85',
    });
    const settings = fullSettings({ comparisons: [comp, exactOnlyComparison("surname")] });

    expect(() => fromSplink(settings, { strict: true })).toThrow(SplinkConversionError);
    try {
      fromSplink(settings, { strict: true });
      expect.unreachable();
    } catch (e) {
      const msg = String((e as Error).message);
      expect(msg.toLowerCase().includes("warning") || msg.toLowerCase().includes("error")).toBe(true);
      expect(msg).toContain("error(s)");
      expect(msg).toContain("warning(s)");
    }
  });

  it("does not raise in default mode on the same lossy input", () => {
    const comp = jwComparison();
    (comp["comparison_levels"] as unknown[]).splice(4, 0, {
      sql_condition: 'jaro_winkler_similarity("first_name_l", "surname_r") >= 0.85',
    });
    const settings = fullSettings({ comparisons: [comp, exactOnlyComparison("surname")] });

    const conversion = fromSplink(settings, { strict: false });
    expect(conversion.report.hasWarnings).toBe(true);
  });

  it("raises with zero convertible comparisons", () => {
    const settings = fullSettings({ comparisons: [] });
    expect(() => fromSplink(settings, { strict: false })).toThrow(SplinkConversionError);
  });

  it("raises when all comparisons are unrecognized", () => {
    const badComp = {
      output_column_name: "first_name",
      comparison_levels: [{ sql_condition: "some_weird_udf(first_name_l, first_name_r) > 3" }],
    };
    const settings = fullSettings({ comparisons: [badComp] });
    expect(() => fromSplink(settings, { strict: false })).toThrow(SplinkConversionError);
  });

  it("raises with zero convertible blocking rules", () => {
    const settings = fullSettings({ blockingRules: ["l.a > r.a OR l.b < r.b"] });
    expect(() => fromSplink(settings, { strict: false })).toThrow(SplinkConversionError);
  });

  it("resolves the mappedTo placeholder to the survivor's final position", () => {
    const droppedComp = {
      output_column_name: "first_name",
      comparison_levels: [{ sql_condition: 'jaro_winkler_similarity("first_name_l", "surname_r") >= 0.85' }],
    };
    const settings = fullSettings({ comparisons: [droppedComp, exactOnlyComparison("surname")] });

    const conversion = fromSplink(settings);

    const survivorFindings = conversion.report.findings.filter(
      (f) => f.splinkPath === "comparisons[1]" && f.mappedTo,
    );
    expect(survivorFindings.length).toBeGreaterThan(0);
    expect(survivorFindings.some((f) => f.mappedTo!.startsWith("matchkeys[0].fields[0]"))).toBe(true);
    expect(conversion.report.findings.some((f) => (f.mappedTo ?? "").includes("matchkeys[?]"))).toBe(false);
  });

  it("lands em_iterations and convergence_threshold on the matchkey", () => {
    const settings = fullSettings();
    settings["em_convergence"] = 0.0005;
    settings["max_iterations"] = 12;

    const conversion = fromSplink(settings);
    const mk = getMatchkeys(conversion.config)[0] as ProbabilisticMatchkey;
    expect(mk.convergenceThreshold).toBeCloseTo(0.0005, 9);
    expect(mk.emIterations).toBe(12);
  });

  it("rejects non-object input", () => {
    expect(() => fromSplink("not an object")).toThrow(SplinkConversionError);
    expect(() => fromSplink(null)).toThrow(SplinkConversionError);
    expect(() => fromSplink(42)).toThrow(SplinkConversionError);
  });
});
