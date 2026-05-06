// Canonical field-type definitions shared across the Golden Suite.
//
// Wire-format contract — these interfaces ship across package boundaries
// (InferMap → GoldenCheck → GoldenPipe) and across language boundaries
// (Python ↔ TypeScript). Renaming a field or changing a default is a
// breaking change. SCHEMA_VERSION lets consumers detect mismatches at
// runtime if the wire shape ever has to evolve.
//
// Field naming is snake_case (not the workspace's usual camelCase) because
// these structures pass through YAML on the producer side and JSON wire on
// the consumer side without remapping. The Python sibling at
// `packages/python/goldencheck-types/goldencheck_types/types.py` uses the
// same names; cross-language parity here is more valuable than language-
// idiomatic case style. See `packages/typescript/CLAUDE.md`.

/** Canonical "no mapping found" sentinel for `FieldMapping.type`.
 *  Use `isUnknown(m)` to test rather than comparing the string directly. */
export const UNMAPPED_TYPE = "unknown" as const;

/** Wire-format version embedded in `InferredSchema`. Bump on any
 *  backwards-incompatible change to the shape.
 *
 *  v2 (2026-05-06): `FieldSpec` gained `name` so the canonical identifier
 *  travels with the spec instead of only as a dict key. */
export const SCHEMA_VERSION = 2 as const;

export interface FieldSpec {
  /** Canonical identifier — matches the key under `DomainPack.types`.
   *  The loader populates from the dict key and raises if a YAML
   *  explicitly sets a different name. */
  readonly name: string;
  readonly name_hints: string[];
  readonly value_signals: Record<string, unknown>;
  readonly suppress: string[];
  readonly confidence_threshold?: number;
  readonly description?: string;
}

export interface DomainPack {
  readonly name: string;
  readonly description: string;
  readonly types: Record<string, FieldSpec>;
}

export interface FieldMapping {
  readonly source_col: string;
  readonly canonical: string | null;
  /** Canonical type name, or UNMAPPED_TYPE for "unknown". */
  readonly type: string;
  readonly confidence: number;
  /** InferMap-internal; do not depend on shape. */
  readonly evidence: Record<string, unknown>;
}

export interface InferredSchema {
  readonly domain: string;
  readonly fields: Record<string, FieldMapping>;
  readonly confidence: number;
  readonly schema_version?: number;
}

export const isUnknown = (m: FieldMapping): boolean => m.type === UNMAPPED_TYPE;

export const unmappedCols = (s: InferredSchema): string[] =>
  Object.entries(s.fields)
    .filter(([, m]) => isUnknown(m))
    .map(([k]) => k);

/** Reason field on `DetectionResult`. */
export type DetectionReason =
  | "confident"
  | "tie"
  | "below_min_score"
  | "no_data";

/** Rich auto-detection result.
 *
 *  Use `detectDomainDetailed` (returns this) when you want the runner-up,
 *  score, or to distinguish "tied" from "no match". The thin
 *  `detectDomain` wrapper returns just `.domain` for callers that only
 *  care about the picked name. */
export interface DetectionResult {
  readonly domain: string | null;
  readonly score: number;
  readonly runner_up: string | null;
  readonly runner_up_score: number;
  readonly reason: DetectionReason;
}
