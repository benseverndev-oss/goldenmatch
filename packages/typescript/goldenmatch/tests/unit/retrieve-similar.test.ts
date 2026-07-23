import { describe, it, expect } from "vitest";

import {
  retrieveSimilar,
  retrievedRecordToDict,
  RetrieveSimilarError,
  type EmbedderLike,
} from "../../src/core/retrieve-similar.js";

// ---------------------------------------------------------------------------
// Stub embedder — deterministic 2-D vectors so cosine ordering is controllable.
// Mirrors how the TS embedding tests inject a fake embedder (no network).
// ---------------------------------------------------------------------------

const VECS: Record<string, [number, number]> = {
  apple: [1, 0], // the query
  "apple inc": [1, 0], // cos 1.0000 with query
  "apple corp": [0.9, 0.1], // cos ~0.9938 with query
  "banana ltd": [0.1, 0.9], // cos ~0.1104 with query
  "cherry co": [0, 1], // cos 0.0000 with query
};

const stubEmbedder: EmbedderLike = {
  async embedColumn(values) {
    return values.map((v) => {
      const t = (v ?? "").toString().trim().toLowerCase();
      const pair = VECS[t] ?? [0, 0];
      return Float32Array.from(pair);
    });
  },
};

const CORPUS = [
  { id: "1", name: "Apple Inc", kind: "tech" },
  { id: "2", name: "Apple Corp", kind: "tech" },
  { id: "3", name: "Banana Ltd", kind: "fruit" },
  { id: "4", name: "Cherry Co", kind: "fruit" },
];

describe("retrieveSimilar: top-k ordering + score shape", () => {
  it("ranks by cosine similarity desc and returns row_id/score/record", async () => {
    const out = await retrieveSimilar(CORPUS, "apple", "name", {
      embedder: stubEmbedder,
    });
    // All 4 clear threshold 0.0; ordered by descending cosine.
    expect(out.map((r) => r.record["name"])).toEqual([
      "Apple Inc",
      "Apple Corp",
      "Banana Ltd",
      "Cherry Co",
    ]);
    // Row ids fall back to corpus position (no __row_id__ column).
    expect(out.map((r) => r.rowId)).toEqual([0, 1, 2, 3]);
    // Scores are numbers in [-1, 1], strictly descending here.
    for (const r of out) {
      expect(typeof r.score).toBe("number");
      expect(r.score).toBeGreaterThanOrEqual(-1);
      expect(r.score).toBeLessThanOrEqual(1);
    }
    expect(out[0]!.score).toBeGreaterThan(out[1]!.score);
    expect(out[1]!.score).toBeGreaterThan(out[2]!.score);
    // Records carry the full (non-internal) row.
    expect(out[0]!.record).toEqual({ id: "1", name: "Apple Inc", kind: "tech" });
  });

  it("honors the k cap", async () => {
    const out = await retrieveSimilar(CORPUS, "apple", "name", {
      embedder: stubEmbedder,
      k: 2,
    });
    expect(out).toHaveLength(2);
    expect(out.map((r) => r.record["name"])).toEqual(["Apple Inc", "Apple Corp"]);
  });

  it("applies the cosine threshold", async () => {
    const out = await retrieveSimilar(CORPUS, "apple", "name", {
      embedder: stubEmbedder,
      threshold: 0.5,
    });
    // Only the two Apple rows clear 0.5.
    expect(out.map((r) => r.record["name"])).toEqual(["Apple Inc", "Apple Corp"]);
  });
});

describe("retrieveSimilar: caller-supplied embedder contract", () => {
  it("throws a clear error when no embedder is supplied", async () => {
    await expect(retrieveSimilar(CORPUS, "apple", "name")).rejects.toBeInstanceOf(
      RetrieveSimilarError,
    );
    await expect(
      retrieveSimilar(CORPUS, "apple", "name", {}),
    ).rejects.toThrow(/requires an explicit embedder/i);
  });

  it("throws when the column is not in the corpus", async () => {
    await expect(
      retrieveSimilar(CORPUS, "apple", "missing", { embedder: stubEmbedder }),
    ).rejects.toThrow(/not in dataframe/i);
  });
});

describe("retrieveSimilar: filters + empties + ids", () => {
  it("applies an equality pre-filter before embedding", async () => {
    const out = await retrieveSimilar(CORPUS, "apple", "name", {
      embedder: stubEmbedder,
      filters: { kind: "fruit" },
    });
    // Only fruit rows are in the corpus; Apple rows are filtered out entirely.
    expect(out.map((r) => r.record["name"])).toEqual(["Banana Ltd", "Cherry Co"]);
  });

  it("returns [] when a filter names a column absent from the corpus", async () => {
    const out = await retrieveSimilar(CORPUS, "apple", "name", {
      embedder: stubEmbedder,
      filters: { nope: "x" },
    });
    expect(out).toEqual([]);
  });

  it("returns [] on blank query or empty corpus", async () => {
    expect(await retrieveSimilar(CORPUS, "", "name", { embedder: stubEmbedder })).toEqual([]);
    expect(await retrieveSimilar([], "apple", "name", { embedder: stubEmbedder })).toEqual([]);
  });

  it("uses __row_id__ for the id when present and strips __-keys", async () => {
    const withIds = [
      { __row_id__: 100, name: "Apple Inc", __source__: "a" },
      { __row_id__: 200, name: "Cherry Co", __source__: "b" },
    ];
    const out = await retrieveSimilar(withIds, "apple", "name", {
      embedder: stubEmbedder,
    });
    expect(out[0]!.rowId).toBe(100);
    expect(out[0]!.record).toEqual({ name: "Apple Inc" }); // __-keys stripped
  });
});

describe("retrievedRecordToDict", () => {
  it("shapes the wire record with a 4dp score (Python parity)", () => {
    const d = retrievedRecordToDict({
      rowId: 7,
      score: 0.123456,
      record: { name: "x" },
    });
    expect(d).toEqual({ row_id: 7, score: 0.1235, record: { name: "x" } });
  });
});
