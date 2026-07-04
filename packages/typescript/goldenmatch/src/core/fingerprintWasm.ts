/**
 * fingerprintWasm.ts — synchronous, edge-safe loader for the fingerprint-core
 * canonical record-fingerprint kernel, compiled to wasm.
 *
 * This is the SAME kernel the Python native path and the DuckDB / Postgres
 * surfaces run, so the record-id hash is identical across surfaces — proven by
 * the shared golden vector (`tests/parity/fixtures/fingerprint/
 * fingerprint_golden.json`, the same file `fingerprint-core/tests/golden.rs`
 * checks). Importing this module and calling `enableFingerprintWasm()` reroutes
 * `record-fingerprint.ts::recordFingerprint` off its hand-written canonicalizer
 * onto this one core (for JSON-primitive-safe records; see that file).
 *
 * Edge-safe: no `node:*`. The wasm is inlined as base64 and instantiated
 * synchronously via `initSync`. The record crosses as a JSON object string and
 * the 64-hex digest crosses back as a string — no typed-array marshaling.
 */
import { initSync, fingerprint_json } from "./_wasm/fingerprintWasmBindings.js";
import { FINGERPRINT_WASM_BASE64 } from "./_wasm/fingerprintWasmBytes.js";
import {
  setFingerprintWasmBackend,
  disableFingerprintWasm,
} from "./fingerprintWasmBackend.js";

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
  initSync({ module: base64ToBytes(FINGERPRINT_WASM_BASE64) });
  initialized = true;
}

/**
 * Canonical SHA-256 fingerprint (64 lowercase hex) of a record given as a JSON
 * object string, via the shared fingerprint-core kernel. Throws on invalid
 * JSON, a non-object, a nested value, or a non-finite float.
 */
export function fingerprintJson(recordJson: string): string {
  ensureInit();
  return fingerprint_json(recordJson);
}

/**
 * Route `record-fingerprint.ts::recordFingerprint` off its hand-written
 * canonicalizer onto the shared fingerprint-core kernel (for JSON-primitive-safe
 * records). Idempotent. Call `disableFingerprintWasm()` to revert (test
 * isolation / opt-out).
 */
export function enableFingerprintWasm(): void {
  ensureInit();
  setFingerprintWasmBackend({ fingerprintJson });
}

export { disableFingerprintWasm };
