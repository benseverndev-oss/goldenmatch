/**
 * Cross-language END-TO-END split-run conformance.
 *
 * The cluster-handoff harness proved: identical scored pairs -> identical
 * clusters. This runs a REAL pipeline and answers:
 *
 *  (a) HANDOFF FIDELITY: TS `buildClusters` on Python's REAL pipeline scored
 *      pairs reproduces Python's own final clusters -> "score in Python, cluster
 *      in TS" == all-Python. This is the seamless-handoff proof on real output.
 *
 *  (b) INDEPENDENT AGREEMENT: a full all-TS run vs the all-Python run on the
 *      same data + explicit config -> do they reach the same clusters, and do
 *      the scored pairs agree (or does the 4dp scoring tolerance flip a pair)?
 *
 * Blocking is neutralized (every row shares one key) so any divergence is
 * scoring/standardize, not a different candidate set.
 *
 * Fixture authored by `scripts/emit_split_run_fixture.py` (the Python oracle).
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { describe, it, expect } from "vitest";
import { dedupe } from "../../src/core/api.js";
import { buildClusters } from "../../src/core/cluster.js";
import { makeMatchkeyConfig, makeMatchkeyField } from "../../src/core/types.js";
import type { GoldenMatchConfig, Row } from "../../src/core/types.js";

const HERE = dirname(fileURLToPath(import.meta.url));
const fx = JSON.parse(
  readFileSync(join(HERE, "fixtures", "conformance", "split-run.json"), "utf8"),
) as {
  rows: Array<Record<string, unknown>>;
  threshold: number;
  all_ids: number[];
  py_scored_pairs: Array<[number, number, number]>;
  py_partition: number[][];
};

function partition(clusters: Map<number, { members: number[] }>): number[][] {
  const g = [...clusters.values()].map((c) => [...c.members].sort((a, b) => a - b));
  g.sort((a, b) => {
    const n = Math.min(a.length, b.length);
    for (let i = 0; i < n; i++) if (a[i]! !== b[i]!) return a[i]! - b[i]!;
    return a.length - b.length;
  });
  return g;
}

const TS_CONFIG: GoldenMatchConfig = {
  blocking: {
    strategy: "static",
    keys: [{ fields: ["blk"], transforms: [] }],
    maxBlockSize: 1000,
    skipOversized: false,
  },
  matchkeys: [
    makeMatchkeyConfig({
      name: "name_mk",
      type: "weighted",
      threshold: fx.threshold,
      fields: [makeMatchkeyField({ field: "name", scorer: "jaro_winkler", weight: 1.0 })],
    }),
  ],
};

describe("cross-language end-to-end split-run conformance", () => {
  it("(a) handoff fidelity: TS clusters Python's real scored pairs == all-Python clusters", () => {
    const clusters = buildClusters(fx.py_scored_pairs, fx.all_ids, {});
    expect(partition(clusters as Map<number, { members: number[] }>)).toEqual(fx.py_partition);
  });

  it("(b) independent all-TS run agrees with all-Python (clusters + scored pairs)", async () => {
    const rows = fx.rows as Row[];
    const r = await dedupe(rows, { config: TS_CONFIG });

    // --- clusters: independent runs reach the same partition ---
    expect(partition(r.clusters as Map<number, { members: number[] }>)).toEqual(fx.py_partition);

    // --- scored pairs: same set + quantify the scoring boundary ---
    const key = (a: number, b: number): string => `${Math.min(a, b)}-${Math.max(a, b)}`;
    const py = new Map(fx.py_scored_pairs.map((p) => [key(p[0], p[1]), p[2]]));
    const ts = new Map(r.scoredPairs.map((p) => [key(p.idA, p.idB), p.score]));
    // Same pair set above threshold (no pair present in one language but not the other).
    expect([...ts.keys()].sort()).toEqual([...py.keys()].sort());
    // No threshold-flip and bounded delta on the shared pairs.
    let maxDelta = 0;
    for (const [k, pv] of py) {
      const tv = ts.get(k)!;
      maxDelta = Math.max(maxDelta, Math.abs(pv - tv));
      // neither side crosses the threshold that the other doesn't
      expect(pv >= fx.threshold).toBe(tv >= fx.threshold);
    }
    // jaro_winkler is rapidfuzz-aligned across languages -> tight agreement.
    expect(maxDelta).toBeLessThan(1e-3);
  });
});
