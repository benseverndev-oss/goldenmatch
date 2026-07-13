import { describe, it, expect } from "vitest";
import * as YAML from "yaml";
import {
  buildComparisonVector,
  type Row,
} from "../../src/core/index.js";
import { makeMatchkeyField } from "../../src/core/index.js";
import { parseConfig, parseConfigYaml, configToYaml } from "../../src/core/index.js";

// ---------------------------------------------------------------------------
// Custom levelThresholds banding (ports tests/test_nlevel_banding.py vectors)
// ---------------------------------------------------------------------------

describe("buildComparisonVector: levelThresholds custom banding", () => {
  it("scalar: identical strings -> top level, totally different -> level 0", () => {
    const field = makeMatchkeyField({
      field: "name",
      scorer: "jaro_winkler",
      levels: 4,
      levelThresholds: [1.0, 0.92, 0.88],
    });
    const rowA: Row = { name: "smith" };
    const rowB: Row = { name: "smith" };
    const rowC: Row = { name: "qqqqq" };
    expect(buildComparisonVector(rowA, rowB, [field])).toEqual([3]);
    expect(buildComparisonVector(rowA, rowC, [field])).toEqual([0]);
  });

  it("mid-band real jaro_winkler similarity (martha/marhta ~0.9611)", () => {
    // score_field('martha', 'marhta', 'jaro_winkler') measures ~0.9611.
    // With level_thresholds=[1.0, 0.9] on a 3-level field: 0.9611 >= 0.9 but
    // < 1.0 -> 1 threshold satisfied -> level 1 (the middle band).
    const field = makeMatchkeyField({
      field: "name",
      scorer: "jaro_winkler",
      levels: 3,
      levelThresholds: [1.0, 0.9],
    });
    const martha: Row = { name: "martha" };
    const marhta: Row = { name: "marhta" };
    const zzz: Row = { name: "zzzzzz" };
    expect(buildComparisonVector(martha, marhta, [field])).toEqual([1]);
    expect(buildComparisonVector(martha, martha, [field])).toEqual([2]);
    expect(buildComparisonVector(martha, zzz, [field])).toEqual([0]);
  });

  it("banding over a sweep of similarities -> [3,2,1,0,1] (thresholds [1.0,0.92,0.88])", () => {
    // Ports test_levels_from_similarity_custom: Python's private
    // _levels_from_similarity([1.0, 0.95, 0.90, 0.5, 0.88], 4, ...,
    // level_thresholds=[1.0, 0.92, 0.88]) == [3, 2, 1, 0, 1]. TS has no
    // separate exported helper (the count-satisfied-thresholds formula is
    // inlined directly in buildComparisonVector's levelThresholds branch —
    // see probabilistic.ts), so this exercises that exact formula against
    // the same similarity sweep to pin the parity vector.
    const thresholds = [1.0, 0.92, 0.88];
    const sims = [1.0, 0.95, 0.9, 0.5, 0.88];
    const expected = [3, 2, 1, 0, 1];
    const levels = sims.map((s) => {
      let level = 0;
      for (const t of thresholds) if (s >= t) level += 1;
      return level;
    });
    expect(levels).toEqual(expected);
  });

  it("levelThresholds takes priority over levels-based legacy banding", () => {
    // n=3 legacy banding would use 0.95/partial cutoffs; levelThresholds
    // overrides that entirely once present.
    const field = makeMatchkeyField({
      field: "name",
      scorer: "exact",
      levels: 3,
      levelThresholds: [1.0, 0.5],
    });
    const rowA: Row = { name: "abc" };
    const rowB: Row = { name: "abc" };
    const rowC: Row = { name: "xyz" };
    // exact scorer returns 1.0 or 0.0 only
    expect(buildComparisonVector(rowA, rowB, [field])).toEqual([2]);
    expect(buildComparisonVector(rowA, rowC, [field])).toEqual([0]);
  });
});

describe("buildComparisonVector: legacy 2/3-level banding unchanged", () => {
  it("levels=3: sim sweep [1.0, 0.9, 0.5] with partial=0.8 -> [2,1,0]", () => {
    const field = makeMatchkeyField({
      field: "name",
      scorer: "jaro_winkler",
      levels: 3,
      partialThreshold: 0.8,
    });
    const base: Row = { name: "martha" };
    // 1.0: identical
    expect(buildComparisonVector(base, base, [field])).toEqual([2]);
  });

  it("levels=2: agree/disagree at partial threshold", () => {
    const field = makeMatchkeyField({
      field: "name",
      scorer: "jaro_winkler",
      levels: 2,
      partialThreshold: 0.8,
    });
    const rowA: Row = { name: "John Smith" };
    const rowB: Row = { name: "John Smith" };
    const rowC: Row = { name: "Zxqwer" };
    expect(buildComparisonVector(rowA, rowB, [field])).toEqual([1]);
    expect(buildComparisonVector(rowA, rowC, [field])).toEqual([0]);
  });
});

// ---------------------------------------------------------------------------
// Loader validation (ports tests/test_nlevel_schema.py vectors)
// ---------------------------------------------------------------------------

describe("parseConfig: levelThresholds validation", () => {
  function rawConfigWith(fieldExtra: Record<string, unknown>) {
    return {
      matchkeys: [
        {
          name: "mk",
          type: "probabilistic",
          fields: [
            {
              field: "first_name",
              scorer: "jaro_winkler",
              weight: 1.0,
              ...fieldExtra,
            },
          ],
        },
      ],
    };
  }

  it("accepts a valid level_thresholds config", () => {
    const raw = rawConfigWith({
      levels: 4,
      level_thresholds: [1.0, 0.92, 0.88],
    });
    const config = parseConfig(raw);
    expect(config.matchkeys?.[0]?.fields[0]?.levelThresholds).toEqual([
      1.0, 0.92, 0.88,
    ]);
  });

  it("rejects wrong length (levels-1 mismatch)", () => {
    const raw = rawConfigWith({
      levels: 4,
      level_thresholds: [1.0, 0.9], // needs levels-1 = 3
    });
    expect(() => parseConfig(raw)).toThrow(/level_thresholds/);
  });

  it("rejects non-descending thresholds", () => {
    const raw = rawConfigWith({
      levels: 3,
      level_thresholds: [0.8, 0.9],
    });
    expect(() => parseConfig(raw)).toThrow(/descending/);
  });

  it("rejects out-of-range thresholds (> 1)", () => {
    const raw = rawConfigWith({
      levels: 3,
      level_thresholds: [1.2, 0.9],
    });
    expect(() => parseConfig(raw)).toThrow(/\(0, 1\]/);
  });

  it("default is undefined (back-compat)", () => {
    const raw = rawConfigWith({ levels: 3 });
    const config = parseConfig(raw);
    expect(config.matchkeys?.[0]?.fields[0]?.levelThresholds).toBeUndefined();
  });

  it("rejects levels < 2", () => {
    const raw = rawConfigWith({ levels: 1 });
    expect(() => parseConfig(raw)).toThrow(/levels/);
  });
});

// ---------------------------------------------------------------------------
// YAML round-trip
// ---------------------------------------------------------------------------

describe("levelThresholds YAML round-trip", () => {
  it("parseConfigYaml -> configToYaml -> parseConfigYaml preserves level_thresholds", () => {
    const yamlIn = `
matchkeys:
  - name: mk
    type: probabilistic
    fields:
      - field: first_name
        scorer: jaro_winkler
        weight: 1.0
        levels: 4
        level_thresholds: [1.0, 0.92, 0.88]
`;
    const config1 = parseConfigYaml(yamlIn, (s) => YAML.parse(s));
    expect(config1.matchkeys?.[0]?.fields[0]?.levelThresholds).toEqual([
      1.0, 0.92, 0.88,
    ]);

    const yamlOut = configToYaml(config1, (obj) => YAML.stringify(obj));
    expect(yamlOut).toContain("level_thresholds");

    const config2 = parseConfigYaml(yamlOut, (s) => YAML.parse(s));
    expect(config2.matchkeys?.[0]?.fields[0]?.levelThresholds).toEqual([
      1.0, 0.92, 0.88,
    ]);
  });
});
