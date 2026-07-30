/**
 * Cross-surface parity: the cluster (MST-split + confidence) wasm kernel
 * reproduces the SAME results as the pure-TS `splitOversizedCluster` /
 * `computeClusterConfidence` — which makes the Rust `cluster-core` the source of
 * truth (pure-TS = faithful fallback). Also checks the wasm-backed
 * `buildClusters` against the Python `cluster-handoff.json` oracle (the same
 * fixture `cluster-conformance.parity.test.ts` uses), so the split is
 * Python==Rust==TS.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { describe, it, expect, afterEach } from "vitest";
import {
  splitOversizedCluster,
  computeClusterConfidence,
  buildClusters,
  pairKey,
} from "../../src/core/cluster.js";
import type { PairKey } from "../../src/core/types.js";
import {
  enableClusterWasm,
  disableClusterWasm,
} from "../../src/core/clusterWasm.js";

/** Build a canonical-keyed pairScores map from raw (a, b, score) triples. */
function ps(triples: [number, number, number][]): Map<PairKey, number> {
  const m = new Map<PairKey, number>();
  for (const [a, b, s] of triples) m.set(pairKey(a, b), s);
  return m;
}

/** Stable projection of a split result (order-independent). */
function projSplit(
  clusters: { members: number[]; confidence: number; bottleneckPair: readonly [number, number] | null }[],
): string[] {
  return clusters
    .map((c) =>
      JSON.stringify({
        members: [...c.members].sort((a, b) => a - b),
        confidence: Math.round(c.confidence * 1e9) / 1e9,
        bottleneck: c.bottleneckPair,
      }),
    )
    .sort();
}

interface SplitCase {
  name: string;
  members: number[];
  pairs: [number, number, number][];
}

const SPLIT_CASES: SplitCase[] = [
  {
    name: "simple oversized path splits at weakest link",
    members: [0, 1, 2, 3],
    pairs: [
      [0, 1, 0.9],
      [1, 2, 0.1],
      [2, 3, 0.8],
    ],
  },
  {
    name: "tied-weakest-edge is deterministic (first-min wins)",
    members: [0, 1, 2, 3],
    pairs: [
      [0, 1, 0.9],
      [1, 2, 0.5],
      [2, 3, 0.5],
    ],
  },
  {
    name: "size-1 cluster is unsplittable",
    members: [7],
    pairs: [],
  },
  {
    name: "no edges is unsplittable",
    members: [0, 1, 2],
    pairs: [],
  },
  {
    name: "star cluster splits off the weakest spoke",
    members: [0, 1, 2, 3, 4],
    pairs: [
      [0, 1, 0.95],
      [0, 2, 0.9],
      [0, 3, 0.2],
      [0, 4, 0.85],
    ],
  },
];

interface ConfCase {
  name: string;
  size: number;
  pairs: [number, number, number][];
}

const CONF_CASES: ConfCase[] = [
  {
    name: "fully-connected triangle",
    size: 3,
    pairs: [
      [0, 1, 0.8],
      [0, 2, 0.6],
      [1, 2, 0.4],
    ],
  },
  {
    name: "tied bottleneck (first-min wins)",
    size: 3,
    pairs: [
      [0, 1, 0.4],
      [0, 2, 0.4],
      [1, 2, 0.9],
    ],
  },
  { name: "size-1 => confidence 1.0", size: 1, pairs: [] },
  { name: "size>1 with no edges => confidence 0.0", size: 3, pairs: [] },
];

describe("cluster-wasm parity — wasm == pure-TS", () => {
  afterEach(() => disableClusterWasm());

  for (const c of SPLIT_CASES) {
    it(`splitOversizedCluster: ${c.name}`, () => {
      disableClusterWasm();
      const pure = projSplit(splitOversizedCluster(c.members, ps(c.pairs)));
      enableClusterWasm();
      const wasm = projSplit(splitOversizedCluster(c.members, ps(c.pairs)));
      expect(wasm).toEqual(pure);
    });
  }

  for (const c of CONF_CASES) {
    it(`computeClusterConfidence: ${c.name}`, () => {
      disableClusterWasm();
      const pure = computeClusterConfidence(ps(c.pairs), c.size);
      enableClusterWasm();
      const wasm = computeClusterConfidence(ps(c.pairs), c.size);
      // Bit-for-bit: same float-sum order + first-min tie-break.
      expect(wasm).toEqual(pure);
    });
  }
});

// --- Python oracle: wasm-backed buildClusters == Python's partition ----------
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

function tsPartition(clusters: ReadonlyMap<number, { members: readonly number[] }>): number[][] {
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

describe("cluster-wasm parity — wasm-backed buildClusters matches the Python oracle", () => {
  afterEach(() => disableClusterWasm());

  for (const s of fixture.scenarios) {
    it(`wasm split reproduces Python's partition: ${s.name}`, () => {
      enableClusterWasm();
      const clusters = buildClusters(s.pairs, s.all_ids, {
        maxClusterSize: s.options.maxClusterSize,
        weakClusterThreshold: s.options.weakClusterThreshold,
        autoSplit: s.options.autoSplit,
      });
      expect(tsPartition(clusters)).toEqual(s.py_partition);
    });
  }
});
