/**
 * Public opt-in WASM API. enableWasm() is async (browsers ban sync instantiation
 * >4KB); after it resolves, the existing SYNC scoreMatrix runs against the
 * instantiated module. Pure-TS stays the default + fallback.
 *
 * Plumbing (byte loader + enable skeleton + backend registry) lives in the
 * shared `goldenmatch-wasm-runtime` package; this module owns the scorer backend
 * + the artifact URL (resolved here so `import.meta.url` points at this package's
 * own dist).
 */
import { enableWasmBackend, type EnableOptions } from "goldenmatch-wasm-runtime";
import { setScorerBackend } from "./backend.js";

export type { ScorerBackend } from "./backend.js";
export { WASM_COVERED_SCORERS } from "./backend.js";

export type EnableWasmOptions = EnableOptions;

let _enabled = false;

/**
 * Load + instantiate the WASM scorer backend and register it. Returns true on
 * success. On failure returns false (pure-TS stays active) unless require:true.
 * Idempotent while a backend is active — a second call returns true and IGNORES
 * any new opts (no reinstantiation / backend swap under an in-flight dedupe);
 * call disableWasm() first to load different bytes.
 */
export async function enableWasm(opts: EnableWasmOptions = {}): Promise<boolean> {
  if (_enabled) return true;
  try {
    // Lazy: default (pure-TS) users never load the glue. loader.js's
    // instantiateBackend does the wasm-bindgen glue import inside itself; byte
    // resolution + the try/fallback live in enableWasmBackend.
    const { instantiateBackend } = await import("./loader.js");
    const ok = await enableWasmBackend(
      opts,
      instantiateBackend,
      setScorerBackend,
      new URL("./artifacts/score_wasm_bg.wasm", import.meta.url),
    );
    if (ok) _enabled = true;
    return ok;
  } catch (err) {
    // Reached only if the loader module import itself fails (rare), or
    // require:true re-threw out of enableWasmBackend.
    if (opts.require) throw err;
    return false;
  }
}

/** Reset to pure-TS (test isolation; mirrors setSyncEmbedder(null)). */
export function disableWasm(): void {
  setScorerBackend(null);
  _enabled = false;
}
