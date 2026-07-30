/**
 * index.ts -- Batteries-included package entry (`goldenmatch`).
 *
 * Re-exports the full edge-safe core API AND auto-enables the shared `fs_core`
 * wasm kernel for Fellegi-Sunter block scoring. So the DEFAULT `dedupe()` /
 * `match()` probabilistic path runs the SAME kernel the Python `goldenmatch-native`
 * wheel runs -- Python's #1854 FIXED full-field weight operating point -- with no
 * opt-in call. The pure-TS `scoreProbabilistic` stays the classified fallback for
 * configs the kernel can't express (embedding / name-refdata / TF-adjusted fields).
 *
 * Cost of this default: the bare `goldenmatch` bundle carries the inlined
 * ~187 KB fs-wasm. It is registered eagerly but COMPILED LAZILY -- no wasm is
 * instantiated until the first kernel-eligible FS block is scored -- so merely
 * importing `goldenmatch` stays cheap.
 *
 * Want the LEAN, wasm-free bundle (Cloudflare Workers / bundle-size-sensitive
 * edge)? Import from `goldenmatch/core` instead: identical API, pure-TS FS
 * scoring, and you opt IN to the kernel yourself with `enableFsWasmScoring()`.
 * Already on `goldenmatch` and want the old pure-TS behavior? Call
 * `disableFsWasmScoring()` once at startup.
 *
 * For Node-only helpers (file I/O, config loading, CLI), import from
 * `goldenmatch/node`.
 */
export * from "./core/index.js";

// Re-export the kernel controls so batteries consumers can opt out (or re-enable)
// without reaching into the `goldenmatch/core/fs-scoring` subpath.
export { enableFsWasmScoring, disableFsWasmScoring } from "./core/fsScore.js";

import { enableFsWasmScoring as registerFsKernel } from "./core/fsScore.js";

// Batteries-included default: register the fs-core kernel so the probabilistic
// scoring path aligns with Python-native out of the box. Idempotent; the wasm is
// not instantiated until the first kernel-eligible FS block is scored.
registerFsKernel();
