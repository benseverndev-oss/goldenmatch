/**
 * goldenprofileWasmBackend.ts — lean runtime registry for the OPT-IN
 * GoldenProfile (Virtual Fingerprint) wasm backend. Edge-safe: no `node:`
 * imports, and (unlike the heavy `goldenprofileWasm` loader) it pulls ZERO wasm
 * bytes into the bundle — it owns only the registry singleton + the kernel I/O
 * shape.
 *
 * The heavy `goldenprofile/wasm` subpath registers a backend here via
 * `enableGoldenprofileWasm()`; until then `getGoldenprofileWasmBackend()`
 * returns null and `resolveProfiles()` throws an actionable error. This mirrors
 * the goldenmatch `suggestWasmBackend` split and Python's `_engine()` raise
 * (the Python surface likewise requires the `goldenprofile_native` wheel).
 */

/** The single JSON-boundary the wasm kernel implements (one core, one fn). */
export interface GoldenprofileWasmBackend {
  /** Resolve a JSON `ResolveRequest` into a JSON `Resolution` string. */
  resolveJson(request: string): string;
}

let _backend: GoldenprofileWasmBackend | null = null;

/** Register the wasm backend (called by the opt-in subpath's enable fn). */
export function setGoldenprofileWasmBackend(
  backend: GoldenprofileWasmBackend,
): void {
  _backend = backend;
}

/** The registered backend, or null when wasm is not enabled (the default). */
export function getGoldenprofileWasmBackend(): GoldenprofileWasmBackend | null {
  return _backend;
}

/** Clear the backend — restores the unregistered state (test isolation / opt-out). */
export function disableGoldenprofileWasm(): void {
  _backend = null;
}

/** True when the opt-in wasm backend is currently registered. */
export function isGoldenprofileWasmEnabled(): boolean {
  return _backend !== null;
}
