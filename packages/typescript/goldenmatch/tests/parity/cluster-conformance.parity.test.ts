/**
 * Cross-language phase-handoff conformance -- the CLUSTERING boundary.
 *
 * Measures ARTIFACT interoperability (not just surface parity): given an
 * IDENTICAL set of scored pairs + the shared defaults, does TS `buildClusters`
 * produce the IDENTICAL partition of records as Python `build_clusters`? If so,
 * the score->cluster handoff is byte-safe -- a user can score in one language
 * and cluster in the other and get the same clusters.
 *
 * Fixture authored by `scripts/emit_cluster_conformance_fixture.py` (the Python
 * oracle). Scenarios probe where divergence is plausible, especially the
 * oversized-cluster MST auto-split (incl. a tied-weakest-edge case).
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { describe, it, expect } from "vitest";
import { buildClusters } from "../../src/core/cluster.js";

const HERE = dirname(fileURLToPath(import.meta.url));
const fixture = JSON.parse(
  readFileSync(join(HERE, "fixtures", "conformance", "cluster-handoff.json"), "utf8"),
) as {
  scenarios: Array<{
    name: string;
    pairs: Array<[number, number, number]>;
    all_ids: number[];
    options: { maxClusterSize: number; weakClusterThreshold: number; autoSplit: boolean };
    py_partition: number[][];
  }>;
};

/** Canonical partition of a TS clustering: sorted list of sorted member groups. */
function tsPartition(clusters: Map<number, { members: number[] }>): number[][] {
  const groups = [...clusters.values()].map((c) => [...c.members].sort((a, b) => a - b));
  groups.sort((a, b) => {
    const n = Math.min(a.length, b.length);
    for (let i = 0; i < n; i++) {
      if (a[i]! !== b[i]!) return a[i]! - b[i]!;
    }
    return a.length - b.length;
  });
  return groups;
}

describe("cross-language cluster handoff conformance", () => {
  for (const s of fixture.scenarios) {
    it(`TS buildClusters matches Python's partition: ${s.name}`, () => {
      const clusters = buildClusters(s.pairs, s.all_ids, {
        maxClusterSize: s.options.maxClusterSize,
        weakClusterThreshold: s.options.weakClusterThreshold,
        autoSplit: s.options.autoSplit,
      });
      const ts = tsPartition(clusters as Map<number, { members: number[] }>);
      expect(ts).toEqual(s.py_partition);
    });
  }

  it("every record is accounted for in exactly one cluster (partition invariant)", () => {
    for (const s of fixture.scenarios) {
      const seen = new Set<number>();
      for (const g of s.py_partition) for (const m of g) seen.add(m);
      expect(seen.size).toBe(s.all_ids.length);
    }
  });
});
