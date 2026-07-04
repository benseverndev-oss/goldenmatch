/**
 * goldenembedWasm.ts — synchronous, edge-safe loader for the in-house embedder
 * (char n-gram featurize + linear projection head), compiled to wasm.
 *
 * This runs the SAME `goldenembed-core` kernels as the Python native path and
 * the SQL surfaces — so an embedding produced at the edge matches the others
 * within cosine tolerance (proven by the shared oracle
 * `tests/parity/fixtures/goldenembed/project_golden.json`, the same file
 * `goldenembed-core/tests/project_parity.rs` checks). This is the edge
 * embedding path that closes parity-roadmap P10 — the `goldenembed` native
 * runtime links ONNX Runtime (`ort`), which does NOT compile to wasm; the pure
 * `goldenembed-core` does.
 *
 * Edge-safe: no `node:*`. The wasm is inlined as base64 and instantiated
 * synchronously via `initSync`. The model (projection weights + optional bias +
 * featurizer params) is supplied by the caller; texts cross as `string[]` and
 * the `(n, dim)` row-major embedding matrix crosses back as one `Float32Array`.
 */
import { initSync, Embedder } from "./_wasm/goldenembedWasmBindings.js";
import { GOLDENEMBED_WASM_BASE64 } from "./_wasm/goldenembedWasmBytes.js";

let initialized = false;

function base64ToBytes(b64: string): Uint8Array {
  const bin = atob(b64); // browsers, Workers, Node >= 18 — edge-safe
  const len = bin.length;
  const bytes = new Uint8Array(len);
  for (let i = 0; i < len; i++) bytes[i] = bin.charCodeAt(i);
  return bytes;
}

function ensureInit(): void {
  if (initialized) return;
  initSync({ module: base64ToBytes(GOLDENEMBED_WASM_BASE64) });
  initialized = true;
}

/** An in-house embedding model: the learned projection head + featurizer params
 * (as saved by `GoldenEmbedModel.save` — `weights.npz` + `config.json`). */
export interface EmbedModel {
  /** Row-major `(nFeatures * dim)` projection matrix. */
  weights: Float32Array;
  /** Embedding dimension. */
  dim: number;
  /** Optional length-`dim` bias. */
  bias?: Float32Array | undefined;
  nFeatures: number;
  ngramMin: number;
  ngramMax: number;
  lowercase: boolean;
  /** Boundary marker wrapped around non-empty text (default `""`). */
  boundary: string;
  seed: number;
}

/** A ready embedder. Call `embed` per batch; `free()` releases the wasm handle. */
export interface GoldenEmbedder {
  readonly dim: number;
  /** Embed `texts` into a row-major `(texts.length * dim)` Float32Array (each
   * row L2-normalized). */
  embed(texts: string[]): Float32Array;
  /** Release the underlying wasm object. */
  free(): void;
}

/**
 * Build an edge embedder for `model`, running the shared goldenembed-core
 * kernels via wasm. Idempotently initializes the wasm module on first call.
 */
export function createEmbedder(model: EmbedModel): GoldenEmbedder {
  ensureInit();
  return new Embedder(
    model.weights,
    model.dim,
    model.bias,
    model.nFeatures,
    model.ngramMin,
    model.ngramMax,
    model.lowercase,
    model.boundary,
    model.seed,
  ) as unknown as GoldenEmbedder;
}
