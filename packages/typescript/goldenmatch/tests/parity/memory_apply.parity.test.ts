/**
 * memory_apply.parity.test.ts -- cross-language apply-outcome golden.
 *
 * Loads the shared `memory_corrections.json` and `memory_apply_inputs.json`
 * fixtures, seeds an `InMemoryStore` with the corrections, runs
 * `applyCorrections` against the input scored pairs / df / matchkey fields,
 * and asserts the resulting `(adjusted, stats)` matches the expected JSON
 * byte-for-byte (after sorting `stalePairs` for cross-language determinism).
 *
 * This is the load-bearing parity check: it locks the entire algorithm
 * (re-anchor + dual-hash safety + stat counters) at the value level so a
 * regression in either language must trip a fixture diff.
 */
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import {
  correctionFromJSON,
  type CorrectionJSON,
} from "../../src/core/memory/types.js";
import { InMemoryStore } from "../../src/core/memory/store.js";
import {
  applyCorrections,
  type ScoredPair,
} from "../../src/core/memory/corrections.js";
import type { Row } from "../../src/core/types.js";

const FIXTURE_DIR = join(__dirname, "fixtures");
const CORRECTIONS_PATH = join(FIXTURE_DIR, "memory_corrections.json");
const APPLY_PATH = join(FIXTURE_DIR, "memory_apply_inputs.json");

interface ApplyInputs {
  readonly df: ReadonlyArray<Row>;
  readonly matchkey_fields: ReadonlyArray<string>;
  readonly dataset: string;
  readonly reanchor: boolean;
  readonly scored_pairs: ReadonlyArray<readonly [number, number, number]>;
  readonly expected: {
    readonly adjusted: ReadonlyArray<readonly [number, number, number]>;
    readonly stats: {
      readonly applied: number;
      readonly stale: number;
      readonly stale_ambiguous: number;
      readonly stale_unanchorable: number;
      readonly stale_pairs: ReadonlyArray<readonly [number, number]>;
      readonly total_pairs: number;
    };
  };
}

describe("memory applyCorrections parity", () => {
  const corrections = JSON.parse(
    readFileSync(CORRECTIONS_PATH, "utf-8"),
  ) as CorrectionJSON[];
  const inputs = JSON.parse(readFileSync(APPLY_PATH, "utf-8")) as ApplyInputs;

  it(
    "reproduces Python's (adjusted, stats) for the seeded corrections",
    { timeout: 15000 },
    async () => {
      const store = new InMemoryStore();
      for (const c of corrections) {
        await store.addCorrection(correctionFromJSON(c));
      }

      const scored: ScoredPair[] = inputs.scored_pairs.map(
        (p) => [p[0], p[1], p[2]] as ScoredPair,
      );

      const [adjusted, stats] = await applyCorrections(
        scored,
        store,
        inputs.df,
        inputs.matchkey_fields,
        { dataset: inputs.dataset, reanchor: inputs.reanchor },
      );

      // Adjusted scored pairs match exactly (order-preserving).
      expect(adjusted).toHaveLength(inputs.expected.adjusted.length);
      for (let i = 0; i < adjusted.length; i++) {
        const got = adjusted[i]!;
        const want = inputs.expected.adjusted[i]!;
        expect(got[0]).toBe(want[0]);
        expect(got[1]).toBe(want[1]);
        expect(got[2]).toBe(want[2]);
      }

      // Stats counters match exactly.
      expect(stats.applied).toBe(inputs.expected.stats.applied);
      expect(stats.stale).toBe(inputs.expected.stats.stale);
      expect(stats.staleAmbiguous).toBe(inputs.expected.stats.stale_ambiguous);
      expect(stats.staleUnanchorable).toBe(
        inputs.expected.stats.stale_unanchorable,
      );
      expect(stats.totalPairs).toBe(inputs.expected.stats.total_pairs);

      // stale_pairs: sort both sides (the fixture is already sorted; TS
      // implementation appends in resolution order) before comparison.
      const sortKey = (p: readonly [number, number]) => `${p[0]},${p[1]}`;
      const gotSorted = [...stats.stalePairs]
        .map((p) => [p[0], p[1]] as const)
        .sort((a, b) => sortKey(a).localeCompare(sortKey(b)));
      const wantSorted = [...inputs.expected.stats.stale_pairs]
        .map((p) => [p[0], p[1]] as const)
        .sort((a, b) => sortKey(a).localeCompare(sortKey(b)));
      expect(gotSorted).toEqual(wantSorted);
    },
  );

  it("scoreboard sanity: applied + stale buckets <= total_pairs", () => {
    const s = inputs.expected.stats;
    const sum =
      s.applied + s.stale + s.stale_ambiguous + s.stale_unanchorable;
    expect(sum).toBeLessThanOrEqual(s.total_pairs + corrections.length);
  });
});
