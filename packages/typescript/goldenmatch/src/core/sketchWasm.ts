/**
 * sketchWasm.ts — synchronous, edge-safe loader for the sketch-core MinHash/LSH
 * kernel, compiled to wasm.
 *
 * This is the SAME code the Python reference (`goldenmatch/core/sketch.py`) and
 * the Rust `sketch-core` crate run, so the u64 hash output is byte-identical
 * across Python / Rust / TS — proven by the shared golden vector
 * (`tests/parity/fixtures/sketch/sketch_golden.json`, the same file
 * `sketch-core/tests/golden.rs` checks). Importing this module and calling
 * `enableSketchWasm()` reroutes `sketch.ts` (and the LSH / SimHash blockers that
 * build on it) off its hand-written pure-TS BigInt arithmetic onto this one core.
 *
 * Edge-safe: no `node:*`. The wasm is inlined as base64 and instantiated
 * synchronously via wasm-bindgen's `initSync` (browsers / Workers / Node). The
 * 64-bit hashes cross the boundary as `BigUint64Array`; this module hides the
 * `bigint[] <-> BigUint64Array` marshaling behind the `sketch.ts` signatures.
 */
import {
  initSync,
  base_hash,
  signature as wasm_signature,
  band_hashes as wasm_band_hashes,
  sketch_band_hashes,
  simhash_signature,
} from "./_wasm/sketchWasmBindings.js";
import { SKETCH_WASM_BASE64 } from "./_wasm/sketchWasmBytes.js";
import {
  setSketchWasmBackend,
  disableSketchWasm,
} from "./sketchWasmBackend.js";

// ── one-time synchronous wasm init (edge-safe: atob, no fs/fetch) ────────────

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
  initSync({ module: base64ToBytes(SKETCH_WASM_BASE64) });
  initialized = true;
}

// ── typed wrappers (BigUint64Array boundary hidden) ──────────────────────────

/** FNV-1a(64) + splitmix64 finalizer over `data`. */
export function baseHash(data: Uint8Array): bigint {
  ensureInit();
  return base_hash(data);
}

/** MinHash signature (`numPerms` values) of a shingle set, seeded by `seed`. */
export function signature(
  shingles: readonly bigint[],
  numPerms: number,
  seed: bigint,
): bigint[] {
  ensureInit();
  return Array.from(
    wasm_signature(BigUint64Array.from(shingles), numPerms, seed),
  );
}

/** Banded-LSH bucket hashes: one per band over a signature. */
export function bandHashes(sig: readonly bigint[], numBands: number): bigint[] {
  ensureInit();
  return Array.from(wasm_band_hashes(BigUint64Array.from(sig), numBands));
}

/** End-to-end per record: shingle -> signature -> band hashes. */
export function sketchBandHashes(
  text: string,
  mode: string,
  k: number,
  numPerms: number,
  numBands: number,
  seed: bigint,
): bigint[] {
  ensureInit();
  return Array.from(
    sketch_band_hashes(text, mode, k, numPerms, numBands, seed),
  );
}

/**
 * SimHash signature via the shared kernel: project a dense embedding through the
 * seeded Rademacher matrix + sign, returning one 0/1 value per hyperplane. The
 * divergence-prone half of the semantic sketch (`simhash.ts::simhashSignature`);
 * the banding step stays pure-TS. The `Float64Array`/`Uint8Array` boundary is
 * hidden behind the `simhash.ts` `readonly number[]` signature.
 */
export function simhashSignature(
  vector: readonly number[],
  numPlanes: number,
  seed: bigint,
): number[] {
  ensureInit();
  return Array.from(
    simhash_signature(Float64Array.from(vector), numPlanes, seed),
  );
}

// ── opt-in enable / disable ──────────────────────────────────────────────────

/**
 * Route the sketch blocking paths off their pure-TS BigInt arithmetic onto the
 * shared wasm core: the MinHash-LSH end-to-end (`sketch.ts::sketchBandHashes`,
 * which `MinHashLSHBlocker` calls per record) AND the SimHash projection
 * (`simhash.ts::simhashSignature`, the semantic near-dup sketch). Idempotent.
 * Call `disableSketchWasm()` to revert (test isolation / opt-out). The
 * `baseHash`/`signature`/`bandHashes` wrappers above are exported for the parity
 * test (per-stage validation) but are not part of the reroute — see
 * `sketchWasmBackend.ts` for why.
 */
export function enableSketchWasm(): void {
  ensureInit();
  setSketchWasmBackend({ sketchBandHashes, simhashSignature });
}

export { disableSketchWasm };
