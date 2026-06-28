/**
 * goldenprofileWasm.ts — the HEAVY, opt-in entry point (`goldenprofile/wasm`).
 *
 * This is the ONLY module that embeds the wasm bytes. Importing it (and calling
 * `enableGoldenprofileWasm()`) registers the kernel-backed backend so
 * `resolveProfiles()` works. The base `goldenprofile` entry pulls none of this —
 * default consumers load zero wasm bytes and stay edge-pure.
 *
 * The TS/JS analog of `pip install goldenprofile-native`: same one Rust kernel
 * (`goldenprofile-core`), surfaced here through `goldenprofile-wasm` instead of
 * the pyo3 shim. Byte-identical resolutions by construction.
 */
import { initSync, resolve_json } from "./_wasm/goldenprofileWasmBindings.js";
import { GOLDENPROFILE_WASM_BASE64 } from "./_wasm/goldenprofileWasmBytes.js";
import { setGoldenprofileWasmBackend } from "./goldenprofileWasmBackend.js";

let _inited = false;

/** Decode the inlined base64 wasm to bytes — edge-safe (`atob`, no node:Buffer). */
function wasmBytes(): Uint8Array {
  const bin = atob(GOLDENPROFILE_WASM_BASE64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes;
}

/**
 * Initialize the wasm module (idempotent) and register the backend so
 * `resolveProfiles()` resolves through the kernel. Synchronous: `initSync`
 * compiles the inlined bytes — no `fetch`/`import.meta.url`.
 */
export function enableGoldenprofileWasm(): void {
  if (!_inited) {
    initSync({ module: wasmBytes() });
    _inited = true;
  }
  setGoldenprofileWasmBackend({
    resolveJson: (request: string): string => resolve_json(request),
  });
}
