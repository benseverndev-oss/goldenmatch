/**
 * fingerprintWasmBackend.ts — lean runtime registry for the OPT-IN
 * record-fingerprint wasm backend. Edge-safe: no `node:` imports, and (unlike
 * the heavy `fingerprintWasm` loader) it pulls ZERO wasm bytes into the bundle —
 * it owns only the registry singleton + the backend shape.
 *
 * The heavy `goldenmatch/core/fingerprint-wasm` subpath registers a backend here
 * via `enableFingerprintWasm()`; until then `getFingerprintWasmBackend()`
 * returns null and `recordFingerprint` runs its hand-written canonicalizer (the
 * faithful fallback). Mirrors the `graphWasmBackend` / `sketchWasmBackend` split
 * and Python's default-OFF native gate.
 */

/** The shared canonicalization primitive the wasm core implements. */
export interface FingerprintWasmBackend {
  /**
   * Canonical SHA-256 fingerprint (64 lowercase hex) of a record given as a
   * JSON object string — the SAME entry the DuckDB / Postgres surfaces use.
   * Throws on invalid JSON, a non-object, a nested value, or a non-finite float.
   */
  fingerprintJson(recordJson: string): string;
}

let _backend: FingerprintWasmBackend | null = null;

/** Register the wasm backend (called by the opt-in subpath's enable fn). */
export function setFingerprintWasmBackend(backend: FingerprintWasmBackend): void {
  _backend = backend;
}

/** The registered backend, or null when wasm is not enabled (the default). */
export function getFingerprintWasmBackend(): FingerprintWasmBackend | null {
  return _backend;
}

/** Clear the backend — restores the pure-TS path (test isolation / opt-out). */
export function disableFingerprintWasm(): void {
  _backend = null;
}

/** True when the opt-in wasm backend is currently registered. */
export function isFingerprintWasmEnabled(): boolean {
  return _backend !== null;
}
