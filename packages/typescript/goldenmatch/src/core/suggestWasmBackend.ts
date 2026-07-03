/**
 * suggestWasmBackend.ts — lean runtime registry for the OPT-IN healer
 * (config-suggestion) wasm backend. Edge-safe: no `node:` imports, and (unlike
 * the heavy `suggestWasm` loader) it pulls ZERO wasm bytes into the bundle —
 * it owns only the registry singleton + the kernel-input shape.
 *
 * The heavy `goldenmatch/core/suggest-wasm` subpath registers a backend here
 * via `enableSuggestWasm()`; until then `get*` returns null and callers (the
 * TS healer surface) fall back to graceful-empty (`[]`). This mirrors the
 * autoconfig-wasm `setAutoconfigWasmBackend` split and Python's default-OFF
 * native gate (`pip install goldenmatch[native]`).
 */

/**
 * The five JSON strings the wasm `suggest_review` packs into one object — the
 * exact `suggest_from_json` arg set the Rust kernel decodes (snake_case keys to
 * match the wasm-bindgen `In` struct).
 */
export interface SuggestKernelInput {
  readonly scored_pairs: string;
  readonly clusters: string;
  readonly column_signals: string;
  readonly config: string;
  readonly priors: string;
}

/** The shared decision surface the wasm core implements. */
export interface SuggestWasmBackend {
  /** Raw kernel call: the 5 JSON strings packed -> suggestion JSON string. */
  suggestReview(input: SuggestKernelInput): string;
}

let _backend: SuggestWasmBackend | null = null;

/** Register the wasm backend (called by the opt-in subpath's enable fn). */
export function setSuggestWasmBackend(backend: SuggestWasmBackend): void {
  _backend = backend;
}

/** The registered backend, or null when wasm is not enabled (the default). */
export function getSuggestWasmBackend(): SuggestWasmBackend | null {
  return _backend;
}

/** Clear the backend — restores graceful-empty (test isolation / opt-out). */
export function disableSuggestWasm(): void {
  _backend = null;
}

/** True when the opt-in wasm backend is currently registered. */
export function isSuggestWasmEnabled(): boolean {
  return _backend !== null;
}
