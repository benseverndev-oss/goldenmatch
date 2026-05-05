/**
 * Tests for MemoryLearner -- threshold tuning via weighted grid search.
 * Ports cases from packages/python/goldenmatch/tests/test_learner.py.
 */
import { describe, it, expect } from "vitest";
import { InMemoryStore } from "../../src/core/memory/store.js";
import { MemoryLearner } from "../../src/core/memory/learner.js";
import type { Correction, Decision } from "../../src/core/memory/types.js";

function makeCorrection(
  id: string,
  idA: number,
  idB: number,
  decision: Decision,
  originalScore: number,
  matchkeyName: string,
  trust = 1.0,
): Correction {
  return {
    id,
    idA,
    idB,
    decision,
    source: "steward",
    trust,
    fieldHash: `fh-${id}`,
    recordHash: `rh-${id}`,
    originalScore,
    matchkeyName,
    reason: null,
    dataset: matchkeyName,
    createdAt: new Date(),
  };
}

async function seedCorrections(
  store: InMemoryStore,
  matchkey: string,
  approvedScores: number[],
  rejectedScores: number[],
): Promise<void> {
  for (let i = 0; i < approvedScores.length; i++) {
    await store.addCorrection(
      makeCorrection(`a-${matchkey}-${i}`, i * 2, i * 2 + 1, "approve", approvedScores[i]!, matchkey),
    );
  }
  for (let i = 0; i < rejectedScores.length; i++) {
    await store.addCorrection(
      makeCorrection(
        `r-${matchkey}-${i}`,
        1000 + i * 2,
        1000 + i * 2 + 1,
        "reject",
        rejectedScores[i]!,
        matchkey,
      ),
    );
  }
}

describe("MemoryLearner.hasNewCorrections", () => {
  it("returns false when store has no corrections", async () => {
    const store = new InMemoryStore();
    const learner = new MemoryLearner(store);
    expect(await learner.hasNewCorrections()).toBe(false);
  });

  it("returns true when corrections exist and no learning has happened", async () => {
    const store = new InMemoryStore();
    await seedCorrections(store, "mk1", [0.9], [0.3]);
    const learner = new MemoryLearner(store);
    expect(await learner.hasNewCorrections()).toBe(true);
  });

  it("returns false after learning consumes all corrections", async () => {
    const store = new InMemoryStore();
    await seedCorrections(store, "mk1", Array(6).fill(0.9), Array(5).fill(0.3));
    const learner = new MemoryLearner(store);
    await learner.learn();
    expect(await learner.hasNewCorrections()).toBe(false);
  });
});

describe("MemoryLearner.learn -- threshold tuning", () => {
  it("returns empty when fewer than thresholdMinCorrections", async () => {
    const store = new InMemoryStore();
    await seedCorrections(store, "mk1", Array(5).fill(0.9), Array(3).fill(0.3));
    const learner = new MemoryLearner(store);
    const result = await learner.learn();
    expect(result.length).toBe(0);
  });

  it("computes threshold via weighted grid search at 10+ corrections (clean separation)", async () => {
    const store = new InMemoryStore();
    // 6 approves at 0.85, 6 rejects at 0.55 -> threshold lands between
    await seedCorrections(store, "mk1", Array(6).fill(0.85), Array(6).fill(0.55));
    const learner = new MemoryLearner(store);
    const result = await learner.learn();
    expect(result.length).toBe(1);
    const adj = result[0]!;
    expect(adj.matchkeyName).toBe("mk1");
    expect(adj.threshold).not.toBeNull();
    expect(adj.threshold!).toBeGreaterThan(0.55);
    expect(adj.threshold!).toBeLessThan(0.85);
    expect(adj.sampleSize).toBe(12);
  });

  it("computes threshold via weighted grid search with overlapping scores", async () => {
    const store = new InMemoryStore();
    await seedCorrections(
      store,
      "mk1",
      [0.8, 0.82, 0.85, 0.88, 0.9, 0.92],
      [0.78, 0.81, 0.6, 0.55, 0.5, 0.45],
    );
    const learner = new MemoryLearner(store);
    const result = await learner.learn();
    expect(result.length).toBe(1);
    expect(result[0]!.threshold!).toBeGreaterThan(0.5);
    expect(result[0]!.threshold!).toBeLessThan(0.95);
  });

  it("skips matchkey with all approves or all rejects", async () => {
    const store = new InMemoryStore();
    await seedCorrections(store, "mk1", Array(15).fill(0.9), []);
    const learner = new MemoryLearner(store);
    const result = await learner.learn();
    expect(result.length).toBe(0);
  });

  it("learns per matchkey independently", async () => {
    const store = new InMemoryStore();
    await seedCorrections(store, "mk1", Array(6).fill(0.9), Array(5).fill(0.3));
    await seedCorrections(store, "mk2", Array(7).fill(0.8), Array(4).fill(0.4));
    const learner = new MemoryLearner(store);
    const result = await learner.learn();
    const names = new Set(result.map((r) => r.matchkeyName));
    expect(names.has("mk1")).toBe(true);
    expect(names.has("mk2")).toBe(true);
  });

  it("filters by matchkey name when provided", async () => {
    const store = new InMemoryStore();
    await seedCorrections(store, "mk1", Array(6).fill(0.9), Array(5).fill(0.3));
    await seedCorrections(store, "mk2", Array(7).fill(0.8), Array(4).fill(0.4));
    const learner = new MemoryLearner(store);
    const result = await learner.learn("mk1");
    expect(result.length).toBe(1);
    expect(result[0]!.matchkeyName).toBe("mk1");
  });

  it("field weights remain null in v0.4.0 (stub)", async () => {
    const store = new InMemoryStore();
    // 30 approves + 25 rejects = 55, beyond weightsMinCorrections=50
    await seedCorrections(store, "mk1", Array(30).fill(0.9), Array(25).fill(0.3));
    const learner = new MemoryLearner(store);
    const result = await learner.learn();
    expect(result.length).toBe(1);
    expect(result[0]!.fieldWeights).toBeNull();
  });
});

describe("MemoryLearner -- weighted misclassification cost", () => {
  it("higher-trust corrections weigh more in threshold selection", async () => {
    // Build a case where low-trust outliers would shift threshold if equally weighted
    const store = new InMemoryStore();
    // 6 high-trust approves at 0.85
    for (let i = 0; i < 6; i++) {
      await store.addCorrection(
        makeCorrection(`a-${i}`, i * 2, i * 2 + 1, "approve", 0.85, "mk1", 1.0),
      );
    }
    // 6 high-trust rejects at 0.55
    for (let i = 0; i < 6; i++) {
      await store.addCorrection(
        makeCorrection(`r-${i}`, 1000 + i * 2, 1000 + i * 2 + 1, "reject", 0.55, "mk1", 1.0),
      );
    }
    const learner = new MemoryLearner(store);
    const result = await learner.learn();
    expect(result.length).toBe(1);
    // Clean separation: threshold = (0.55 + 0.85) / 2 = 0.70
    expect(result[0]!.threshold!).toBeCloseTo(0.7, 5);
  });
});
