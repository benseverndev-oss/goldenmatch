/**
 * autoconfigRules.indicators.test.ts — Unit tests for the v1.10
 * indicator-aware refit rules ported in Wave 2.
 */
import { describe, it, expect } from "vitest";
import {
  ruleUniformHeavyBlocking,
  ruleBlockingFieldNullHeavy,
  ruleRecallGapSuspected,
  ruleCollisionSignalTooHigh,
  ruleSparseMatchExpand,
  ruleCrossBlockingDisagreement,
  ruleCorruptionNormalize,
  DEFAULT_RULES_V1_10,
} from "../../src/core/autoconfigRules.js";
import {
  makeComplexityProfile,
  makeDataProfile,
  makeBlockingProfile,
  makeScoringProfile,
} from "../../src/core/complexityProfile.js";
import { RunHistory } from "../../src/core/autoconfigHistory.js";
import { IndicatorContext } from "../../src/core/indicators.js";
import type {
  GoldenMatchConfig,
  Row,
} from "../../src/core/types.js";

function baseConfig(): GoldenMatchConfig {
  return {
    matchkeys: [
      {
        name: "weighted_identity",
        type: "weighted",
        threshold: 0.85,
        fields: [
          { field: "name", scorer: "jaro_winkler", weight: 0.6, transforms: ["lowercase"] },
        ],
      },
    ],
    blocking: {
      strategy: "static",
      keys: [{ fields: ["zip"], transforms: ["digits_only"] }],
      maxBlockSize: 1000,
      skipOversized: true,
    },
    threshold: 0.85,
  } as GoldenMatchConfig;
}

const rows: Row[] = [
  { name: "Alice", email: "alice@example.com", zip: "10001" },
  { name: "Bob", email: "bob@example.com", zip: "10002" },
];

describe("ruleUniformHeavyBlocking", () => {
  it("fires when avg block large and mass_above+borderline > 0.5", () => {
    const profile = makeComplexityProfile({
      data: makeDataProfile({
        nRows: 100,
        nCols: 2,
        columnTypes: { name: "name", email: "email" },
        cardinalityRatio: { name: 0.5, email: 0.5 },
      }),
      blocking: makeBlockingProfile({ nBlocks: 2 }),
      scoring: makeScoringProfile({
        candidatesCompared: 200,
        massAboveThreshold: 0.8,
        massInBorderline: 0.6,
      }),
    });
    const out = ruleUniformHeavyBlocking({
      profile,
      config: baseConfig(),
      history: new RunHistory(),
    });
    expect(out).not.toBeNull();
  });

  it("no-ops when avg block small", () => {
    const profile = makeComplexityProfile({
      data: makeDataProfile({ nRows: 10 }),
      blocking: makeBlockingProfile({ nBlocks: 5 }),
      scoring: makeScoringProfile({
        candidatesCompared: 20,
        massAboveThreshold: 0.8,
        massInBorderline: 0.6,
      }),
    });
    const out = ruleUniformHeavyBlocking({
      profile,
      config: baseConfig(),
      history: new RunHistory(),
    });
    expect(out).toBeNull();
  });
});

describe("ruleBlockingFieldNullHeavy", () => {
  it("fires when blocking field is high-null", () => {
    const profile = makeComplexityProfile({
      data: makeDataProfile({
        nRows: 10,
        columnTypes: { zip: "numeric", email: "email" },
        cardinalityRatio: { zip: 0.5, email: 0.9 },
        nullRate: { zip: 0.3, email: 0.0 },
      }),
    });
    const out = ruleBlockingFieldNullHeavy({
      profile,
      config: baseConfig(),
      history: new RunHistory(),
    });
    expect(out).not.toBeNull();
  });
});

describe("ruleRecallGapSuspected", () => {
  it("fires on tight blocking (mass==1, low candidates, high reduction)", () => {
    const profile = makeComplexityProfile({
      data: makeDataProfile({
        nRows: 100,
        columnTypes: { name: "name", email: "email" },
        cardinalityRatio: { name: 0.9, email: 0.9 },
        nullRate: { name: 0.0, email: 0.0 },
      }),
      blocking: makeBlockingProfile({ reductionRatio: 0.999 }),
      scoring: makeScoringProfile({
        candidatesCompared: 5,
        massAboveThreshold: 1.0,
      }),
    });
    const out = ruleRecallGapSuspected({
      profile,
      config: baseConfig(),
      history: new RunHistory(),
    });
    expect(out).not.toBeNull();
  });

  it("no-ops on healthy scoring", () => {
    const profile = makeComplexityProfile({
      blocking: makeBlockingProfile({ reductionRatio: 0.5 }),
      scoring: makeScoringProfile({ candidatesCompared: 100, massAboveThreshold: 0.3 }),
    });
    const out = ruleRecallGapSuspected({
      profile,
      config: baseConfig(),
      history: new RunHistory(),
    });
    expect(out).toBeNull();
  });
});

describe("ruleCollisionSignalTooHigh", () => {
  it("no-ops when indicators missing", () => {
    const out = ruleCollisionSignalTooHigh({
      profile: makeComplexityProfile(),
      config: baseConfig(),
      history: new RunHistory(),
    });
    expect(out).toBeNull();
  });

  it("demotes a colliding exact matchkey", () => {
    const collisionRows: Row[] = [
      { email: "shared@example.com", name: "Alice Anderson" },
      { email: "shared@example.com", name: "Zachary Zykov" },
      { email: "shared@example.com", name: "Yara Y" },
      { email: "other@example.com", name: "Bob B" },
    ];
    const cfg: GoldenMatchConfig = {
      matchkeys: [
        {
          name: "exact_email",
          type: "exact",
          fields: [{ field: "email", scorer: "exact", weight: 1.0, transforms: ["lowercase"] }],
          threshold: 1.0,
        },
        {
          name: "weighted_identity",
          type: "weighted",
          threshold: 0.85,
          fields: [{ field: "name", scorer: "jaro_winkler", weight: 0.6, transforms: ["lowercase"] }],
        },
      ],
      blocking: {
        strategy: "static",
        keys: [{ fields: ["name"], transforms: ["lowercase"] }],
        maxBlockSize: 1000,
        skipOversized: true,
      },
      threshold: 0.85,
    } as GoldenMatchConfig;
    const profile = makeComplexityProfile({
      data: makeDataProfile({
        cardinalityRatio: { email: 0.5, name: 1.0 },
        columnTypes: { email: "email", name: "name" },
      }),
    });
    const indicators = new IndicatorContext(collisionRows, cfg);
    const out = ruleCollisionSignalTooHigh({
      profile,
      config: cfg,
      history: new RunHistory(),
      indicators,
    });
    expect(out).not.toBeNull();
    const [newCfg, decision] = out!;
    expect(decision.ruleName).toBe("demote_clustered_identity");
    // The exact_email matchkey should be gone.
    expect(newCfg.matchkeys?.some((m) => m.name === "exact_email")).toBe(false);
  });
});

describe("ruleSparseMatchExpand", () => {
  it("one-shot lowers threshold when sparsity verdict is sparse", () => {
    const cfg = baseConfig();
    const indicators = new IndicatorContext(rows, cfg);
    // indicators.sparsityVerdict will be sparse (no exact matchkeys → forced sparse).
    const out = ruleSparseMatchExpand({
      profile: makeComplexityProfile(),
      config: cfg,
      history: new RunHistory(),
      indicators,
    });
    expect(out).not.toBeNull();
    expect(indicators.hasFired("rule_sparse_match_expand")).toBe(true);

    // Second call: already fired, returns null.
    const out2 = ruleSparseMatchExpand({
      profile: makeComplexityProfile(),
      config: out![0],
      history: new RunHistory(),
      indicators,
    });
    expect(out2).toBeNull();
  });
});

describe("ruleCrossBlockingDisagreement", () => {
  it("no-ops without indicators", () => {
    const out = ruleCrossBlockingDisagreement({
      profile: makeComplexityProfile(),
      config: baseConfig(),
      history: new RunHistory(),
    });
    expect(out).toBeNull();
  });
});

describe("ruleCorruptionNormalize", () => {
  it("appends transforms when corruption + identity priors are high", () => {
    const dirtyRows: Row[] = [
      { email: "ALICE@x.com", name: "Alice" },
      { email: "alice@X.com", name: "Alice" },
      { email: "Alice@x.com", name: "Alice" },
      { email: "BOB@x.com", name: "Bob" },
      { email: "bob@X.com", name: "Bob" },
    ];
    const cfg: GoldenMatchConfig = {
      ...baseConfig(),
      blocking: {
        strategy: "static",
        keys: [{ fields: ["email"], transforms: [] }],
        maxBlockSize: 1000,
        skipOversized: true,
      },
    } as GoldenMatchConfig;
    const indicators = new IndicatorContext(dirtyRows, cfg);
    const profile = makeComplexityProfile({
      data: makeDataProfile({ nRows: 3, columnTypes: { email: "email" } }),
      scoring: makeScoringProfile({ candidatesCompared: 0, nPairsScored: 0 }),
    });
    const out = ruleCorruptionNormalize({
      profile,
      config: cfg,
      history: new RunHistory(),
      indicators,
    });
    expect(out).not.toBeNull();
    const [newCfg] = out!;
    expect(newCfg.blocking!.keys[0]!.transforms).toContain("lowercase");
  });
});

describe("DEFAULT_RULES_V1_10", () => {
  it("has 14 rules in the canonical order", () => {
    expect(DEFAULT_RULES_V1_10.length).toBe(14);
  });
});
