/**
 * memory-collection.test.ts -- collection points (Phase 2.4).
 *
 * Each surface has its own describe(); the file groups them so phase 2.4
 * commits can be reviewed independently while the test suite stays cohesive.
 */
import { describe, it, expect } from "vitest";
import { ReviewQueue } from "../../src/core/review-queue.js";
import {
  unmergeCluster,
  unmergeRecord,
} from "../../src/core/cluster.js";
import type { ClusterInfo, PairKey } from "../../src/core/types.js";
import { InMemoryStore } from "../../src/core/memory/store.js";

describe("Phase 2.4.1: ReviewQueue collection", () => {
  it(
    "approve() with memoryStore writes a steward correction",
    { timeout: 15000 },
    async () => {
      const store = new InMemoryStore();
      const queue = new ReviewQueue();
      queue.add({ idA: 0, idB: 1, score: 0.9 });
      const pairId = ReviewQueue.pairIdFor(0, 1);
      await queue.approve(pairId, {
        memoryStore: store,
        df: [
          { __row_id__: 0, name: "Acme Corp", zip: "10001" },
          { __row_id__: 1, name: "Acme LLC", zip: "10001" },
        ],
        matchkeyFields: ["name"],
        matchkeyName: "identity",
      });
      expect(await store.countCorrections()).toBe(1);
      const all = await store.getCorrections();
      expect(all[0]!.source).toBe("steward");
      expect(all[0]!.trust).toBe(1.0);
      expect(all[0]!.decision).toBe("approve");
      expect(all[0]!.fieldHash.length).toBeGreaterThan(0);
      expect(all[0]!.recordHash.length).toBeGreaterThan(0);
    },
  );

  it("reject() without df writes empty-hash correction", async () => {
    const store = new InMemoryStore();
    const queue = new ReviewQueue();
    queue.add({ idA: 5, idB: 7, score: 0.7 });
    await queue.reject(ReviewQueue.pairIdFor(5, 7), { memoryStore: store });
    const all = await store.getCorrections();
    expect(all).toHaveLength(1);
    expect(all[0]!.decision).toBe("reject");
    expect(all[0]!.fieldHash).toBe("");
    expect(all[0]!.recordHash).toBe("");
  });

  it("approve() without memoryStore is a no-op for memory", async () => {
    const queue = new ReviewQueue();
    queue.add({ idA: 0, idB: 1, score: 0.9 });
    queue.approve(ReviewQueue.pairIdFor(0, 1));
    expect(queue.approved()).toHaveLength(1);
  });
});

// ---------------------------------------------------------------------------
// Phase 2.4.2: unmerge collection
// ---------------------------------------------------------------------------

function buildPairCluster(
  ids: number[],
  pairs: Array<[number, number, number]>,
): Map<number, ClusterInfo> {
  const ps = new Map<PairKey, number>();
  for (const [a, b, s] of pairs) {
    const lo = Math.min(a, b);
    const hi = Math.max(a, b);
    ps.set(`${lo}:${hi}` as PairKey, s);
  }
  const m = new Map<number, ClusterInfo>();
  m.set(0, {
    members: ids,
    size: ids.length,
    oversized: false,
    pairScores: ps,
    confidence: 1.0,
    bottleneckPair: null,
    clusterQuality: "strong",
  });
  return m;
}

// ---------------------------------------------------------------------------
// Phase 2.4.3: LLM scorer collection
// ---------------------------------------------------------------------------

import { _writeLlmCorrections, pairIndex } from "../../src/core/llm/scorer.js";
import type { Row } from "../../src/core/types.js";

describe("Phase 2.4.3: llmScorePairs collection", () => {
  it(
    "_writeLlmCorrections writes one llm correction per decided pair",
    { timeout: 15000 },
    async () => {
      const store = new InMemoryStore();
      const rowById = new Map<number, Row>([
        [0, { __row_id__: 0, name: "Acme Corp", zip: "10001" }],
        [1, { __row_id__: 1, name: "Acme LLC", zip: "10001" }],
        [2, { __row_id__: 2, name: "Beta", zip: "20002" }],
        [3, { __row_id__: 3, name: "Beta Inc", zip: "20002" }],
      ]);
      const candidates = [
        { idA: 0, idB: 1, score: 0.8 },
        { idA: 2, idB: 3, score: 0.78 },
      ];
      const decisions = new Map<number, boolean>([
        [pairIndex({ idA: 0, idB: 1, score: 0 }), true],
        [pairIndex({ idA: 2, idB: 3, score: 0 }), false],
      ]);
      await _writeLlmCorrections(candidates, decisions, rowById, {
        memoryStore: store,
        matchkeyFields: ["name"],
        matchkeyName: "identity",
      });
      const all = await store.getCorrections();
      expect(all.length).toBe(2);
      for (const c of all) {
        expect(c.source).toBe("llm");
        expect(c.trust).toBe(0.5);
        expect(c.fieldHash.length).toBeGreaterThan(0);
        expect(c.recordHash.length).toBeGreaterThan(0);
      }
      expect(all.find((c) => c.idA === 0)!.decision).toBe("approve");
      expect(all.find((c) => c.idA === 2)!.decision).toBe("reject");
    },
  );
});

describe("Phase 2.4.2: unmerge collection", () => {
  it(
    "unmergeRecord with memoryStore writes empty-hash unmerge correction",
    { timeout: 15000 },
    async () => {
      const store = new InMemoryStore();
      const clusters = buildPairCluster(
        [0, 1, 2],
        [
          [0, 1, 0.9],
          [0, 2, 0.85],
          [1, 2, 0.8],
        ],
      );
      await unmergeRecord(2, clusters, 0.0, { memoryStore: store });
      expect(await store.countCorrections()).toBeGreaterThanOrEqual(1);
      const all = await store.getCorrections();
      expect(all[0]!.source).toBe("unmerge");
      expect(all[0]!.trust).toBe(1.0);
      expect(all[0]!.fieldHash).toBe("");
      expect(all[0]!.recordHash).toBe("");
    },
  );

  it(
    "unmergeCluster with memoryStore writes one reject per former pair",
    { timeout: 15000 },
    async () => {
      const store = new InMemoryStore();
      const clusters = buildPairCluster(
        [10, 11, 12],
        [
          [10, 11, 0.9],
          [10, 12, 0.85],
          [11, 12, 0.8],
        ],
      );
      await unmergeCluster(0, clusters, { memoryStore: store });
      const all = await store.getCorrections();
      expect(all.length).toBe(3);
      for (const c of all) {
        expect(c.source).toBe("unmerge");
        expect(c.decision).toBe("reject");
        expect(c.fieldHash).toBe("");
        expect(c.recordHash).toBe("");
      }
    },
  );
});
