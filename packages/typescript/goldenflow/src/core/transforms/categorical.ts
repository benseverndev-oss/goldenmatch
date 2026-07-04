/**
 * Categorical transforms — ported from goldenflow/transforms/categorical.py
 * Side-effect module: registers 5 categorical transforms on import.
 *
 * Owned-kernel family (D5 wave): boolean_normalize/gender_standardize/
 * null_standardize are byte-for-byte ports of the Python pure-TS reference
 * (`_boolean_normalize_py` et al. in `goldenflow/transforms/categorical.py`),
 * which is itself proven byte-identical to the Rust
 * `goldenflow-core::categorical` kernels (parity corpus in
 * `tests/parity/identifiers_corpus.jsonl`). Each dispatches to the opt-in
 * WASM backend (`FlowWasmBackend`) when `enableWasm()` has succeeded;
 * otherwise it runs the pure-TS implementation below. Pure-TS is the
 * default.
 *
 * `category_standardize`/`category_from_file` apply a caller-supplied
 * variant->canonical mapping -- that mapping is runtime DATA, not logic, so
 * it has no kernel; only the shared key-normalization step
 * (`categoryNormalizeKeyTs`, trim+lowercase) is native-first. The dict
 * lookup itself stays pure TS.
 */

import type { ColumnValue } from "../types.js";
import { registerTransform } from "./registry.js";
import { getFlowWasmBackend, type FlowWasmBackend } from "../wasm/backend.js";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Shared key-normalization step (trim+lowercase) -- pure-TS reference for
 * goldenflow-core's `categorical::category_normalize_key` kernel. Used
 * directly by boolean/gender/null below, and by the mapping-based
 * transforms to derive their runtime-data lookup key. */
function categoryNormalizeKeyTs(s: string): string {
  return s.trim().toLowerCase();
}

// ---------------------------------------------------------------------------
// boolean_normalize (series, boolean|string, 50)
//
// Pure-TS reference for goldenflow-core's `categorical::boolean_normalize`
// kernel.
// ---------------------------------------------------------------------------

const TRUTHY = new Set(["yes", "y", "1", "true", "t"]);
const FALSY = new Set(["no", "n", "0", "false", "f"]);

function booleanNormalizeTs(s: string): boolean | undefined {
  const key = categoryNormalizeKeyTs(s);
  if (TRUTHY.has(key)) return true;
  if (FALSY.has(key)) return false;
  return undefined;
}

function booleanNormalize(values: readonly ColumnValue[]): ColumnValue[] {
  const backend: FlowWasmBackend | null = getFlowWasmBackend();
  return values.map((v) => {
    if (v === null) return null;
    const s = String(v);
    const r = backend ? backend.booleanNormalize(s) : booleanNormalizeTs(s);
    return r === undefined ? null : r;
  });
}

registerTransform(
  { name: "boolean_normalize", inputTypes: ["boolean", "string"], priority: 50, mode: "series" },
  booleanNormalize,
);

// ---------------------------------------------------------------------------
// gender_standardize (series, string, 50)
//
// Pure-TS reference for goldenflow-core's `categorical::gender_standardize`
// kernel. Unrecognized values pass through UNCHANGED (the original string).
// ---------------------------------------------------------------------------

function genderStandardizeTs(s: string): string {
  const key = categoryNormalizeKeyTs(s);
  if (key === "male" || key === "m") return "M";
  if (key === "female" || key === "f") return "F";
  return s;
}

function genderStandardize(values: readonly ColumnValue[]): ColumnValue[] {
  const backend: FlowWasmBackend | null = getFlowWasmBackend();
  return values.map((v) => {
    if (v === null) return null;
    if (typeof v !== "string") return v;
    return backend ? backend.genderStandardize(v) : genderStandardizeTs(v);
  });
}

registerTransform(
  { name: "gender_standardize", inputTypes: ["string"], priority: 50, mode: "series" },
  genderStandardize,
);

// ---------------------------------------------------------------------------
// null_standardize (series, string, 80, auto_apply)
//
// Pure-TS reference for goldenflow-core's `categorical::null_standardize`
// kernel. Unrecognized values pass through UNCHANGED (the original string).
// ---------------------------------------------------------------------------

const NULL_VARIANTS = new Set([
  "n/a", "null", "none", "na", "nil", "nan", "-", "",
]);

function nullStandardizeTs(s: string): string | undefined {
  const key = categoryNormalizeKeyTs(s);
  if (NULL_VARIANTS.has(key)) return undefined;
  return s;
}

function nullStandardize(values: readonly ColumnValue[]): ColumnValue[] {
  const backend: FlowWasmBackend | null = getFlowWasmBackend();
  return values.map((v) => {
    if (v === null) return null;
    if (typeof v !== "string") return v;
    const r = backend ? backend.nullStandardize(v) : nullStandardizeTs(v);
    return r === undefined ? null : r;
  });
}

registerTransform(
  { name: "null_standardize", inputTypes: ["string"], autoApply: true, priority: 80, mode: "series" },
  nullStandardize,
);

// ---------------------------------------------------------------------------
// category_standardize (series, string, 45, param: mapping=null)
// Mapping format: { canonical: [variant1, variant2, ...], ... }
//
// The mapping is runtime DATA supplied by the caller, so the dict lookup
// stays pure TS; only the key-normalization step is native-first via
// `categoryNormalizeKeyTs`/the WASM backend's `categoryNormalizeKey`.
// ---------------------------------------------------------------------------

function categoryStandardize(
  values: readonly ColumnValue[],
  mapping: unknown = null,
): ColumnValue[] {
  if (!mapping || typeof mapping !== "object") return values.slice();

  // Build a reverse lookup: lowercase variant -> canonical
  const lookup = new Map<string, string>();
  for (const [canonical, variants] of Object.entries(
    mapping as Record<string, string[]>,
  )) {
    if (Array.isArray(variants)) {
      for (const variant of variants) {
        lookup.set(String(variant).toLowerCase(), canonical);
      }
    }
    // Also map the canonical itself
    lookup.set(canonical.toLowerCase(), canonical);
  }

  const backend: FlowWasmBackend | null = getFlowWasmBackend();
  return values.map((v) => {
    if (v === null) return null;
    if (typeof v !== "string") return v;
    const key = backend ? backend.categoryNormalizeKey(v) : categoryNormalizeKeyTs(v);
    return lookup.get(key) ?? v;
  });
}

registerTransform(
  { name: "category_standardize", inputTypes: ["string"], priority: 45, mode: "series" },
  categoryStandardize,
);

// ---------------------------------------------------------------------------
// category_from_file (series, string, 45, param: lookup_path=null)
// Stub: Node-only implementation later; returns input unchanged.
// ---------------------------------------------------------------------------

function categoryFromFile(
  values: readonly ColumnValue[],
  lookupPath: unknown = null,
): ColumnValue[] {
  if (lookupPath) {
    console.warn("[goldenflow] category_from_file is not yet implemented in the JS port — returning values unchanged");
  }
  return values.slice();
}

registerTransform(
  { name: "category_from_file", inputTypes: ["string"], priority: 45, mode: "series" },
  categoryFromFile,
);

// ---------------------------------------------------------------------------
// Pure-TS single-value exports (cross-surface byte-parity harness)
//
// Bypass the wasm-dispatch wrappers above so a parity test can assert the
// pure-TS path independently of whatever backend is currently registered.
// ---------------------------------------------------------------------------

export {
  categoryNormalizeKeyTs,
  booleanNormalizeTs,
  genderStandardizeTs,
  nullStandardizeTs,
};
