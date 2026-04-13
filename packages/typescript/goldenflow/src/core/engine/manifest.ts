/**
 * Manifest — tracks transforms applied, errors, and before/after samples.
 * Re-exports MutableManifest from types for convenience.
 */

export { MutableManifest, makeManifest, makeTransformRecord } from "../types.js";
export type { Manifest, TransformRecord, TransformError } from "../types.js";
