/**
 * Public opt-in WASM API for the goldenpipe planner kernel. enableWasm() is
 * async; after it resolves, the five planner seams consult the registered
 * backend instead of pure-TS. Pure-TS stays the default + fallback.
 */
import { enableWasmBackend, type EnableOptions } from "goldenmatch-wasm-runtime";
import { setPipeWasmBackend, getPipeWasmBackend } from "./backend.js";

export type { PipeWasmBackend } from "./backend.js";
export type EnableWasmOptions = EnableOptions;

let _enabled = false;

export async function enableWasm(opts: EnableWasmOptions = {}): Promise<boolean> {
  if (_enabled) return true;
  try {
    const { instantiateBackend } = await import("./loader.js");
    const ok = await enableWasmBackend(
      opts,
      instantiateBackend,
      setPipeWasmBackend,
      new URL("./artifacts/goldenpipe_wasm_bg.wasm", import.meta.url),
    );
    if (ok) _enabled = true;
    return ok;
  } catch (err) {
    if (opts.require) throw err;
    return false;
  }
}

export function isWasmEnabled(): boolean {
  return getPipeWasmBackend() !== null;
}

export function disableWasm(): void {
  setPipeWasmBackend(null);
  _enabled = false;
}
