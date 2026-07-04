/**
 * Reroute equivalence for the clustering step: with the graph wasm backend
 * enabled, `buildClusters` computes its connected components via the shared
 * graph-core kernel; disabled, via the pure-TS union-find. Both must produce
 * IDENTICAL clusters (the CC partition is unique) — which makes the Rust core
 * the source of truth (pure-TS = faithful fallback) and closes the divergence
 * risk of the hand-written union-find.
 */
import { describe, it, expect, afterEach } from "vitest";
import { buildClusters } from "../../src/core/cluster.js";
import type { ClusterInfo } from "../../src/core/types.js";
import { enableGraphWasm, disableGraphWasm } from "../../src/core/graphWasm.js";

// A stable, comparable projection of the salient cluster fields.
function project(clusters: Map<number, ClusterInfo>): string[] {
  return [...clusters.values()]
    .map((c) =>
      JSON.stringify({
        members: [...c.members].sort((a, b) => a - b),
        size: c.size,
        oversized: c.oversized,
        confidence: Math.round(c.confidence * 1e6) / 1e6,
        quality: c.clusterQuality,
        bottleneck: c.bottleneckPair,
      }),
    )
    .sort();
}

// 40 rows in a few entity groups, with a distinct component and a singleton.
const ALL_IDS = Array.from({ length: 40 }, (_, i) => i);
const PAIRS: [number, number, number][] = [
  // component {0,1,2,3}
  [0, 1, 0.95], [1, 2, 0.92], [2, 3, 0.9], [0, 3, 0.88],
  // component {4,5}
  [4, 5, 0.97],
  // component {6,7,8}
  [6, 7, 0.85], [7, 8, 0.83],
  // a long chain {10..15}
  [10, 11, 0.9], [11, 12, 0.9], [12, 13, 0.9], [13, 14, 0.9], [14, 15, 0.9],
  // 9, 16..39 stay singletons
];

function run(): Map<number, ClusterInfo> {
  return buildClusters(PAIRS, ALL_IDS);
}

describe("graph wasm reroute — buildClusters connected-components equivalence", () => {
  afterEach(() => disableGraphWasm());

  it("buildClusters: wasm == pure-TS", () => {
    disableGraphWasm();
    const pureTs = project(run());
    enableGraphWasm();
    const wasm = project(run());

    expect(wasm).toEqual(pureTs);
    // Real multi-member clusters were formed, so the reroute is exercised.
    const nonSingleton = [...run().values()].filter((c) => c.size > 1);
    expect(nonSingleton.length).toBeGreaterThanOrEqual(4);
  });
});
