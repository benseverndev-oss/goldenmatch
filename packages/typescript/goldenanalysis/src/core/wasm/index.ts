/**
 * Public opt-in WASM API for goldenanalysis aggregates. enableAnalysisWasm() is
 * async (browsers ban sync instantiation >4KB); after it resolves, the existing
 * SYNC histogram/quantile run against the instantiated module. Pure-TS stays the
 * default + fallback.
 *
 * Plumbing (byte loader + enable skeleton + registry) lives in the shared
 * `goldenmatch-wasm-runtime` package; this module owns the aggregate backend +
 * the artifact URL (resolved here so import.meta.url points at this package's
 * own dist).
 */
import { enableWasmBackend, type EnableOptions } from "goldenmatch-wasm-runtime";
import { setAnalysisBackend } from "./backend.js";

export type { AnalysisBackend } from "./backend.js";

export type EnableAnalysisWasmOptions = EnableOptions;

let _enabled = false;

/**
 * Load + instantiate the WASM aggregate backend and register it. Returns true on
 * success. On failure returns false (pure-TS stays active) unless require:true.
 * Idempotent while a backend is active; call disableAnalysisWasm() to reload.
 */
export async function enableAnalysisWasm(
  opts: EnableAnalysisWasmOptions = {},
): Promise<boolean> {
  if (_enabled) return true;
  try {
    // Lazy: default (pure-TS) users never load the glue.
    const { instantiateBackend } = await import("./loader.js");
    const ok = await enableWasmBackend(
      opts,
      instantiateBackend,
      setAnalysisBackend,
      new URL("./artifacts/analysis_wasm_bg.wasm", import.meta.url),
    );
    if (ok) _enabled = true;
    return ok;
  } catch (err) {
    if (opts.require) throw err;
    return false;
  }
}

/** Reset to pure-TS (test isolation). */
export function disableAnalysisWasm(): void {
  setAnalysisBackend(null);
  _enabled = false;
}
