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

/**
 * `universal: true` selects the UNIVERSAL loader (R1 Workstream A): resolve the
 * artifact from the base64-INLINED `score_wasm_base64.js` module instead of via
 * `import.meta.url` + fs/fetch. This is the only path that loads edge-safe
 * across all four JS targets (Node/browser/Workers/Deno) and every bundler with
 * no per-target hacks (Workers/Deno can't do `import.meta.url`-relative asset
 * resolution). Costs bundle size (base64 ~= 4/3 of the raw wasm). The DEFAULT
 * (`universal` unset/false) keeps the URL/fs/fetch loader. Either way pure-TS
 * stays the default + fallback — `universal` only changes HOW the bytes are
 * resolved once you opt into WASM.
 */
export type EnableWasmOptions = EnableOptions & { readonly universal?: boolean };

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

    // Universal loader (opt-in): resolve bytes from the base64-inlined module so
    // no fetch/fs/import.meta.url is needed (edge-safe on Workers/Deno/bundlers).
    // If the generated module is absent (default checkout) we leave opts as-is
    // and fall through to the URL loader, which then falls back to pure-TS.
    let resolveOpts: EnableWasmOptions = opts;
    if (opts.universal && opts.wasmBytes === undefined && opts.wasmBase64 === undefined) {
      const { loadInlinedWasmBase64 } = await import("./universal-loader.js");
      const b64 = await loadInlinedWasmBase64();
      if (b64 !== null) resolveOpts = { ...opts, wasmBase64: b64 };
      else if (opts.require) {
        throw new Error(
          "enableWasm({ universal: true, require: true }): inlined base64 artifact " +
            "(score_wasm_base64.js) absent — run build_wasm.sh to generate it",
        );
      }
    }

    const ok = await enableWasmBackend(
      resolveOpts,
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
