/**
 * sketchWasmBackend.ts — lean runtime registry for the OPT-IN sketch (MinHash /
 * LSH) wasm backend. Edge-safe: no `node:` imports, and (unlike the heavy
 * `sketchWasm` loader) it pulls ZERO wasm bytes into the bundle — it owns only
 * the registry singleton + the backend shape.
 *
 * The heavy `goldenmatch/core/sketch-wasm` subpath registers a backend here via
 * `enableSketchWasm()`; until then `getSketchWasmBackend()` returns null and
 * `sketch.ts` runs its pure-TS path (the faithful fallback). Mirrors the
 * `suggestWasmBackend` / `autoconfigWasmBackend` split and Python's default-OFF
 * native gate (`pip install goldenmatch[native]`).
 *
 * The 64-bit sketch hashes are `bigint` on this side (u64 in the kernel).
 */

/**
 * The shared sketch surface the wasm core implements. The reroute is at the
 * per-record end-to-end level (`sketchBandHashes`) — the single entry a blocker
 * calls, and the one that runs the WHOLE pipeline (shingle -> signature -> band
 * hashes) in Rust. The lower-level primitives (`baseHash`/`signature`/
 * `bandHashes`/`shingle`) stay pure-TS: they're byte-identical (golden-verified)
 * and are called in tight loops where a per-op wasm boundary crossing would be
 * pure overhead.
 */
export interface SketchWasmBackend {
  /** End-to-end per record: shingle -> signature -> band hashes. */
  sketchBandHashes(
    text: string,
    mode: string,
    k: number,
    numPerms: number,
    numBands: number,
    seed: bigint,
  ): bigint[];
  /**
   * SimHash signature: 0/1 byte per random hyperplane (project a dense embedding
   * through the seeded Rademacher matrix + sign). The expensive, divergence-prone
   * half of the semantic sketch runs in the kernel; the banding step
   * (`simhashBandHashes`) stays pure-TS (cheap, golden-verified base_hash),
   * mirroring the MinHash `bandHashes` split.
   */
  simhashSignature(
    vector: readonly number[],
    numPlanes: number,
    seed: bigint,
  ): number[];
}

let _backend: SketchWasmBackend | null = null;

/** Register the wasm backend (called by the opt-in subpath's enable fn). */
export function setSketchWasmBackend(backend: SketchWasmBackend): void {
  _backend = backend;
}

/** The registered backend, or null when wasm is not enabled (the default). */
export function getSketchWasmBackend(): SketchWasmBackend | null {
  return _backend;
}

/** Clear the backend — restores the pure-TS path (test isolation / opt-out). */
export function disableSketchWasm(): void {
  _backend = null;
}

/** True when the opt-in wasm backend is currently registered. */
export function isSketchWasmEnabled(): boolean {
  return _backend !== null;
}
