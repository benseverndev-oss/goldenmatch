/**
 * Cross-surface HNSW parity: the TS/WASM surface must reproduce the SAME golden
 * neighbors as the Rust core (`goldenhnsw/tests/golden.rs`) and the Python wheel.
 * The fixture is generated once from the kernel and copied here by
 * `scripts/build_goldenhnsw_wasm.mjs`; all three surfaces run the SAME
 * `goldenhnsw` code, so ids match exactly and scores match to f32 precision.
 */
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { WasmHNSWANNBlocker } from "../../src/core/hnswWasm.js";

interface Fixture {
  params: {
    dim: number;
    m: number;
    ef_construction: number;
    ef_search: number;
    seed: number;
    k: number;
  };
  n: number;
  corpus: number[];
  query_ids: number[];
  queries: number[];
  expected: Array<Array<[number, number]>>;
}

const here = dirname(fileURLToPath(import.meta.url));
const fixture: Fixture = JSON.parse(
  readFileSync(resolve(here, "fixtures/hnsw/hnsw_vectors.json"), "utf8"),
);

function rows(flat: number[], dim: number): Float32Array[] {
  const out: Float32Array[] = [];
  for (let i = 0; i < flat.length; i += dim) {
    out.push(Float32Array.from(flat.slice(i, i + dim)));
  }
  return out;
}

describe("goldenhnsw wasm — cross-surface parity", () => {
  it("reproduces the golden neighbor ids + scores", () => {
    const p = fixture.params;
    const corpusRows = rows(fixture.corpus, p.dim);
    expect(corpusRows.length).toBe(fixture.n);

    const blocker = new WasmHNSWANNBlocker({
      topK: p.k,
      M: p.m,
      efConstruction: p.ef_construction,
      efSearch: p.ef_search,
      seed: p.seed,
    });
    blocker.buildIndex(corpusRows);
    expect(blocker.indexSize).toBe(fixture.n);

    const queryRows = rows(fixture.queries, p.dim);
    expect(queryRows.length).toBe(fixture.expected.length);

    for (let qi = 0; qi < queryRows.length; qi++) {
      const got = blocker.queryOne(queryRows[qi]!);
      const want = fixture.expected[qi]!;
      expect(got.length).toBe(want.length);
      for (let j = 0; j < want.length; j++) {
        // ids must match EXACTLY (same graph, same kernel)
        expect(got[j]![0]).toBe(want[j]![0]);
        // scores match to f32 precision
        expect(got[j]![1]).toBeCloseTo(want[j]![1], 5);
      }
    }
  });
});
