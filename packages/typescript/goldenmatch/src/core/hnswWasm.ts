/**
 * hnswWasm.ts — synchronous, edge-safe HNSW ANN blocker backed by the shared
 * `goldenhnsw` Rust kernel compiled to wasm.
 *
 * This is the SAME kernel the Python `goldenmatch-hnsw` wheel and the Rust core
 * run, so the inner-product ranking is byte-identical across Python / Rust / TS
 * — proven by the shared golden vector (`tests/parity/hnsw.parity.test.ts` reads
 * the fixture generated from the kernel). Unlike the `HNSWANNBlocker` that wraps
 * the Node-only `hnswlib-node` native addon, this runs anywhere: browsers,
 * Workers, edge runtimes, Node — no `node:*` imports, no native peer dep.
 *
 * Scores are the raw inner product (cosine when the embedder emits L2-normalized
 * vectors, which it does by default) — the same contract as the Python HNSW /
 * FAISS `IndexFlatIP` path. Metric is inner-product only; the `metric` option is
 * accepted for interface symmetry but euclidean is not supported here.
 */
import { WasmHnswIndex, initSync } from "./_wasm/goldenhnswWasmBindings.js";
import { GOLDENHNSW_WASM_BASE64 } from "./_wasm/goldenhnswWasmBytes.js";
import type { ANNBlockerBase } from "./ann-blocker.js";

// ---------------------------------------------------------------------------
// One-time synchronous wasm init (edge-safe: atob, no fs/fetch).
// ---------------------------------------------------------------------------

let initialized = false;

function base64ToBytes(b64: string): Uint8Array {
  // atob is available in browsers, Workers, and Node >= 18 — edge-safe.
  const bin = atob(b64);
  const len = bin.length;
  const bytes = new Uint8Array(len);
  for (let i = 0; i < len; i++) bytes[i] = bin.charCodeAt(i);
  return bytes;
}

function ensureInit(): void {
  if (initialized) return;
  initSync({ module: base64ToBytes(GOLDENHNSW_WASM_BASE64) });
  initialized = true;
}

// ---------------------------------------------------------------------------
// Options
// ---------------------------------------------------------------------------

export interface WasmHNSWOptions {
  readonly topK?: number;
  /** Interface symmetry only — the kernel is inner-product (cosine) based. */
  readonly metric?: "cosine" | "euclidean";
  /** HNSW graph degree M (upper layers; layer 0 uses 2M). Default 16. */
  readonly M?: number;
  /** Candidate-list size during build. Default 200. */
  readonly efConstruction?: number;
  /** Candidate-list size during search (raised to at least topK). Default 64. */
  readonly efSearch?: number;
  /** PRNG seed for reproducible graph construction. Default 0x9E3779B9. */
  readonly seed?: number;
}

const DEFAULT_SEED = 0x9e3779b9;

// ---------------------------------------------------------------------------
// WasmHNSWANNBlocker
// ---------------------------------------------------------------------------

/**
 * Native HNSW ANN blocker (goldenhnsw wasm). Interchangeable with `ANNBlocker`
 * and `HNSWANNBlocker` via the shared `ANNBlockerBase` interface.
 */
export class WasmHNSWANNBlocker implements ANNBlockerBase {
  private index: WasmHnswIndex | null = null;
  private count = 0;
  private readonly topK: number;
  private readonly M: number;
  private readonly efConstruction: number;
  private readonly efSearch: number;
  private readonly seed: number;

  constructor(opts: WasmHNSWOptions = {}) {
    this.topK = opts.topK ?? 20;
    this.M = opts.M ?? 16;
    this.efConstruction = opts.efConstruction ?? 200;
    this.efSearch = opts.efSearch ?? 64;
    this.seed = opts.seed ?? DEFAULT_SEED;
  }

  get indexSize(): number {
    return this.count;
  }

  buildIndex(embeddings: readonly Float32Array[]): void {
    ensureInit();
    if (embeddings.length === 0) {
      this.index = null;
      this.count = 0;
      return;
    }
    const dim = embeddings[0]!.length;
    const efSearch = Math.max(this.efSearch, this.topK);
    const index = new WasmHnswIndex(
      dim,
      this.M,
      this.efConstruction,
      efSearch,
      this.seed >>> 0,
    );
    // Flatten row-major into one Float32Array and bulk-load.
    const n = embeddings.length;
    const flat = new Float32Array(n * dim);
    for (let i = 0; i < n; i++) flat.set(embeddings[i]!, i * dim);
    index.add_batch(flat, n);
    this.index = index;
    this.count = n;
  }

  addToIndex(embedding: Float32Array): number {
    if (!this.index) {
      throw new Error("WasmHNSWANNBlocker.addToIndex called before buildIndex");
    }
    const id = this.index.add(embedding);
    this.count++;
    return id;
  }

  /** Top-K neighbors for a single query as (neighborIdx, innerProduct). */
  queryOne(queryEmbedding: Float32Array): Array<[number, number]> {
    if (!this.index || this.count === 0) return [];
    const k = Math.min(this.topK, this.count);
    const flat = this.index.search(queryEmbedding, k); // [id,score,id,score,...]
    const out: Array<[number, number]> = [];
    for (let i = 0; i < flat.length; i += 2) {
      out.push([flat[i]!, flat[i + 1]!]);
    }
    return out;
  }

  query(queryEmbeddings: readonly Float32Array[]): Array<[number, number]> {
    const seen = new Set<number>();
    const out: Array<[number, number]> = [];
    for (let i = 0; i < queryEmbeddings.length; i++) {
      for (const [neighbour] of this.queryOne(queryEmbeddings[i]!)) {
        if (neighbour === i) continue;
        if (neighbour < 0) continue;
        const a = Math.min(i, neighbour);
        const b = Math.max(i, neighbour);
        const key = a * 100000003 + b;
        if (seen.has(key)) continue;
        seen.add(key);
        out.push([a, b]);
      }
    }
    return out;
  }

  queryWithScores(
    queryEmbeddings: readonly Float32Array[],
  ): Array<[number, number, number]> {
    const best = new Map<number, [number, number, number]>();
    for (let i = 0; i < queryEmbeddings.length; i++) {
      for (const [neighbour, score] of this.queryOne(queryEmbeddings[i]!)) {
        if (neighbour === i) continue;
        if (neighbour < 0) continue;
        const a = Math.min(i, neighbour);
        const b = Math.max(i, neighbour);
        const key = a * 100000003 + b;
        const prev = best.get(key);
        if (!prev || score > prev[2]) best.set(key, [a, b, score]);
      }
    }
    return [...best.values()];
  }
}
