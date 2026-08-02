/**
 * keyIntegrityWasm.ts — synchronous, edge-safe loader for the key-integrity-core
 * structural certifier (group-by uniqueness + fan-out), compiled to wasm.
 *
 * This is the SAME kernel the Python reference `certify_key_integrity` computes,
 * so the structural certificate is identical across surfaces — proven by the
 * shared golden vector (`tests/parity/fixtures/key-integrity/
 * key_integrity_golden.json`, the same file `key-integrity-core/tests/golden.rs`
 * checks). Importing this module and calling `enableKeyIntegrityWasm()` reroutes
 * `keyIntegrity.ts::certifyKeyIntegrity`'s structural reduction off its
 * hand-written JS `Map` loop onto this one core.
 *
 * Edge-safe: no `node:*`. The wasm is inlined as base64 and instantiated
 * synchronously via `initSync`. The block crosses as a JSON object string and
 * the structural result crosses back as a JSON string — no typed-array marshaling.
 */
import { initSync, certify_structural_json } from "./_wasm/keyIntegrityWasmBindings.js";
import { KEY_INTEGRITY_WASM_BASE64 } from "./_wasm/keyIntegrityWasmBytes.js";
import {
  setKeyIntegrityWasmBackend,
  disableKeyIntegrityWasm,
  type KeyIntegrityStructuralInput,
  type KeyIntegrityStructuralResult,
} from "./keyIntegrityWasmBackend.js";

let initialized = false;

function base64ToBytes(b64: string): Uint8Array {
  const bin = atob(b64); // browsers, Workers, Node >= 18 — edge-safe
  const len = bin.length;
  const bytes = new Uint8Array(len);
  for (let i = 0; i < len; i++) bytes[i] = bin.charCodeAt(i);
  return bytes;
}

function ensureInit(): void {
  if (initialized) return;
  initSync({ module: base64ToBytes(KEY_INTEGRITY_WASM_BASE64) });
  initialized = true;
}

/** Run the structural certifier through the shared key-integrity-core kernel. */
export function certifyStructural(
  input: KeyIntegrityStructuralInput,
): KeyIntegrityStructuralResult {
  ensureInit();
  const payload = JSON.stringify({
    n_rows: input.nRows,
    // undefined → null (JSON.stringify does this inside arrays), matching the
    // pure-TS groupKey's `v === undefined ? null : v`.
    group_columns: input.groupColumns.map((c) => [...c]),
    measures: input.measures.map((m) => ({ name: m.name, values: [...m.values] })),
  });
  const out = JSON.parse(certify_structural_json(payload)) as {
    n_key_groups: number;
    duplicate_key_groups: number;
    max_fan_out: number;
    is_unique_at_grain: boolean;
    measure_fan_out: Record<string, number>;
  };
  return {
    nKeyGroups: out.n_key_groups,
    duplicateKeyGroups: out.duplicate_key_groups,
    maxFanOut: out.max_fan_out,
    isUniqueAtGrain: out.is_unique_at_grain,
    measureFanOut: out.measure_fan_out,
  };
}

/**
 * Route `certifyKeyIntegrity`'s structural reduction off its hand-written JS loop
 * onto the shared key-integrity-core kernel. Idempotent. Call
 * `disableKeyIntegrityWasm()` to revert (test isolation / opt-out).
 */
export function enableKeyIntegrityWasm(): void {
  ensureInit();
  setKeyIntegrityWasmBackend({ certifyStructural });
}

export { disableKeyIntegrityWasm };
