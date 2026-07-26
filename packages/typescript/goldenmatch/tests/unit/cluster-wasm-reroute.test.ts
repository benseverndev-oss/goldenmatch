/**
 * Reroute equivalence for the split + confidence steps: with the cluster wasm
 * backend enabled, `splitOversizedCluster` / `computeClusterConfidence` (and,
 * through them, `buildClusters`) run the shared `cluster-core` kernel; disabled,
 * the pure-TS impls. Both must produce IDENTICAL results — making the Rust core
 * the source of truth (pure-TS = faithful fallback) and closing the divergence
 * risk of the hand-written kernels.
 */
import { describe, it, expect, afterEach } from "vitest";
import { buildClusters } from "../../src/core/cluster.js";
import type { ClusterInfo } from "../../src/core/types.js";
import {
  enableClusterWasm,
  disableClusterWasm,
} from "../../src/core/clusterWasm.js";

// Full projection incl. confidence + split quality (the fields the two kernels
// drive) so the reroute is proven equivalent end-to-end, not just on partition.
function project(clusters: Map<number, ClusterInfo>): string[] {
  return [...clusters.values()]
    .map((c) =>
      JSON.stringify({
        members: [...c.members].sort((a, b) => a - b),
        size: c.size,
        oversized: c.oversized,
        confidence: Math.round(c.confidence * 1e9) / 1e9,
        quality: c.clusterQuality,
        bottleneck: c.bottleneckPair,
      }),
    )
    .sort();
}

// A dataset with a genuinely OVERSIZED cluster (maxClusterSize small) so the
// MST-split path is exercised, plus tight cliques + a singleton.
const ALL_IDS = Array.from({ length: 20 }, (_, i) => i);
const PAIRS: [number, number, number][] = [
  // a chain 0..7 (weakest link 3-4) — oversized under maxClusterSize=5, splits
  [0, 1, 0.95],
  [1, 2, 0.94],
  [2, 3, 0.93],
  [3, 4, 0.2],
  [4, 5, 0.92],
  [5, 6, 0.91],
  [6, 7, 0.9],
  // clique {10,11,12}
  [10, 11, 0.88],
  [10, 12, 0.86],
  [11, 12, 0.87],
  // pair {14,15}
  [14, 15, 0.99],
  // 8,9,13,16..19 stay singletons
];

function run(): Map<number, ClusterInfo> {
  return buildClusters(PAIRS, ALL_IDS, { maxClusterSize: 5 });
}

describe("cluster wasm reroute — split + confidence equivalence", () => {
  afterEach(() => disableClusterWasm());

  it("buildClusters (with MST split): wasm == pure-TS", () => {
    disableClusterWasm();
    const pureTs = project(run());
    enableClusterWasm();
    const wasm = project(run());

    expect(wasm).toEqual(pureTs);

    // The oversized chain actually split, so the split reroute is exercised.
    const split = [...run().values()].filter((c) => c.clusterQuality === "split");
    expect(split.length).toBeGreaterThanOrEqual(2);
  });
});
