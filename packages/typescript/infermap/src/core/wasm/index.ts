/**
 * Public opt-in WASM API for infermap detect. enableInfermapWasm() is async;
 * after it resolves, the sync detectDomain* runs against the instantiated module.
 * Pure-TS stays the default + fallback. Plumbing lives in goldenmatch-wasm-runtime.
 */
import { enableWasmBackend, type EnableOptions } from "goldenmatch-wasm-runtime";
import { setInfermapBackend } from "./backend.js";

export type { InfermapBackend } from "./backend.js";
export type EnableInfermapWasmOptions = EnableOptions;

let _enabled = false;

export async function enableInfermapWasm(
  opts: EnableInfermapWasmOptions = {},
): Promise<boolean> {
  if (_enabled) return true;
  try {
    const { instantiateBackend } = await import("./loader.js");
    const ok = await enableWasmBackend(
      opts,
      instantiateBackend,
      setInfermapBackend,
      new URL("./artifacts/infermap_wasm_bg.wasm", import.meta.url),
    );
    if (ok) _enabled = true;
    return ok;
  } catch (err) {
    if (opts.require) throw err;
    return false;
  }
}

export function disableInfermapWasm(): void {
  setInfermapBackend(null);
  _enabled = false;
}
