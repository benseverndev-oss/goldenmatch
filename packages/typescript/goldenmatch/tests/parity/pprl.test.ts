/**
 * pprl.test.ts -- cross-language parity for PPRL.
 *
 * Part A: CLK bloom filters must BYTE-match Python's (pure-TS SHA-256/HMAC vs
 * hashlib/hmac) across plain / parametric / parametric+HMAC / preset forms.
 *
 * Part B: runPPRL linkage parity in both protocol modes on a margin-verified
 * dataset (every pair dice >= 1e-3 from threshold): trusted_third_party
 * reports real scores; smc reveals only match bits (score == threshold).
 */
import { describe, it, expect } from "vitest";
import { applyTransform } from "../../src/core/transforms.js";
import { linkSMC, runPPRL } from "../../src/core/pprl/protocol.js";
import type { PPRLResult } from "../../src/core/pprl/protocol.js";
import fixture from "./fixtures/pprl.json" with { type: "json" };

const clkCases = fixture.clk_cases as Array<{
  value: string;
  transform: string;
  clk: string;
}>;

describe("CLK byte-parity (Python fixture)", () => {
  it.each(clkCases.map((c, i) => [i, c.transform] as const))(
    "case %i (%s) byte-matches Python",
    (i) => {
      const c = clkCases[i]!;
      expect(applyTransform(c.value, c.transform)).toBe(c.clk);
    },
  );
});

describe("PPRL linkage parity (Python fixture)", () => {
  const linkage = fixture.linkage as {
    rows_a: Array<Record<string, string>>;
    rows_b: Array<Record<string, string>>;
    threshold: number;
    shared_key: string;
    ttp: {
      match_count: number;
      total_comparisons: number;
      clusters: string[][];
      match_scores: Record<string, number>;
    };
    smc: {
      match_count: number;
      total_comparisons: number;
      clusters: string[][];
    };
  };

  function clusterKeys(result: PPRLResult): string[] {
    return result.clusters
      .map((members) =>
        members.map((m) => `${m.party}:${m.id}`).sort().join("|"),
      )
      .sort();
  }

  function expectedClusterKeys(groups: string[][]): string[] {
    return groups.map((g) => [...g].sort().join("|")).sort();
  }

  it("trusted_third_party matches Python (real scores)", () => {
    const result = runPPRL(linkage.rows_a, linkage.rows_b, {
      fields: ["name"],
      threshold: linkage.threshold,
      securityLevel: "high",
      protocol: "trusted_third_party",
      bloomFilterSize: 1024,
      hashFunctions: 30,
      ngramSize: 2,
      scorer: "dice",
    });
    expect(result.matchCount).toBe(linkage.ttp.match_count);
    expect(result.totalComparisons).toBe(linkage.ttp.total_comparisons);
    expect(clusterKeys(result)).toEqual(expectedClusterKeys(linkage.ttp.clusters));
    for (const m of result.matches) {
      const expectedScore = linkage.ttp.match_scores[`${m.idA},${m.idB}`];
      expect(expectedScore, `unexpected match (${m.idA},${m.idB})`).toBeDefined();
      expect(m.score).toBeCloseTo(expectedScore!, 9);
    }
  });

  it("smc matches Python (only match bits revealed)", () => {
    const result = linkSMC(linkage.rows_a, linkage.rows_b, {
      fields: ["name"],
      threshold: linkage.threshold,
      securityLevel: "high",
      protocol: "smc",
      salt: linkage.shared_key,
      bloomFilterSize: 1024,
      hashFunctions: 30,
      ngramSize: 2,
      scorer: "dice",
    });
    expect(result.matchCount).toBe(linkage.smc.match_count);
    expect(result.totalComparisons).toBe(linkage.smc.total_comparisons);
    expect(clusterKeys(result)).toEqual(expectedClusterKeys(linkage.smc.clusters));
    // SMC must never reveal a true similarity -- every score is the threshold.
    for (const m of result.matches) {
      expect(m.score).toBe(linkage.threshold);
    }
  });
});
