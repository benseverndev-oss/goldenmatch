/**
 * Public opt-in WASM API. enableWasm() is async (browsers ban sync instantiation
 * >4KB); after it resolves, the existing SYNC scoreMatrix runs against the
 * instantiated module. Pure-TS stays the default + fallback.
 */
import { setScorerBackend } from "./backend.js";
import type { LoadOptions } from "./loader.js";

export type { ScorerBackend } from "./backend.js";
export { WASM_COVERED_SCORERS } from "./backend.js";

export interface EnableWasmOptions extends LoadOptions {
  /** Throw instead of falling back to pure-TS when the module can't load. */
  readonly require?: boolean;
}

let _enabled = false;

/**
 * Load + instantiate the WASM scorer backend and register it. Returns true on
 * success. On failure returns false (pure-TS stays active) unless require:true.
 * Idempotent while a backend is active.
 */
export async function enableWasm(opts: EnableWasmOptions = {}): Promise<boolean> {
  if (_enabled) return true;
  try {
    // Lazy: default (pure-TS) users never load the loader/glue/bytes.
    const { resolveWasmBytes, instantiateBackend } = await import("./loader.js");
    const bytes = await resolveWasmBytes(opts);
    const backend = await instantiateBackend(bytes);
    setScorerBackend(backend);
    _enabled = true;
    return true;
  } catch (err) {
    if (opts.require) throw err;
    return false;
  }
}

/** Reset to pure-TS (test isolation; mirrors setSyncEmbedder(null)). */
export function disableWasm(): void {
  setScorerBackend(null);
  _enabled = false;
}
