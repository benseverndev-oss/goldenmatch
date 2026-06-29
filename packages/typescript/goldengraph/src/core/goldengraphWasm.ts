/**
 * goldengraphWasm.ts — the HEAVY, opt-in entry point (`goldengraph/wasm`).
 *
 * The ONLY module that embeds the wasm bytes. Importing it and calling
 * `enableGoldengraphWasm()` registers the kernel-backed backend so the query
 * functions in the base entry work. The base `goldengraph` import pulls none of
 * this — default consumers load zero wasm bytes and stay edge-pure.
 *
 * The TS/JS analog of `pip install goldengraph-native`: one Rust kernel
 * (`goldengraph-core`), surfaced here through `goldengraph-wasm`.
 */
import {
  build_graph,
  neighborhood,
  seeds_by_name,
  communities,
  store_append,
  store_as_of,
  store_history,
  initSync,
} from "./_wasm/goldengraphWasmBindings.js";
import { GOLDENGRAPH_WASM_BASE64 } from "./_wasm/goldengraphWasmBytes.js";
import { setGoldengraphWasmBackend } from "./goldengraphWasmBackend.js";

let _inited = false;

/** Decode the inlined base64 wasm to bytes — edge-safe (`atob`, no node:Buffer). */
function wasmBytes(): Uint8Array {
  const bin = atob(GOLDENGRAPH_WASM_BASE64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes;
}

/**
 * Initialize the wasm module (idempotent) and register the backend so the
 * GoldenGraph query functions resolve through the kernel. Synchronous —
 * `initSync` compiles the inlined bytes (no `fetch`/`import.meta.url`).
 */
export function enableGoldengraphWasm(): void {
  if (!_inited) {
    initSync({ module: wasmBytes() });
    _inited = true;
  }
  setGoldengraphWasmBackend({
    buildGraph: (m, e, r) => build_graph(m, e, r),
    neighborhood: (g, s, h) => neighborhood(g, s, h),
    seedsByName: (g, n) => seeds_by_name(g, n),
    communities: (g) => communities(g),
    storeAppend: (s, b) => store_append(s, b),
    // valid_t/tx_t/id are i64/u64 in the kernel -> wasm-bindgen wants BigInt.
    storeAsOf: (s, v, t) => store_as_of(s, BigInt(v), BigInt(t)),
    storeHistory: (s, id) => store_history(s, BigInt(id)),
  });
}
