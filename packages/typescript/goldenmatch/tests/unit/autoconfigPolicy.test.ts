import { describe, it, expect } from "vitest";
import { HeuristicRefitPolicy, type Rule } from "../../src/core/autoconfigPolicy.js";
import { RunHistory } from "../../src/core/autoconfigHistory.js";
import {
  makeComplexityProfile,
  makeDataProfile,
  makeBlockingProfile,
  makeScoringProfile,
} from "../../src/core/complexityProfile.js";
import { makeConfig } from "../../src/core/types.js";

const greenProfile = makeComplexityProfile({
  data: makeDataProfile({
    nRows: 100,
    nCols: 3,
    columnTypes: { a: "text", b: "numeric", c: "date" },
  }),
  blocking: makeBlockingProfile({
    nBlocks: 10,
    blockSizesP99: 10,
    reductionRatio: 0.9,
    singletonBlockCount: 1,
  }),
  scoring: makeScoringProfile({
    candidatesCompared: 100,
    nPairsScored: 50,
    massAboveThreshold: 0.6,
    massInBorderline: 0.1,
    dipStatistic: 0.05,
  }),
});

const redProfile = makeComplexityProfile({
  data: makeDataProfile({
    nRows: 100,
    nCols: 3,
    columnTypes: { a: "text", b: "numeric", c: "date" },
  }),
  blocking: makeBlockingProfile(),  // nBlocks=0 → RED
  scoring: makeScoringProfile({
    candidatesCompared: 100,
    nPairsScored: 50,
    massAboveThreshold: 0.6,
    massInBorderline: 0.1,
    dipStatistic: 0.05,
  }),
});

describe("HeuristicRefitPolicy", () => {
  it("returns null on GREEN profile (no refit needed)", () => {
    const fired: string[] = [];
    const noisyRule: Rule = (ctx) => {
      fired.push("noisy");
      return [ctx.config, { ruleName: "noisy", rationale: "", configDiff: {} }];
    };
    const policy = new HeuristicRefitPolicy([noisyRule]);
    const result = policy.propose(greenProfile, makeConfig(), new RunHistory());
    expect(result).toBe(null);
    expect(fired).toEqual([]);  // never even consulted on GREEN
  });

  it("returns the first rule's output on non-GREEN", () => {
    const newCfg = makeConfig({ threshold: 0.6 });
    const ruleA: Rule = () => [
      newCfg,
      { ruleName: "rule_a", rationale: "test", configDiff: {} },
    ];
    const ruleB: Rule = (ctx) => [
      ctx.config,
      { ruleName: "rule_b", rationale: "", configDiff: {} },
    ];
    const policy = new HeuristicRefitPolicy([ruleA, ruleB]);
    const result = policy.propose(redProfile, makeConfig(), new RunHistory());
    expect(result).toBe(newCfg);
  });

  it("treats a no-op (newConfig === current) as satisfied", () => {
    const noop: Rule = (ctx) => [
      ctx.config,
      { ruleName: "noop", rationale: "", configDiff: {} },
    ];
    const policy = new HeuristicRefitPolicy([noop]);
    const result = policy.propose(redProfile, makeConfig(), new RunHistory());
    expect(result).toBe(null);
  });

  it("attaches the firing rule's decision to the latest history entry", () => {
    const newCfg = makeConfig({ threshold: 0.6 });
    const firingRule: Rule = () => [
      newCfg,
      { ruleName: "firing", rationale: "", configDiff: {} },
    ];
    const policy = new HeuristicRefitPolicy([firingRule]);
    const history = new RunHistory();
    history.append({
      iteration: 0,
      config: makeConfig(),
      profile: redProfile,
      decision: null,
      error: null,
      wallClockMs: 1,
    });
    policy.propose(redProfile, makeConfig(), history);
    expect(history.entries[0]!.decision?.ruleName).toBe("firing");
  });

  it("returns null when no rule fires (rules all returned null)", () => {
    const skippingRule: Rule = () => null;
    const policy = new HeuristicRefitPolicy([skippingRule, skippingRule]);
    const result = policy.propose(redProfile, makeConfig(), new RunHistory());
    expect(result).toBe(null);
  });
});
