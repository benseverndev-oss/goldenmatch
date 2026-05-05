/**
 * memory-api.test.ts -- Python API mirror tests.
 *
 * Verifies `getMemory / addCorrection / learn / memoryStats` mirror Python
 * `_api.py:882-973` behavior.
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import {
  getMemory,
  addCorrection,
  learn,
  memoryStats,
} from "../../src/node/memory/api.js";

let dir: string;
let dbPath: string;

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "gm-api-"));
  dbPath = join(dir, "memory.db");
});

afterEach(() => {
  try {
    rmSync(dir, { recursive: true, force: true });
  } catch {
    /* ignore Windows-held handles */
  }
});

describe("getMemory", () => {
  it("opens an initialized SqliteMemoryStore at the given path", async () => {
    const store = await getMemory({ path: dbPath });
    expect(await store.countCorrections()).toBe(0);
    await store.close?.();
  });
});

describe("addCorrection", () => {
  it("writes a correction with default source='api' and trust=0.5", async () => {
    await addCorrection({
      idA: 1,
      idB: 2,
      decision: "approve",
      dataset: "test",
      path: dbPath,
    });
    const store = await getMemory({ path: dbPath });
    const corr = await store.getCorrection(1, 2, "test");
    expect(corr).not.toBeNull();
    expect(corr!.source).toBe("api");
    expect(corr!.trust).toBe(0.5);
    expect(corr!.decision).toBe("approve");
    expect(corr!.fieldHash).toBe("");
    expect(corr!.recordHash).toBe("");
    await store.close?.();
  });

  it("uses trust=1.0 when source='steward'", async () => {
    await addCorrection({
      idA: 5,
      idB: 6,
      decision: "reject",
      source: "steward",
      dataset: "ds1",
      path: dbPath,
    });
    const store = await getMemory({ path: dbPath });
    const corr = await store.getCorrection(5, 6, "ds1");
    expect(corr!.trust).toBe(1.0);
    expect(corr!.source).toBe("steward");
    await store.close?.();
  });

  it("generates a UUID id for each correction", async () => {
    await addCorrection({
      idA: 1,
      idB: 2,
      decision: "approve",
      dataset: "d",
      path: dbPath,
    });
    const store = await getMemory({ path: dbPath });
    const corr = await store.getCorrection(1, 2, "d");
    // RFC 4122 UUIDs are 36 chars: 8-4-4-4-12
    expect(corr!.id).toMatch(/^[0-9a-f-]{36}$/);
    await store.close?.();
  });
});

describe("learn", () => {
  it("returns an empty list when no corrections exist", async () => {
    const adjustments = await learn({ path: dbPath });
    expect(adjustments).toEqual([]);
  });

  it("returns adjustments after enough corrections accumulate", async () => {
    // Need >=10 corrections, mix of approve / reject.
    for (let i = 0; i < 6; i++) {
      await addCorrection({
        idA: i * 2,
        idB: i * 2 + 1,
        decision: "approve",
        dataset: "d",
        matchkeyName: "mk1",
        path: dbPath,
      });
    }
    for (let i = 0; i < 6; i++) {
      await addCorrection({
        idA: 100 + i * 2,
        idB: 100 + i * 2 + 1,
        decision: "reject",
        dataset: "d",
        matchkeyName: "mk1",
        path: dbPath,
      });
    }
    const adjustments = await learn({ path: dbPath });
    expect(adjustments.length).toBeGreaterThan(0);
    expect(adjustments[0]!.matchkeyName).toBe("mk1");
  });
});

describe("memoryStats", () => {
  it("returns zero count and null lastLearnTime when empty", async () => {
    const stats = await memoryStats({ path: dbPath });
    expect(stats.count).toBe(0);
    expect(stats.lastLearnTime).toBeNull();
    expect(stats.adjustments).toEqual([]);
  });

  it("counts inserted corrections", async () => {
    await addCorrection({
      idA: 1,
      idB: 2,
      decision: "approve",
      dataset: "d",
      path: dbPath,
    });
    await addCorrection({
      idA: 3,
      idB: 4,
      decision: "reject",
      dataset: "d",
      path: dbPath,
    });
    const stats = await memoryStats({ path: dbPath });
    expect(stats.count).toBe(2);
  });
});
