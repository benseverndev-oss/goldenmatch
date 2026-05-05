/**
 * memory-pipeline.test.ts -- pipeline + memory integration (Phase 2.2).
 */
import { describe, it, expect } from "vitest";
import type {
  DedupeResult,
  MatchResult,
  GoldenMatchConfig,
} from "../../src/core/types.js";
import type { CorrectionStats } from "../../src/core/memory/types.js";
import { dedupe, match } from "../../src/core/api.js";
import { InMemoryStore } from "../../src/core/memory/store.js";

describe("Phase 2.1: memoryStats result field", () => {
  it("DedupeResult shape accepts memoryStats", () => {
    const stats: CorrectionStats = {
      applied: 0,
      stale: 0,
      staleAmbiguous: 0,
      staleUnanchorable: 0,
      stalePairs: [],
      totalPairs: 0,
    };
    const r: DedupeResult = {
      goldenRecords: [],
      clusters: new Map(),
      dupes: [],
      unique: [],
      stats: {
        totalRecords: 0,
        totalClusters: 0,
        matchRate: 0,
        matchedRecords: 0,
        uniqueRecords: 0,
      },
      scoredPairs: [],
      config: {} as GoldenMatchConfig,
      memoryStats: stats,
    };
    expect(r.memoryStats?.applied).toBe(0);
  });

  it("MatchResult shape accepts memoryStats", () => {
    const r: MatchResult = {
      matched: [],
      unmatched: [],
      stats: {},
      memoryStats: null,
    };
    expect(r.memoryStats).toBeNull();
  });
});

describe("Phase 2.2: pipeline async memory hooks", () => {
  it(
    "seeded reject correction overrides scored pair on re-run",
    { timeout: 15000 },
    async () => {
      const store = new InMemoryStore();
      // Empty hashes => short-circuit dual-hash check, always apply when row
      // ids match. Pair (0, 1) will be forced to score 0.0.
      await store.addCorrection({
        id: "x",
        idA: 0,
        idB: 1,
        decision: "reject",
        source: "steward",
        trust: 1.0,
        fieldHash: "",
        recordHash: "",
        originalScore: 0.95,
        matchkeyName: null,
        reason: null,
        dataset: null,
        createdAt: new Date(),
      });

      const result = await dedupe(
        [
          { name: "Acme Corp", zip: "10001" },
          { name: "Acme LLC", zip: "10001" },
          { name: "Beta", zip: "20002" },
        ],
        {
          fuzzy: { name: 1.0 },
          blocking: ["zip"],
          threshold: 0.5,
          memoryStore: store,
          memoryConfig: { enabled: true },
        },
      );

      expect(result.memoryStats).not.toBeNull();
      expect(result.memoryStats?.applied).toBe(1);
    },
  );

  it("memoryStats is null when memory disabled", { timeout: 15000 }, async () => {
    const result = await dedupe(
      [
        { name: "Acme Corp", zip: "10001" },
        { name: "Acme LLC", zip: "10001" },
      ],
      { fuzzy: { name: 1.0 }, blocking: ["zip"], threshold: 0.5 },
    );
    expect(result.memoryStats == null).toBe(true);
  });

  it(
    "store is not consulted when memory disabled",
    { timeout: 15000 },
    async () => {
      const store = new InMemoryStore();
      let consulted = 0;
      const proxy = new Proxy(store, {
        get(target, prop, recv) {
          if (prop === "getCorrections" || prop === "countCorrections") {
            consulted += 1;
          }
          return Reflect.get(target, prop, recv);
        },
      });
      await dedupe(
        [
          { name: "Acme Corp", zip: "10001" },
          { name: "Acme LLC", zip: "10001" },
        ],
        {
          fuzzy: { name: 1.0 },
          blocking: ["zip"],
          threshold: 0.5,
          memoryStore: proxy as InMemoryStore,
          // memoryConfig deliberately omitted (memory disabled).
        },
      );
      expect(consulted).toBe(0);
    },
  );

  it(
    "pipeline survives a corrupt store and reports failure",
    { timeout: 15000 },
    async () => {
      // Fake store whose getCorrections always throws.
      const corrupt: InMemoryStore = new InMemoryStore();
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      (corrupt as any).getCorrections = () => {
        throw new Error("simulated DB corruption");
      };

      const result = await dedupe(
        [
          { name: "Acme Corp", zip: "10001" },
          { name: "Acme LLC", zip: "10001" },
        ],
        {
          fuzzy: { name: 1.0 },
          blocking: ["zip"],
          threshold: 0.5,
          memoryStore: corrupt,
          memoryConfig: { enabled: true },
        },
      );
      expect(result.memoryStats).not.toBeNull();
      expect(result.memoryStats?.failed).toBe(true);
    },
  );

  it("match() returns memoryStats null when memory disabled", async () => {
    const result = await match(
      [{ name: "Acme", zip: "10001" }],
      [{ name: "Beta", zip: "20002" }],
      { fuzzy: { name: 1.0 }, blocking: ["zip"], threshold: 0.5 },
    );
    expect(result.memoryStats == null).toBe(true);
  });
});
