import { describe, it, expect } from "vitest";
import { WasmHNSWANNBlocker } from "../../src/core/hnswWasm.js";

// Deterministic unit vectors.
function corpus(n: number, dim: number, seed: number): Float32Array[] {
  // xorshift32 for a dependency-free deterministic RNG.
  let s = seed >>> 0 || 1;
  const next = () => {
    s ^= s << 13;
    s ^= s >>> 17;
    s ^= s << 5;
    return ((s >>> 0) / 0xffffffff) * 2 - 1;
  };
  const out: Float32Array[] = [];
  for (let i = 0; i < n; i++) {
    const v = new Float32Array(dim);
    let norm = 0;
    for (let d = 0; d < dim; d++) {
      v[d] = next();
      norm += v[d]! * v[d]!;
    }
    norm = Math.sqrt(norm) || 1;
    for (let d = 0; d < dim; d++) v[d]! /= norm;
    out.push(v);
  }
  return out;
}

function bruteTopK(rows: Float32Array[], q: Float32Array, k: number): number[] {
  const scored = rows.map((r, i) => {
    let ip = 0;
    for (let d = 0; d < r.length; d++) ip += r[d]! * q[d]!;
    return { i, ip };
  });
  scored.sort((a, b) => b.ip - a.ip || a.i - b.i);
  return scored.slice(0, k).map((s) => s.i);
}

describe("WasmHNSWANNBlocker", () => {
  it("empty index returns nothing", () => {
    const b = new WasmHNSWANNBlocker({ topK: 5 });
    b.buildIndex([]);
    expect(b.indexSize).toBe(0);
    expect(b.queryOne(new Float32Array([1, 0, 0, 0]))).toEqual([]);
  });

  it("self is the top hit at score ~1 on unit vectors", () => {
    const rows = corpus(200, 16, 1);
    const b = new WasmHNSWANNBlocker({ topK: 5 });
    b.buildIndex(rows);
    expect(b.indexSize).toBe(200);
    const res = b.queryOne(rows[0]!);
    expect(res[0]![0]).toBe(0);
    expect(res[0]![1]).toBeCloseTo(1.0, 4);
  });

  it("scores are descending inner product", () => {
    const rows = corpus(300, 12, 7);
    const b = new WasmHNSWANNBlocker({ topK: 10 });
    b.buildIndex(rows);
    const res = b.queryOne(rows[3]!);
    for (let i = 1; i < res.length; i++) {
      expect(res[i]![1]).toBeLessThanOrEqual(res[i - 1]![1] + 1e-6);
    }
  });

  it("high recall vs brute force at scale", () => {
    const dim = 24;
    const n = 1500;
    const rows = corpus(n, dim, 42);
    const k = 10;
    const b = new WasmHNSWANNBlocker({ topK: k, efSearch: 128 });
    b.buildIndex(rows);
    let hits = 0;
    let total = 0;
    for (let qi = 0; qi < n; qi += 25) {
      const got = new Set(b.queryOne(rows[qi]!).map(([i]) => i));
      const want = bruteTopK(rows, rows[qi]!, k);
      for (const w of want) {
        total++;
        if (got.has(w)) hits++;
      }
    }
    expect(hits / total).toBeGreaterThanOrEqual(0.95);
  });

  it("incremental add extends ids and is findable", () => {
    const rows = corpus(500, 16, 9);
    const b = new WasmHNSWANNBlocker({ topK: 10 });
    b.buildIndex(rows);
    const extra = corpus(1, 16, 99)[0]!;
    const id = b.addToIndex(extra);
    expect(id).toBe(500);
    expect(b.indexSize).toBe(501);
    const hits = new Set(b.queryOne(extra).map(([i]) => i));
    expect(hits.has(500)).toBe(true);
  });

  it("query() yields canonical, deduped, self-excluded pairs", () => {
    const rows = corpus(200, 16, 3);
    const b = new WasmHNSWANNBlocker({ topK: 10 });
    b.buildIndex(rows);
    const pairs = b.query(rows);
    expect(pairs.length).toBeGreaterThan(0);
    for (const [a, c] of pairs) expect(a).toBeLessThan(c);
    const withScores = b.queryWithScores(rows);
    for (const [, , s] of withScores) {
      expect(s).toBeGreaterThanOrEqual(-1.0001);
      expect(s).toBeLessThanOrEqual(1.0001);
    }
  });
});
