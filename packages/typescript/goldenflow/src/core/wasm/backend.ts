/**
 * backend.ts — opt-in WASM identifier-kernel backend registry. Edge-safe: no
 * node:* here.
 *
 * The active backend (if any) is consulted by the identifier transforms
 * (cc/iban/isbn/ean/vat/swift/aba/imei) for the 14 covered functions, plus
 * the i18n-name transforms (name_transliterate/name_script); everything
 * else stays pure-TS. Mirrors goldenmatch's `setScorerBackend(null)`
 * module-singleton pattern for test isolation.
 */

/**
 * A WASM-backed identifier kernel over the goldenflow-core validate/format/
 * mask/normalize functions (see `goldenflow-wasm/src/lib.rs`). Byte-identical
 * to the Python/native kernels by construction — this crate is a thin
 * wasm-bindgen shim over the SAME `goldenflow-core::identifiers` module.
 *
 * `string | undefined` mirrors the Rust `Option<String>` return of the
 * format/normalize/mask functions (wasm-bindgen maps `None` to `undefined`,
 * not `null`).
 */
export interface FlowWasmBackend {
  ccValidate(s: string): boolean;
  ccFormat(s: string): string | undefined;
  ccMask(s: string): string | undefined;
  ibanValidate(s: string): boolean;
  ibanFormat(s: string): string | undefined;
  isbnValidate(s: string): boolean;
  isbnNormalize(s: string): string | undefined;
  eanValidate(s: string): boolean;
  vatValidate(s: string): boolean;
  vatFormat(s: string): string | undefined;
  swiftValidate(s: string): boolean;
  swiftFormat(s: string): string | undefined;
  abaValidate(s: string): boolean;
  imeiValidate(s: string): boolean;
  nameTransliterate(s: string): string;
  nameScript(s: string): string;
}

import { createBackendRegistry } from "goldenmatch-wasm-runtime";

const _registry = createBackendRegistry<FlowWasmBackend>();

export function setFlowWasmBackend(b: FlowWasmBackend | null): void {
  _registry.set(b);
}

export function getFlowWasmBackend(): FlowWasmBackend | null {
  return _registry.get();
}
