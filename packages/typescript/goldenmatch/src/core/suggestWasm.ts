/**
 * suggestWasm.ts — synchronous, edge-safe loader for the shared healer
 * (config-suggestion) core (the `goldenmatch-suggest-core` Rust kernel, compiled
 * to wasm via `suggest-wasm`).
 *
 * This is the SAME kernel the Python `goldenmatch-native` wheel calls through
 * `suggest_config`, so the suggestion output is byte-identical across
 * Python / Rust / TS — proven by the shared golden vectors
 * (`tests/parity/fixtures/suggest/*.json`).
 *
 * Edge-safe: no `node:*` imports. The wasm is inlined as base64 and instantiated
 * synchronously via wasm-bindgen's `initSync`, so the public API stays sync and
 * works in browsers / Workers / edge runtimes (no `fs`, no `fetch`).
 *
 * The kernel speaks the packed five-JSON-string input (`suggest_from_json`); this
 * module wraps it behind `suggestReview(SuggestKernelInput)` and registers it as
 * the backend for the always-on healer surface via `enableSuggestWasm()`.
 */
import { initSync, suggest_review } from "./_wasm/suggestWasmBindings.js";
import { SUGGEST_WASM_BASE64 } from "./_wasm/suggestWasmBytes.js";
import {
  setSuggestWasmBackend,
  disableSuggestWasm,
  type SuggestKernelInput,
} from "./suggestWasmBackend.js";

// ---------------------------------------------------------------------------
// wasm init (lazy, once)
// ---------------------------------------------------------------------------

let initialized = false;

function base64ToBytes(b64: string): Uint8Array {
  // atob is available in browsers, Workers, and Node >= 18 — edge-safe.
  const bin = atob(b64);
  const len = bin.length;
  const bytes = new Uint8Array(len);
  for (let i = 0; i < len; i++) bytes[i] = bin.charCodeAt(i);
  return bytes;
}

function ensureInit(): void {
  if (initialized) return;
  initSync({ module: base64ToBytes(SUGGEST_WASM_BASE64) });
  initialized = true;
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Run the healer kernel: pack the five JSON strings, return the suggestion JSON
 * array string. Routes through the shared wasm core (byte-parity with
 * Python/Rust).
 */
export function suggestReview(input: SuggestKernelInput): string {
  ensureInit();
  return suggest_review(JSON.stringify(input));
}

/**
 * Escape hatch for the parity harness: call the kernel with the already-packed
 * input JSON string verbatim, bypassing the `JSON.stringify` step so the test
 * compares wasm output against the golden vectors byte-for-byte.
 */
export function suggestReviewRawJson(inputJson: string): string {
  ensureInit();
  return suggest_review(inputJson);
}

// ---------------------------------------------------------------------------
// Opt-in enable: register this wasm core as the healer backend. Importing THIS
// module is what pays the wasm cost (it statically embeds the base64); the main
// `goldenmatch/core` graph only touches the lean registry, so default bundles
// carry no wasm. Sync because the bytes are inlined + `initSync` is synchronous.
// ---------------------------------------------------------------------------

/**
 * Enable the shared wasm healer kernel as the backend for the TS healer surface.
 * After this call, config suggestions are byte-parity with Python/Rust.
 * Graceful-empty stays the default until this is called; `disableSuggestWasm()`
 * reverts.
 *
 * Wraps init + a smoke call in try/catch: on any load/call failure the backend
 * is left unregistered (so callers keep returning `[]`, never throw) and this
 * returns `false`. Returns `true` once the kernel is live and registered.
 */
export function enableSuggestWasm(): boolean {
  try {
    ensureInit();
    // Smoke-call the kernel on a benign empty input so a broken/incompatible
    // wasm fails HERE (→ return false) rather than at the first real suggest.
    suggest_review(
      JSON.stringify({
        scored_pairs: '{"score":[],"n_pairs":0}',
        clusters: "[]",
        column_signals: "[]",
        config: '{"matchkeys":[],"negative_evidence":[]}',
        priors: '{"counts":{}}',
      }),
    );
    setSuggestWasmBackend({ suggestReview });
    return true;
  } catch {
    disableSuggestWasm();
    return false;
  }
}

export { disableSuggestWasm };
