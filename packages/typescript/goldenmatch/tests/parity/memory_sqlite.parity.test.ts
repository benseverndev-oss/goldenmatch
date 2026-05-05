/**
 * memory_sqlite.parity.test.ts -- cross-language SQLite fixture parity.
 *
 * Opens the shared `memory.db` (written by Python's MemoryStore via
 * `tests/parity/memory/gen_memory_fixtures.py --rebuild-db`) through the
 * TypeScript `SqliteMemoryStore` and asserts every row maps back to the
 * canonical `memory_corrections.json` entry. This locks the on-disk schema
 * (column names, types, ISO timestamp encoding) so a `.db` written by either
 * language is readable by the other.
 *
 * Note: SqliteMemoryStore canonicalizes pair ordering (id_a <= id_b) on
 * insert; the JSON fixture is already canonicalized on Python's side via the
 * same rule, so direct comparison is valid.
 */
import { describe, it, expect, beforeAll, afterAll } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { SqliteMemoryStore } from "../../src/node/memory/sqlite-store.js";
import {
  correctionToJSON,
  type CorrectionJSON,
} from "../../src/core/memory/types.js";

const FIXTURE_DIR = join(__dirname, "fixtures");
const DB_PATH = join(FIXTURE_DIR, "memory.db");
const JSON_PATH = join(FIXTURE_DIR, "memory_corrections.json");

describe("memory SQLite parity", () => {
  let store: SqliteMemoryStore;
  let expected: CorrectionJSON[];

  beforeAll(async () => {
    expected = JSON.parse(readFileSync(JSON_PATH, "utf-8")) as CorrectionJSON[];
    store = new SqliteMemoryStore({
      enabled: true,
      backend: "sqlite",
      path: DB_PATH,
      learning: { thresholdMinCorrections: 10, weightsMinCorrections: 50 },
    });
    await store.init();
  });

  afterAll(async () => {
    await store.close?.();
  });

  it("contains all 12 corrections", { timeout: 15000 }, async () => {
    const all = await store.getCorrections();
    expect(all).toHaveLength(expected.length);
  });

  it(
    "every correction matches the JSON fixture (id-keyed)",
    { timeout: 15000 },
    async () => {
      const all = await store.getCorrections();
      const byId = new Map(all.map((c) => [c.id, c]));
      for (const j of expected) {
        const got = byId.get(j.id);
        expect(got, `missing id ${j.id}`).toBeDefined();
        const back = correctionToJSON(got!);
        expect(back.id).toBe(j.id);
        expect(back.id_a).toBe(j.id_a);
        expect(back.id_b).toBe(j.id_b);
        expect(back.decision).toBe(j.decision);
        expect(back.source).toBe(j.source);
        expect(back.trust).toBe(j.trust);
        expect(back.field_hash).toBe(j.field_hash);
        expect(back.record_hash).toBe(j.record_hash);
        expect(back.original_score).toBe(j.original_score);
        expect(back.matchkey_name).toBe(j.matchkey_name);
        expect(back.reason).toBe(j.reason);
        expect(back.dataset).toBe(j.dataset);
        expect(new Date(back.created_at).getTime()).toBe(
          new Date(j.created_at).getTime(),
        );
      }
    },
  );

  it("countCorrections returns 12 across all datasets", async () => {
    const total = await store.countCorrections();
    expect(total).toBe(expected.length);
  });

  it("dataset scoping is honored", async () => {
    const parityRows = await store.getCorrections({ dataset: "parity_test" });
    const expectedParity = expected.filter((c) => c.dataset === "parity_test");
    expect(parityRows).toHaveLength(expectedParity.length);

    const nullRows = await store.getCorrections({ dataset: null });
    const expectedNull = expected.filter((c) => c.dataset === null);
    expect(nullRows).toHaveLength(expectedNull.length);
  });
});
