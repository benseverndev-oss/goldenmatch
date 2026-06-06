/**
 * config-optimizer.test.ts -- cross-language parity for the config optimizer.
 *
 * Part A: GridProposer + CoordinateDescentProposer must generate the same
 * candidate labels per round as Python (scorer tuple pinned without qgram).
 *
 * Part B: the optimizeConfig loop (objective="f1", proposer="grid") replays a
 * margin-verified dataset (every pair score >= 0.10 from every swept
 * threshold, so scorer parity cannot flip a merge) and must produce identical
 * per-trial f1 scores, the same best label, and the same round count.
 */
import { describe, it, expect } from "vitest";
import {
  CoordinateDescentProposer,
  GridProposer,
  optimizeConfig,
  type SearchState,
} from "../../src/core/config-optimizer.js";
import type { GoldenMatchConfig, Row } from "../../src/core/types.js";
import fixture from "./fixtures/config-optimizer.json" with { type: "json" };

function editsBaseConfig(): GoldenMatchConfig {
  return {
    matchkeys: [
      {
        name: "identity",
        type: "weighted",
        threshold: 0.85,
        fields: [
          { field: "name", transforms: [], scorer: "jaro_winkler", weight: 1.0 },
          { field: "email", transforms: [], scorer: "jaro_winkler", weight: 0.8 },
        ],
      },
      {
        name: "email_exact",
        type: "exact",
        fields: [{ field: "email", transforms: [], scorer: "exact", weight: 1.0 }],
      },
    ],
    blocking: {
      strategy: "static",
      keys: [
        { fields: ["email"], transforms: ["lowercase"] },
        { fields: ["zip"], transforms: [] },
      ],
      maxBlockSize: 1000,
      skipOversized: true,
    },
  };
}

function emptyState(base: GoldenMatchConfig): SearchState {
  return { baseConfig: base, objective: "f1", trials: [], round: 0 };
}

describe("proposer parity (Python fixture)", () => {
  const expected = fixture.proposers as {
    pinned_scorers: string[];
    blocking_key_adds: string[][];
    grid_labels: string[];
    coordinate_rounds: string[][];
  };

  it("GridProposer proposes the same candidate labels", () => {
    const grid = new GridProposer();
    const labels = grid.propose(emptyState(editsBaseConfig())).map(([l]) => l);
    expect(labels).toEqual(expected.grid_labels);
  });

  it("CoordinateDescentProposer proposes the same labels per round", () => {
    const coord = new CoordinateDescentProposer({
      scorers: expected.pinned_scorers,
      blockingKeyAdds: expected.blocking_key_adds,
    });
    const state = emptyState(editsBaseConfig());
    const rounds: string[][] = [];
    for (;;) {
      const cands = coord.propose(state);
      if (cands.length === 0) break;
      rounds.push(cands.map(([l]) => l));
    }
    expect(rounds).toEqual(expected.coordinate_rounds);
  });
});

describe("optimizeConfig loop parity (Python fixture)", () => {
  const loop = fixture.loop as unknown as {
    rows: Array<Record<string, string>>;
    base_threshold: number;
    offsets: number[];
    ground_truth: Array<[number, number]>;
    expected: {
      trials: Array<{ label: string; score: number; error: string | null }>;
      best_label: string;
      rounds: number;
      objective: string;
    };
  };

  it("grid loop produces identical trials, scores, and best label", async () => {
    const rows: Row[] = loop.rows.map((r, i) => ({
      ...r,
      __row_id__: i,
      __source__: "s",
    })) as Row[];
    const baseConfig: GoldenMatchConfig = {
      matchkeys: [
        {
          name: "identity",
          type: "weighted",
          threshold: loop.base_threshold,
          fields: [
            { field: "name", transforms: [], scorer: "jaro_winkler", weight: 1.0 },
          ],
        },
      ],
      blocking: {
        strategy: "static",
        keys: [{ fields: ["city"], transforms: [] }],
        maxBlockSize: 1000,
        skipOversized: true,
      },
    };

    const result = await optimizeConfig(rows, {
      baseConfig,
      groundTruth: loop.ground_truth,
      objective: "f1",
      proposer: "grid",
      thresholdOffsets: loop.offsets,
    });

    expect(result.trials.map((t) => t.label)).toEqual(
      loop.expected.trials.map((t) => t.label),
    );
    for (let i = 0; i < result.trials.length; i++) {
      expect(result.trials[i]!.error).toBe(loop.expected.trials[i]!.error);
      expect(result.trials[i]!.score).toBeCloseTo(loop.expected.trials[i]!.score, 10);
    }
    expect(result.bestTrial.label).toBe(loop.expected.best_label);
    expect(result.rounds).toBe(loop.expected.rounds);
    expect(result.objective).toBe(loop.expected.objective);
  });
});
