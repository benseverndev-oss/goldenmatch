/**
 * keyIntegrityWasmBackend.ts — lean runtime registry for the OPT-IN structural
 * key-integrity wasm backend. Edge-safe: no `node:` imports, and (unlike the
 * heavy `keyIntegrityWasm` loader) it pulls ZERO wasm bytes into the bundle — it
 * owns only the registry singleton + the backend shape.
 *
 * The heavy `goldenmatch/core/key-integrity-wasm` subpath registers a backend
 * here via `enableKeyIntegrityWasm()`; until then `getKeyIntegrityWasmBackend()`
 * returns null and `certifyKeyIntegrity` runs its hand-written group-by reduction
 * (the faithful fallback + default). Mirrors the `fingerprintWasmBackend` split
 * and Python's default-OFF native gate.
 */

/** The structural reduction the wasm core implements (group-by uniqueness +
 * fan-out). Values are JSON scalars (string / number / boolean / null). */
export interface KeyIntegrityStructuralInput {
  nRows: number;
  /** Column-oriented group key columns (each length nRows). */
  groupColumns: readonly (readonly unknown[])[];
  /** Column-oriented NUMERIC measures (the host pre-filters non-numeric ones). */
  measures: readonly { name: string; values: readonly unknown[] }[];
}

/** The structural result — the structural tier of a KeyIntegrityCertificate. */
export interface KeyIntegrityStructuralResult {
  nKeyGroups: number;
  duplicateKeyGroups: number;
  maxFanOut: number;
  isUniqueAtGrain: boolean;
  measureFanOut: Record<string, number>;
}

/** The primitive the wasm core exposes over the JSON boundary. */
export interface KeyIntegrityWasmBackend {
  certifyStructural(input: KeyIntegrityStructuralInput): KeyIntegrityStructuralResult;
}

let _backend: KeyIntegrityWasmBackend | null = null;

/** Register the wasm backend (called by the opt-in subpath's enable fn). */
export function setKeyIntegrityWasmBackend(backend: KeyIntegrityWasmBackend): void {
  _backend = backend;
}

/** The registered backend, or null when wasm is not enabled (the default). */
export function getKeyIntegrityWasmBackend(): KeyIntegrityWasmBackend | null {
  return _backend;
}

/** Clear the backend — restores the pure-TS path (test isolation / opt-out). */
export function disableKeyIntegrityWasm(): void {
  _backend = null;
}

/** True when the opt-in wasm backend is currently registered. */
export function isKeyIntegrityWasmEnabled(): boolean {
  return _backend !== null;
}
