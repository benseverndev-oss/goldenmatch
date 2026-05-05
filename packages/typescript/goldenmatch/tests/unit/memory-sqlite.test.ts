/**
 * memory-sqlite.test.ts -- SqliteMemoryStore unit tests.
 *
 * Mirrors the parity contract with Python's MemoryStore at
 * packages/python/goldenmatch/goldenmatch/core/memory/store.py:85-249.
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import type { Correction, LearnedAdjustment } from "../../src/core/memory/types.js";
import { SqliteMemoryStore } from "../../src/node/memory/sqlite-store.js";

let nextCid = 0;
function makeCorrection(overrides: Partial<Correction> = {}): Correction {
  nextCid += 1;
  return {
    id: `auto-${nextCid}`,
    idA: 1,
    idB: 2,
    decision: "approve",
    source: "agent",
    trust: 0.5,
    fieldHash: "field-hash",
    recordHash: "rec-a:rec-b",
    originalScore: 0.8,
    matchkeyName: "identity",
    reason: null,
    dataset: null,
    createdAt: new Date("2026-05-04T12:00:00.000Z"),
    ...overrides,
  };
}

let dir: string;
let dbPath: string;

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "gm-sqlite-"));
  dbPath = join(dir, "memory.db");
});

afterEach(() => {
  try {
    rmSync(dir, { recursive: true, force: true });
  } catch {
    // Windows occasionally holds the SQLite handle a beat past close; ignore.
  }
});

describe("SqliteMemoryStore.init", () => {
  it("creates corrections and adjustments tables matching the Python schema", async () => {
    const store = new SqliteMemoryStore({
      enabled: true,
      backend: "sqlite",
      path: dbPath,
      learning: { thresholdMinCorrections: 10, weightsMinCorrections: 50 },
    });
    await store.init();
    // Reach into the underlying handle to verify schema. Tests are allowed to
    // use the raw db since they verify SQL-level parity with Python.
    const db = (store as unknown as { db: any }).db;
    const tables = db
      .prepare("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
      .all()
      .map((r: { name: string }) => r.name);
    expect(tables).toContain("corrections");
    expect(tables).toContain("adjustments");

    const cols = db.prepare("PRAGMA table_info(corrections)").all().map(
      (r: { name: string }) => r.name,
    );
    for (const expected of [
      "id",
      "id_a",
      "id_b",
      "decision",
      "source",
      "trust",
      "field_hash",
      "record_hash",
      "original_score",
      "matchkey_name",
      "reason",
      "dataset",
      "created_at",
    ]) {
      expect(cols).toContain(expected);
    }
    await store.close?.();
  });
});

describe("SqliteMemoryStore corrections CRUD", () => {
  it("round-trips all correction fields including ISO -> Date for created_at", async () => {
    const store = new SqliteMemoryStore({
      enabled: true,
      backend: "sqlite",
      path: dbPath,
      learning: { thresholdMinCorrections: 10, weightsMinCorrections: 50 },
    });
    await store.init();
    const c = makeCorrection({
      id: "abc-123",
      idA: 5,
      idB: 7,
      decision: "reject",
      source: "steward",
      trust: 1.0,
      fieldHash: "fh1",
      recordHash: "rh-a:rh-b",
      originalScore: 0.92,
      matchkeyName: "identity",
      reason: "manual",
      dataset: "customers",
      createdAt: new Date("2026-05-04T12:00:00.000Z"),
    });
    await store.addCorrection(c);
    const fetched = await store.getCorrections();
    expect(fetched.length).toBe(1);
    const got = fetched[0]!;
    expect(got.id).toBe("abc-123");
    expect(got.idA).toBe(5);
    expect(got.idB).toBe(7);
    expect(got.decision).toBe("reject");
    expect(got.source).toBe("steward");
    expect(got.trust).toBe(1.0);
    expect(got.fieldHash).toBe("fh1");
    expect(got.recordHash).toBe("rh-a:rh-b");
    expect(got.originalScore).toBe(0.92);
    expect(got.matchkeyName).toBe("identity");
    expect(got.reason).toBe("manual");
    expect(got.dataset).toBe("customers");
    expect(got.createdAt).toBeInstanceOf(Date);
    expect(got.createdAt.toISOString()).toBe("2026-05-04T12:00:00.000Z");
    await store.close?.();
  });

  it("ignores lower-trust upserts and overwrites same-tier (latest wins)", async () => {
    const store = new SqliteMemoryStore({
      enabled: true,
      backend: "sqlite",
      path: dbPath,
      learning: { thresholdMinCorrections: 10, weightsMinCorrections: 50 },
    });
    await store.init();
    await store.addCorrection(
      makeCorrection({ id: "high", source: "steward", trust: 1.0, reason: "first" }),
    );
    // Lower trust must be ignored.
    await store.addCorrection(
      makeCorrection({ id: "low", source: "agent", trust: 0.5, reason: "ignored" }),
    );
    let all = await store.getCorrections();
    expect(all.length).toBe(1);
    expect(all[0]!.reason).toBe("first");
    expect(all[0]!.trust).toBe(1.0);

    // Same-tier latest wins.
    await store.addCorrection(
      makeCorrection({ id: "high2", source: "boost", trust: 1.0, reason: "later" }),
    );
    all = await store.getCorrections();
    expect(all.length).toBe(1);
    expect(all[0]!.reason).toBe("later");
    expect(all[0]!.id).toBe("high2");
    await store.close?.();
  });

  it("scopes corrections by dataset (UNIQUE(id_a, id_b, dataset))", async () => {
    const store = new SqliteMemoryStore({
      enabled: true,
      backend: "sqlite",
      path: dbPath,
      learning: { thresholdMinCorrections: 10, weightsMinCorrections: 50 },
    });
    await store.init();
    await store.addCorrection(makeCorrection({ id: "a", dataset: "ds1" }));
    await store.addCorrection(makeCorrection({ id: "b", dataset: "ds2" }));
    await store.addCorrection(makeCorrection({ id: "c", dataset: null }));
    const all = await store.getCorrections();
    expect(all.length).toBe(3);
    const ds1 = await store.getCorrections({ dataset: "ds1" });
    expect(ds1.length).toBe(1);
    expect(ds1[0]!.id).toBe("a");
    const ds2 = await store.getCorrections({ dataset: "ds2" });
    expect(ds2.length).toBe(1);
    expect(ds2[0]!.id).toBe("b");
    const dsnull = await store.getCorrections({ dataset: null });
    expect(dsnull.length).toBe(1);
    expect(dsnull[0]!.id).toBe("c");
    await store.close?.();
  });

  it("getCorrection canonicalizes pair lookup and respects null dataset via IS NULL", async () => {
    const store = new SqliteMemoryStore({
      enabled: true,
      backend: "sqlite",
      path: dbPath,
      learning: { thresholdMinCorrections: 10, weightsMinCorrections: 50 },
    });
    await store.init();
    await store.addCorrection(makeCorrection({ idA: 7, idB: 3, dataset: null }));
    const c1 = await store.getCorrection(3, 7, null);
    expect(c1).not.toBeNull();
    expect(c1!.idA).toBe(3);
    expect(c1!.idB).toBe(7);
    const c2 = await store.getCorrection(3, 7, "other");
    expect(c2).toBeNull();
    await store.close?.();
  });

  it("countCorrections filters by dataset", async () => {
    const store = new SqliteMemoryStore({
      enabled: true,
      backend: "sqlite",
      path: dbPath,
      learning: { thresholdMinCorrections: 10, weightsMinCorrections: 50 },
    });
    await store.init();
    await store.addCorrection(makeCorrection({ idA: 1, idB: 2, dataset: "ds" }));
    await store.addCorrection(makeCorrection({ idA: 3, idB: 4, dataset: "ds" }));
    await store.addCorrection(makeCorrection({ idA: 5, idB: 6, dataset: null }));
    expect(await store.countCorrections()).toBe(3);
    expect(await store.countCorrections("ds")).toBe(2);
    expect(await store.countCorrections(null)).toBe(1);
    await store.close?.();
  });

  it("correctionsSince filters by ISO timestamp", async () => {
    const store = new SqliteMemoryStore({
      enabled: true,
      backend: "sqlite",
      path: dbPath,
      learning: { thresholdMinCorrections: 10, weightsMinCorrections: 50 },
    });
    await store.init();
    await store.addCorrection(
      makeCorrection({
        id: "old",
        idA: 1,
        idB: 2,
        createdAt: new Date("2026-01-01T00:00:00.000Z"),
      }),
    );
    await store.addCorrection(
      makeCorrection({
        id: "new",
        idA: 3,
        idB: 4,
        createdAt: new Date("2026-06-01T00:00:00.000Z"),
      }),
    );
    const since = new Date("2026-03-01T00:00:00.000Z");
    const recent = await store.correctionsSince(since);
    expect(recent.length).toBe(1);
    expect(recent[0]!.id).toBe("new");
    await store.close?.();
  });
});

describe("SqliteMemoryStore adjustments", () => {
  it("save / get / getAll / lastLearnTime round-trip", async () => {
    const store = new SqliteMemoryStore({
      enabled: true,
      backend: "sqlite",
      path: dbPath,
      learning: { thresholdMinCorrections: 10, weightsMinCorrections: 50 },
    });
    await store.init();
    expect(await store.lastLearnTime()).toBeNull();
    const adj: LearnedAdjustment = {
      matchkeyName: "identity",
      threshold: 0.83,
      fieldWeights: { name: 0.6, email: 0.4 },
      sampleSize: 12,
      learnedAt: new Date("2026-05-04T10:00:00.000Z"),
    };
    await store.saveAdjustment(adj);
    const got = await store.getAdjustment("identity");
    expect(got).not.toBeNull();
    expect(got!.matchkeyName).toBe("identity");
    expect(got!.threshold).toBe(0.83);
    expect(got!.fieldWeights).toEqual({ name: 0.6, email: 0.4 });
    expect(got!.sampleSize).toBe(12);
    expect(got!.learnedAt.toISOString()).toBe("2026-05-04T10:00:00.000Z");

    const adj2: LearnedAdjustment = {
      matchkeyName: "phone",
      threshold: 0.9,
      fieldWeights: null,
      sampleSize: 3,
      learnedAt: new Date("2026-05-05T10:00:00.000Z"),
    };
    await store.saveAdjustment(adj2);
    const all = await store.getAllAdjustments();
    expect(all.length).toBe(2);

    const last = await store.lastLearnTime();
    expect(last).not.toBeNull();
    expect(last!.toISOString()).toBe("2026-05-05T10:00:00.000Z");
    await store.close?.();
  });

  it("INSERT OR REPLACE upserts adjustments by matchkey_name", async () => {
    const store = new SqliteMemoryStore({
      enabled: true,
      backend: "sqlite",
      path: dbPath,
      learning: { thresholdMinCorrections: 10, weightsMinCorrections: 50 },
    });
    await store.init();
    await store.saveAdjustment({
      matchkeyName: "identity",
      threshold: 0.7,
      fieldWeights: null,
      sampleSize: 5,
      learnedAt: new Date("2026-05-01T00:00:00.000Z"),
    });
    await store.saveAdjustment({
      matchkeyName: "identity",
      threshold: 0.85,
      fieldWeights: null,
      sampleSize: 20,
      learnedAt: new Date("2026-05-02T00:00:00.000Z"),
    });
    const all = await store.getAllAdjustments();
    expect(all.length).toBe(1);
    expect(all[0]!.threshold).toBe(0.85);
    expect(all[0]!.sampleSize).toBe(20);
    await store.close?.();
  });
});

describe("SqliteMemoryStore.close", () => {
  it("releases the handle and subsequent ops fail predictably", async () => {
    const store = new SqliteMemoryStore({
      enabled: true,
      backend: "sqlite",
      path: dbPath,
      learning: { thresholdMinCorrections: 10, weightsMinCorrections: 50 },
    });
    await store.init();
    await store.close?.();
    await expect(store.getCorrections()).rejects.toThrow();
  });
});
