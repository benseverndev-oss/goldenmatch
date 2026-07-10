/**
 * Fused columnar apply (Pillar-1 on the edge) — run a maximal run of owned no-arg
 * string transforms over a column in ONE JS<->WASM crossing, instead of the WASM
 * backend crossing the boundary once per value per transform.
 *
 * This is a WASM-path optimization ONLY: pure-TS applies transforms in-process
 * (no boundary to collapse), so `fusedEnabled()` is false without an active WASM
 * backend and the engine takes the per-transform path unchanged. `FUSABLE_KERNELS`
 * mirrors `goldenflow_core::chain::Kernel::ALL_NAMES` (the no-arg total string
 * kernels); a parity test asserts it equals the backend's `fusableKernelNames()`.
 */

import { getFlowWasmBackend } from "../wasm/backend.js";

/** No-arg, total (never-null) string kernels eligible for the fused chain.
 * Mirror of `goldenflow_core::chain::Kernel::ALL_NAMES`. Parameterized
 * (`truncate`/`pad`), numeric, and `Option`-returning (URL/company) transforms
 * are excluded — they take the per-transform path. */
export const FUSABLE_KERNELS: ReadonlySet<string> = new Set([
  "strip",
  "lowercase",
  "uppercase",
  "title_case",
  "fix_mojibake",
  "collapse_whitespace",
  "normalize_quotes",
  "normalize_line_endings",
  "normalize_unicode",
  "remove_html_tags",
  "remove_urls",
  "remove_digits",
  "remove_punctuation",
  "remove_emojis",
  "extract_numbers",
  "email_lowercase",
  "email_normalize",
  "email_canonical",
  "name_transliterate",
  "strip_titles",
  "strip_suffixes",
  "name_proper",
  "nickname_standardize",
  "name_initials",
  "strip_middle",
  "soundex",
  "double_metaphone_primary",
  "double_metaphone_alt",
]);

/** The fused chain is active only when a WASM backend that exposes `applyChain`
 * is registered (a 0.1.0 wheel without the fused export stays per-transform). */
export function fusedEnabled(): boolean {
  const b = getFlowWasmBackend();
  return b != null && typeof b.applyChain === "function";
}

/** An op fuses iff it's a no-arg kernel in the fusable set. */
export function isFusable(name: string, params: readonly string[]): boolean {
  return params.length === 0 && FUSABLE_KERNELS.has(name);
}
