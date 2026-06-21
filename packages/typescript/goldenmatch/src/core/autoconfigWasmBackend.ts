/**
 * autoconfigWasmBackend.ts — lean runtime registry for the OPT-IN autoconfig
 * wasm backend. Edge-safe: no `node:` imports, and (critically) NO value import
 * of the heavy `autoconfigWasm` module — only `import type`, which tsc/esbuild
 * fully erase. So importing this registry (which the always-on planner/classifier
 * do) pulls ZERO wasm bytes into the bundle.
 *
 * The heavy `goldenmatch/core/autoconfig-wasm` subpath registers a backend here
 * via `enableAutoconfigWasm()`; until then `get*` returns null and callers use
 * their pure-TS path. This mirrors the score-wasm `goldenmatch-wasm-runtime`
 * split (shared lean registry + consumer-owned heavy enable) and Python's
 * default-OFF native gate (`native_enabled("autoconfig")`).
 */
import type { ExecutionPlan } from "./executionPlan.js";
import type {
  PlannerInput,
  CoreColumnStats,
  CoreColumnProfile,
} from "./autoconfigWasm.js";

/** The shared decision surface the wasm core implements. */
export interface AutoconfigWasmBackend {
  decidePlan(input: PlannerInput): ExecutionPlan;
  classifyColumns(cols: readonly CoreColumnStats[]): CoreColumnProfile[];
}

let _backend: AutoconfigWasmBackend | null = null;

/** Register the wasm backend (called by the opt-in subpath's enable fn). */
export function setAutoconfigWasmBackend(backend: AutoconfigWasmBackend): void {
  _backend = backend;
}

/** The registered backend, or null when wasm is not enabled (the default). */
export function getAutoconfigWasmBackend(): AutoconfigWasmBackend | null {
  return _backend;
}

/** Clear the backend — restores the pure-TS path (test isolation / opt-out). */
export function disableAutoconfigWasm(): void {
  _backend = null;
}

/** True when the opt-in wasm backend is currently registered. */
export function isAutoconfigWasmEnabled(): boolean {
  return _backend !== null;
}
