/**
 * fsScoreBackend.ts — lean runtime registry for the Fellegi-Sunter block-scoring
 * reroute onto the shared `goldenmatch-fs-core` wasm kernel.
 *
 * Edge-safe and BUNDLE-LEAN: unlike the heavy `fsScore` / `fsWasm` loaders it
 * pulls ZERO wasm bytes into the default `core` bundle — it owns only the
 * registry singleton + the backend shape + an opt-out flag. `pipeline.ts`
 * (always-on, edge-safe) value-imports ONLY this module; it `import type`s
 * nothing heavy.
 *
 * The heavy `goldenmatch/core/fs-scoring` subpath registers a backend here via
 * `enableFsWasmScoring()`. When a backend is registered AND the matchkey is
 * kernel-expressible (`backend.eligible(mk)`), `scoreProbabilisticBlocks` runs
 * the shared Rust FS kernel — the SAME `fs_core::score_fs_pair` the Python
 * `goldenmatch-native` wheel runs — so TS FS block scoring aligns byte-for-byte
 * with Python-native (the #1854 FIXED full-field weight range, not the pure-TS
 * per-pair shrinking range). Until registered, or for a config the kernel can't
 * express (embedding / name-refdata / TF fields), the pure-TS
 * `scoreProbabilistic` runs as the classified, conformance-tested FALLBACK.
 *
 * Note on the "default": the reroute is DEFAULT-PREFERRING — the moment the
 * kernel is loaded (one `enableFsWasmScoring()` call, mirroring the sibling
 * wasm reroutes) it takes over. It is not statically wired into `core/index`
 * because `tsup` builds with `splitting:false`, so a static/dynamic import of
 * the inlined kernel from the edge-safe core would bloat `dist/core/index.js`
 * with the ~187 KB wasm — the documented "no wasm in the default core bundle"
 * discipline. `disableFsWasmScoring()` clears it (test isolation / opt-out).
 */
import type { Row, MatchkeyConfig, ScoredPair } from "./types.js";
import type { EMResult } from "./probabilistic.js";

/** The FS block-scoring primitive the wasm reroute implements. */
export interface FsScoreBackend {
  /**
   * True iff `mk` is expressible by the shared `fs_core` kernel (every field +
   * negative-evidence scorer is one the kernel's `score_one` / `field_similarity`
   * implements, and no field opts into TF adjustment). When false the caller
   * MUST use the pure-TS `scoreProbabilistic` fallback.
   */
  eligible(mk: MatchkeyConfig): boolean;
  /**
   * Score every within-block pair of `blockRows` via the shared FS kernel and
   * return those at/above `threshold` as `ScoredPair`s (scores rounded to 4dp,
   * matching Python-native + the pure-TS scorer). `em` is the trained
   * `EMResult`; normalization uses the FIXED full-field weight range
   * (`fsWeightRange`), i.e. the Python operating point. Caller guarantees
   * `eligible(mk)`.
   */
  scoreBlock(
    blockRows: readonly Row[],
    mk: MatchkeyConfig,
    em: EMResult,
    threshold: number,
  ): ScoredPair[];
}

let _backend: FsScoreBackend | null = null;

/** Register the wasm FS-scoring backend (called by the opt-in subpath's enable fn). */
export function setFsScoreBackend(backend: FsScoreBackend): void {
  _backend = backend;
}

/** The registered backend, or null when the reroute is not enabled (default). */
export function getFsScoreBackend(): FsScoreBackend | null {
  return _backend;
}

/** Clear the backend — restores the pure-TS path (test isolation / opt-out). */
export function disableFsWasmScoring(): void {
  _backend = null;
}

/** True when the FS wasm reroute is currently registered. */
export function isFsWasmScoringEnabled(): boolean {
  return _backend !== null;
}
