/**
 * memory-reanchor.test.ts -- collision-safe vectorized re-anchor.
 *
 * Ports `packages/python/goldenmatch/tests/test_memory_reanchor.py` to TS.
 * Polars DataFrames become plain `Row[]` arrays (each row carries
 * `__row_id__` as a number).
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

import { InMemoryStore } from "../../src/core/memory/store.js";
import {
  applyCorrections,
  type ScoredPair,
} from "../../src/core/memory/corrections.js";
import {
  computeFieldHash,
  computeRecordHash,
} from "../../src/core/memory/hash.js";
import type { Correction } from "../../src/core/memory/types.js";
import type { Row } from "../../src/core/types.js";

interface MakeRowInput {
  readonly rowId: number;
  readonly name: string;
  readonly zip: string;
  readonly note?: string;
}

function makeRow(input: MakeRowInput): Row {
  const r: Record<string, unknown> = {
    __row_id__: input.rowId,
    name: input.name,
    zip: input.zip,
  };
  if (input.note !== undefined) r["note"] = input.note;
  return r;
}

function makeDf(rows: ReadonlyArray<MakeRowInput>): Row[] {
  return rows.map(makeRow);
}

function columnsOf(df: ReadonlyArray<Row>): string[] {
  if (df.length === 0) return [];
  return Object.keys(df[0]!);
}

async function seedReject(
  store: InMemoryStore,
  df: ReadonlyArray<Row>,
  idA: number,
  idB: number,
  fields: ReadonlyArray<string> = ["name", "zip"],
  dataset: string | null = "t",
): Promise<void> {
  const rowA = df.find((r) => r["__row_id__"] === idA);
  const rowB = df.find((r) => r["__row_id__"] === idB);
  if (!rowA || !rowB) throw new Error(`row not found: ${idA} or ${idB}`);

  const aVals = fields.map((f) => rowA[f]);
  const bVals = fields.map((f) => rowB[f]);
  const fh = await computeFieldHash(aVals, bVals);

  const cols = columnsOf(df);
  const rhA = await computeRecordHash(rowA, cols);
  const rhB = await computeRecordHash(rowB, cols);
  const rh = `${rhA}:${rhB}`;

  const correction: Correction = {
    id: `c-${idA}-${idB}`,
    idA,
    idB,
    decision: "reject",
    source: "steward",
    trust: 1.0,
    fieldHash: fh,
    recordHash: rh,
    originalScore: 0.92,
    matchkeyName: null,
    reason: null,
    dataset,
    createdAt: new Date(),
  };
  await store.addCorrection(correction);
}

describe("applyCorrections (re-anchor)", () => {
  let warn: ReturnType<typeof vi.spyOn>;
  beforeEach(() => {
    warn = vi.spyOn(console, "warn").mockImplementation(() => {});
  });

  it("re-anchors after row reorder via record_hash", async () => {
    const df1 = makeDf([
      { rowId: 1, name: "Acme Corp", zip: "10001" },
      { rowId: 2, name: "Acme LLC", zip: "10001" },
      { rowId: 3, name: "Beta Inc", zip: "20002" },
    ]);
    const store = new InMemoryStore();
    await seedReject(store, df1, 1, 2);

    const df2 = makeDf([
      { rowId: 10, name: "Acme Corp", zip: "10001" },
      { rowId: 20, name: "Acme LLC", zip: "10001" },
      { rowId: 30, name: "Beta Inc", zip: "20002" },
    ]);
    const scored: ScoredPair[] = [
      [10, 20, 0.92],
      [10, 30, 0.1],
      [20, 30, 0.1],
    ];
    const [adjusted, stats] = await applyCorrections(
      scored,
      store,
      df2,
      ["name", "zip"],
      { dataset: "t" },
    );

    const pair = adjusted.find((p) => p[0] === 10 && p[1] === 20);
    expect(pair).toBeDefined();
    expect(pair![2]).toBe(0.0);
    expect(stats.applied).toBe(1);
    expect(stats.stale).toBe(0);
  });

  it("refuses to re-anchor on ambiguous duplicates", async () => {
    const df1 = makeDf([
      { rowId: 1, name: "Acme Corp", zip: "10001" },
      { rowId: 2, name: "Acme LLC", zip: "10001" },
    ]);
    const store = new InMemoryStore();
    await seedReject(store, df1, 1, 2);

    const df2 = makeDf([
      { rowId: 10, name: "Acme Corp", zip: "10001" },
      { rowId: 11, name: "Acme Corp", zip: "10001" },
      { rowId: 20, name: "Acme LLC", zip: "10001" },
    ]);
    const scored: ScoredPair[] = [
      [10, 20, 0.92],
      [11, 20, 0.92],
      [10, 11, 1.0],
    ];
    const [adjusted, stats] = await applyCorrections(
      scored,
      store,
      df2,
      ["name", "zip"],
      { dataset: "t" },
    );

    for (let i = 0; i < adjusted.length; i++) {
      expect(adjusted[i]![2]).toBe(scored[i]![2]);
    }
    expect(stats.applied).toBe(0);
    expect(stats.staleAmbiguous).toBe(1);
  });

  it("marks stale when a matchkey field is edited", async () => {
    const df1 = makeDf([
      { rowId: 1, name: "Acme Corp", zip: "10001" },
      { rowId: 2, name: "Acme LLC", zip: "10001" },
    ]);
    const store = new InMemoryStore();
    await seedReject(store, df1, 1, 2);

    const df2 = makeDf([
      { rowId: 1, name: "ACME CORPORATION", zip: "10001" },
      { rowId: 2, name: "Acme LLC", zip: "10001" },
    ]);
    const scored: ScoredPair[] = [[1, 2, 0.85]];
    const [adjusted, stats] = await applyCorrections(
      scored,
      store,
      df2,
      ["name", "zip"],
      { dataset: "t" },
    );

    expect(adjusted[0]![2]).toBe(0.85);
    expect(stats.applied).toBe(0);
    expect(stats.stale).toBe(1);
  });

  it("marks stale when a non-matchkey field is edited (record_hash captures all)", async () => {
    const df1 = makeDf([
      { rowId: 1, name: "Acme Corp", zip: "10001", note: "old_note" },
      { rowId: 2, name: "Acme LLC", zip: "10001", note: "old_note" },
    ]);
    const store = new InMemoryStore();
    await seedReject(store, df1, 1, 2);

    const df2 = makeDf([
      { rowId: 1, name: "Acme Corp", zip: "10001", note: "new_note" },
      { rowId: 2, name: "Acme LLC", zip: "10001", note: "new_note" },
    ]);
    const scored: ScoredPair[] = [[1, 2, 0.92]];
    const [, stats] = await applyCorrections(
      scored,
      store,
      df2,
      ["name", "zip"],
      { dataset: "t" },
    );

    expect(stats.stale).toBe(1);
  });

  it("reanchor=false skips the re-anchor pass", async () => {
    const df1 = makeDf([
      { rowId: 1, name: "Acme Corp", zip: "10001" },
      { rowId: 2, name: "Acme LLC", zip: "10001" },
    ]);
    const store = new InMemoryStore();
    await seedReject(store, df1, 1, 2);

    const df2 = makeDf([
      { rowId: 10, name: "Acme Corp", zip: "10001" },
      { rowId: 20, name: "Acme LLC", zip: "10001" },
    ]);
    const scored: ScoredPair[] = [[10, 20, 0.92]];
    const [adjusted, stats] = await applyCorrections(
      scored,
      store,
      df2,
      ["name", "zip"],
      { dataset: "t", reanchor: false },
    );

    expect(adjusted[0]![2]).toBe(0.92);
    expect(stats.applied).toBe(0);
    expect(stats.staleUnanchorable).toBe(1);
  });

  it("returns scored pairs unchanged with empty store", async () => {
    const store = new InMemoryStore();
    const df = makeDf([
      { rowId: 1, name: "Acme Corp", zip: "10001" },
      { rowId: 2, name: "Acme LLC", zip: "10001" },
    ]);
    const scored: ScoredPair[] = [
      [1, 2, 0.92],
      [1, 1, 0.5],
    ];
    const [adjusted, stats] = await applyCorrections(
      scored,
      store,
      df,
      ["name", "zip"],
      { dataset: "t" },
    );

    expect(adjusted).toEqual(scored);
    expect(stats.applied).toBe(0);
    expect(stats.stale).toBe(0);
    expect(stats.totalPairs).toBe(scored.length);
  });

  it("returns input unchanged with warning when __row_id__ column is missing", async () => {
    const store = new InMemoryStore();
    const df1 = makeDf([
      { rowId: 1, name: "Acme Corp", zip: "10001" },
      { rowId: 2, name: "Acme LLC", zip: "10001" },
    ]);
    await seedReject(store, df1, 1, 2);

    const dfNoRowId: Row[] = [
      { name: "Acme Corp", zip: "10001" },
      { name: "Acme LLC", zip: "10001" },
    ];
    const scored: ScoredPair[] = [[1, 2, 0.92]];
    const [adjusted, stats] = await applyCorrections(
      scored,
      store,
      dfNoRowId,
      ["name", "zip"],
      { dataset: "t" },
    );

    expect(adjusted).toEqual(scored);
    expect(stats.applied).toBe(0);
    expect(warn).toHaveBeenCalled();
    const warned = warn.mock.calls.some((args: unknown[]) =>
      args.some((a: unknown) => typeof a === "string" && a.includes("__row_id__")),
    );
    expect(warned).toBe(true);
  });

  it("counts unanchorable corrections (no recordHash, row IDs gone)", async () => {
    const store = new InMemoryStore();
    await store.addCorrection({
      id: "c-unanchorable",
      idA: 999,
      idB: 1000,
      decision: "reject",
      source: "unmerge",
      trust: 1.0,
      fieldHash: "",
      recordHash: "",
      originalScore: 0.92,
      matchkeyName: null,
      reason: null,
      dataset: "t",
      createdAt: new Date(),
    });

    const df = makeDf([
      { rowId: 1, name: "Acme Corp", zip: "10001" },
      { rowId: 2, name: "Acme LLC", zip: "10001" },
    ]);
    const scored: ScoredPair[] = [[1, 2, 0.5]];
    const [, stats] = await applyCorrections(
      scored,
      store,
      df,
      ["name", "zip"],
      { dataset: "t" },
    );

    expect(stats.staleUnanchorable).toBeGreaterThanOrEqual(1);
    const hasPair = stats.stalePairs.some((p) => p[0] === 999 && p[1] === 1000);
    expect(hasPair).toBe(true);
  });
});
