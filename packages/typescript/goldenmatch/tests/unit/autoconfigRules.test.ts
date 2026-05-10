import { describe, it, expect } from "vitest";
import {
  ruleBlockingSingletonTrap,
  ruleBlockingTooCoarse,
  ruleBlockingKeySwap,
  ruleLowReductionRatio,
  ruleLowTransitivity,
  ruleNoMatches,
  ruleUnimodalScoring,
  DEFAULT_RULES_V1_7_V1_8,
} from "../../src/core/autoconfigRules.js";
import type { RuleContext } from "../../src/core/autoconfigPolicy.js";
import { RunHistory } from "../../src/core/autoconfigHistory.js";
import {
  makeComplexityProfile,
  makeDataProfile,
  makeBlockingProfile,
  makeScoringProfile,
  makeClusterProfile,
  makeMatchkeyProfile,
} from "../../src/core/complexityProfile.js";
import {
  makeConfig,
  makeMatchkeyConfig,
  makeMatchkeyField,
  makeBlockingConfig,
} from "../../src/core/types.js";

function withWeighted(threshold = 0.85, fields = [{ field: "name", scorer: "jaro_winkler" }]) {
  return makeConfig({
    matchkeys: [
      makeMatchkeyConfig({
        name: "weighted_identity",
        type: "weighted",
        threshold,
        fields: fields.map((f) =>
          makeMatchkeyField({
            field: f.field,
            transforms: ["lowercase"],
            scorer: f.scorer,
            weight: 1.0,
          }),
        ),
      }),
    ],
    blocking: makeBlockingConfig({
      strategy: "static",
      keys: [{ fields: ["zip"], transforms: ["digits_only"] }],
    }),
  });
}

function makeCtx(overrides: Partial<RuleContext>): RuleContext {
  return {
    profile:
      overrides.profile ??
      makeComplexityProfile({
        data: makeDataProfile({
          nRows: 100,
          nCols: 3,
          columnTypes: { name: "text", zip: "text", id: "id-like" },
        }),
        blocking: makeBlockingProfile({ nBlocks: 10 }),
        scoring: makeScoringProfile(),
      }),
    config: overrides.config ?? withWeighted(),
    history: overrides.history ?? new RunHistory(),
  };
}

describe("ruleBlockingSingletonTrap", () => {
  it("fires when candidates_compared=0 and n_blocks>0", () => {
    const profile = makeComplexityProfile({
      data: makeDataProfile({
        nRows: 100,
        nCols: 3,
        columnTypes: { name: "text", zip: "text", id: "id-like" },
      }),
      blocking: makeBlockingProfile({ nBlocks: 100, singletonBlockCount: 90 }),
      scoring: makeScoringProfile({ candidatesCompared: 0 }),
    });
    const out = ruleBlockingSingletonTrap(makeCtx({ profile }));
    expect(out).not.toBe(null);
    const [newCfg, decision] = out!;
    expect(decision.ruleName).toBe("blocking_singleton_trap");
    expect(newCfg.blocking?.keys[0]?.transforms).toContain("first_token");
  });

  it("does not fire when candidates_compared > 0", () => {
    const profile = makeComplexityProfile({
      data: makeDataProfile({
        nRows: 100,
        nCols: 3,
        columnTypes: { name: "text", zip: "text", id: "id-like" },
      }),
      blocking: makeBlockingProfile({ nBlocks: 100 }),
      scoring: makeScoringProfile({ candidatesCompared: 50 }),
    });
    expect(ruleBlockingSingletonTrap(makeCtx({ profile }))).toBe(null);
  });
});

describe("ruleBlockingTooCoarse", () => {
  it("fires on p99 outlier (skewed distribution)", () => {
    const profile = makeComplexityProfile({
      data: makeDataProfile({
        nRows: 1000,
        nCols: 3,
        columnTypes: { name: "text", zip: "text", id: "text" },
        cardinalityRatio: { name: 0.8, zip: 0.05, id: 0.5 },
      }),
      // avg = 100; p99 = 2000 > 10*100 = 1000 → fires
      blocking: makeBlockingProfile({ nBlocks: 10, blockSizesP99: 2000 }),
      scoring: makeScoringProfile({ candidatesCompared: 100, massAboveThreshold: 0.3 }),
    });
    const out = ruleBlockingTooCoarse(makeCtx({ profile }));
    expect(out).not.toBe(null);
  });

  it("does not fire on balanced distribution", () => {
    const profile = makeComplexityProfile({
      data: makeDataProfile({ nRows: 1000 }),
      blocking: makeBlockingProfile({ nBlocks: 10, blockSizesP99: 200 }),
      scoring: makeScoringProfile({ candidatesCompared: 100 }),
    });
    expect(ruleBlockingTooCoarse(makeCtx({ profile }))).toBe(null);
  });
});

describe("ruleBlockingKeySwap", () => {
  it("does not fire without a prior decision", () => {
    const profile = makeComplexityProfile({
      data: makeDataProfile({
        nRows: 100,
        nCols: 3,
        columnTypes: { name: "text", zip: "text" },
      }),
      blocking: makeBlockingProfile({ nBlocks: 10 }),
      scoring: makeScoringProfile({ candidatesCompared: 100, massAboveThreshold: 0 }),
    });
    expect(ruleBlockingKeySwap(makeCtx({ profile }))).toBe(null);
  });

  it("fires after a prior decision exists", () => {
    const profile = makeComplexityProfile({
      data: makeDataProfile({
        nRows: 100,
        nCols: 3,
        columnTypes: { name: "text", zip: "text" },
      }),
      blocking: makeBlockingProfile({ nBlocks: 10 }),
      scoring: makeScoringProfile({ candidatesCompared: 100, massAboveThreshold: 0 }),
    });
    const history = new RunHistory();
    history.append({
      iteration: 0,
      config: withWeighted(),
      profile,
      decision: { ruleName: "prior", rationale: "", configDiff: {} },
      error: null,
      wallClockMs: 1,
    });
    const out = ruleBlockingKeySwap(makeCtx({ profile, history }));
    expect(out).not.toBe(null);
    expect(out![1].ruleName).toBe("blocking_key_swap");
  });
});

describe("ruleLowReductionRatio", () => {
  it("fires on reduction_ratio < 0.5", () => {
    const profile = makeComplexityProfile({
      data: makeDataProfile({
        nRows: 100,
        nCols: 2,
        columnTypes: { name: "text", last: "text" },
      }),
      blocking: makeBlockingProfile({ nBlocks: 5, reductionRatio: 0.2 }),
      scoring: makeScoringProfile({ candidatesCompared: 100, massAboveThreshold: 0.3 }),
    });
    const out = ruleLowReductionRatio(makeCtx({ profile }));
    expect(out).not.toBe(null);
    expect(out![1].ruleName).toBe("low_reduction_ratio");
    expect(out![0].blocking?.strategy).toBe("multi_pass");
  });

  it("does not fire when reduction is healthy", () => {
    const profile = makeComplexityProfile({
      data: makeDataProfile({ nRows: 100 }),
      blocking: makeBlockingProfile({ nBlocks: 5, reductionRatio: 0.9 }),
    });
    expect(ruleLowReductionRatio(makeCtx({ profile }))).toBe(null);
  });
});

describe("ruleLowTransitivity", () => {
  it("lowers threshold when transitivity < 0.85", () => {
    const profile = makeComplexityProfile({
      data: makeDataProfile({ nRows: 100, nCols: 3, columnTypes: { name: "text", zip: "text", x: "numeric" } }),
      blocking: makeBlockingProfile({ nBlocks: 5 }),
      cluster: makeClusterProfile({ transitivityRate: 0.7, nClusters: 5 }),
    });
    const cfg = withWeighted(0.85);
    const out = ruleLowTransitivity(makeCtx({ profile, config: cfg }));
    expect(out).not.toBe(null);
    const newWeighted = out![0].matchkeys?.[0];
    expect(newWeighted?.type).toBe("weighted");
    expect((newWeighted as { threshold: number }).threshold).toBeCloseTo(0.8, 4);
  });

  it("does not fire when transitivity healthy", () => {
    const profile = makeComplexityProfile({
      cluster: makeClusterProfile({ transitivityRate: 0.95, nClusters: 5 }),
    });
    expect(ruleLowTransitivity(makeCtx({ profile }))).toBe(null);
  });
});

describe("ruleNoMatches", () => {
  it("lowers threshold by 0.05 when nothing matched", () => {
    const profile = makeComplexityProfile({
      data: makeDataProfile({ nRows: 100, nCols: 2, columnTypes: { a: "text", b: "numeric" } }),
      blocking: makeBlockingProfile({ nBlocks: 5 }),
      scoring: makeScoringProfile({ candidatesCompared: 100, massAboveThreshold: 0 }),
    });
    const cfg = withWeighted(0.85);
    const out = ruleNoMatches(makeCtx({ profile, config: cfg }));
    expect(out).not.toBe(null);
    const newWeighted = out![0].matchkeys?.[0];
    expect((newWeighted as { threshold: number }).threshold).toBeCloseTo(0.8, 4);
  });

  it("does not fire below threshold floor (0.5)", () => {
    const profile = makeComplexityProfile({
      blocking: makeBlockingProfile({ nBlocks: 5 }),
      scoring: makeScoringProfile({ candidatesCompared: 100, massAboveThreshold: 0 }),
    });
    const cfg = withWeighted(0.5);  // already at floor
    expect(ruleNoMatches(makeCtx({ profile, config: cfg }))).toBe(null);
  });

  it("does not fire when candidates_compared == 0 (singleton trap territory)", () => {
    const profile = makeComplexityProfile({
      scoring: makeScoringProfile({ candidatesCompared: 0, massAboveThreshold: 0 }),
    });
    expect(ruleNoMatches(makeCtx({ profile }))).toBe(null);
  });
});

describe("ruleUnimodalScoring", () => {
  it("swaps scorer to ensemble on the highest-cardinality matchkey field", () => {
    const profile = makeComplexityProfile({
      data: makeDataProfile({ nRows: 100, nCols: 2, columnTypes: { name: "text", zip: "text" } }),
      blocking: makeBlockingProfile({ nBlocks: 5 }),
      scoring: makeScoringProfile({
        candidatesCompared: 100,
        nPairsScored: 50,
        dipStatistic: 0.005,  // unimodal
        massAboveThreshold: 0.3,
      }),
      matchkey: makeMatchkeyProfile({
        perField: {
          name: {
            postTransformCardinalityRatio: 0.8,
            postTransformNullRate: 0.0,
            postTransformValueLengthP50: 5,
          },
        },
      }),
    });
    const out = ruleUnimodalScoring(makeCtx({ profile }));
    expect(out).not.toBe(null);
    const newWeighted = out![0].matchkeys?.[0];
    expect(newWeighted?.fields[0]?.scorer).toBe("ensemble");
  });
});

describe("DEFAULT_RULES_V1_7_V1_8", () => {
  it("exports exactly 7 base rules in the documented order", () => {
    expect(DEFAULT_RULES_V1_7_V1_8).toHaveLength(7);
  });
});
