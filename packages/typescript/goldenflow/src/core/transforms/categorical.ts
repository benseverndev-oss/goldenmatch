/**
 * Categorical transforms — ported from goldenflow/transforms/categorical.py
 * Side-effect module: registers 5 categorical transforms on import.
 */

import type { ColumnValue } from "../types.js";
import { registerTransform } from "./registry.js";

// ---------------------------------------------------------------------------
// boolean_normalize (series, boolean|string, 50)
// ---------------------------------------------------------------------------

const TRUTHY = new Set(["yes", "y", "1", "true", "t"]);
const FALSY = new Set(["no", "n", "0", "false", "f"]);

function booleanNormalize(values: readonly ColumnValue[]): ColumnValue[] {
  return values.map((v) => {
    if (v === null) return null;
    const s = String(v).trim().toLowerCase();
    if (TRUTHY.has(s)) return true;
    if (FALSY.has(s)) return false;
    return v;
  });
}

registerTransform(
  { name: "boolean_normalize", inputTypes: ["boolean", "string"], priority: 50, mode: "series" },
  booleanNormalize,
);

// ---------------------------------------------------------------------------
// gender_standardize (series, string, 50)
// ---------------------------------------------------------------------------

function genderStandardize(values: readonly ColumnValue[]): ColumnValue[] {
  return values.map((v) => {
    if (v === null) return null;
    if (typeof v !== "string") return v;
    const s = v.trim().toLowerCase();
    if (s === "male" || s === "m") return "M";
    if (s === "female" || s === "f") return "F";
    return v;
  });
}

registerTransform(
  { name: "gender_standardize", inputTypes: ["string"], priority: 50, mode: "series" },
  genderStandardize,
);

// ---------------------------------------------------------------------------
// null_standardize (series, string, 80, auto_apply)
// ---------------------------------------------------------------------------

const NULL_VARIANTS = new Set([
  "n/a", "null", "none", "na", "nil", "nan", "-", "",
]);

function nullStandardize(values: readonly ColumnValue[]): ColumnValue[] {
  return values.map((v) => {
    if (v === null) return null;
    if (typeof v !== "string") return v;
    const s = v.trim().toLowerCase();
    if (NULL_VARIANTS.has(s)) return null;
    return v;
  });
}

registerTransform(
  { name: "null_standardize", inputTypes: ["string"], autoApply: true, priority: 80, mode: "series" },
  nullStandardize,
);

// ---------------------------------------------------------------------------
// category_standardize (series, string, 45, param: mapping=null)
// Mapping format: { canonical: [variant1, variant2, ...], ... }
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

  return values.map((v) => {
    if (v === null) return null;
    if (typeof v !== "string") return v;
    const key = v.trim().toLowerCase();
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
