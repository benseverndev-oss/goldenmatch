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
 *  travels with the spec instead of only as a dict key.
 *  v3 (2026-06-17): DomainPack gained optional groups (FieldGroupSpec list).
 *  v4 (2026-08-14): DomainPack gained optional roles (RoleSpec map), and the
 *  identity-layer shapes (IdentityLayer / LayerDetectionResult) joined the
 *  wire contract so layers travel InferMap -> GoldenPipe -> GoldenMatch. */
export const SCHEMA_VERSION = 4 as const;

/** The **closed** set of entity kinds an identity layer may carry.
 *
 *  Closed on purpose: `kind` is the axis downstream matching behaviour keys
 *  off (you match people differently from machines), so an open set would make
 *  consumer behaviour unpredictable. `role` is the open, pack-extensible axis. */
export const IDENTITY_KINDS = [
  "person",
  "organization",
  "asset",
  "place",
  "unknown",
] as const;

export type IdentityKind = (typeof IDENTITY_KINDS)[number];

/** Canonical "party present but not recognised" sentinel for
 *  `IdentityLayer.role`. Such a layer still carries its columns, kind and
 *  evidence — honest refusal, not a drop. */
export const UNKNOWN_ROLE = "unknown" as const;

/** Why a layer was proposed. Mirrors the vocabulary shape of
 *  `DetectionResult.reason`. */
export type LayerReason =
  | "affix"
  | "role_hint"
  | "affix+role_hint"
  | "singleton"
  | "low_confidence";

export const LAYER_REASONS: readonly LayerReason[] = [
  "affix",
  "role_hint",
  "affix+role_hint",
  "singleton",
  "low_confidence",
] as const;

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

/** A set of canonical fields that survive golden-record merge together.
 *
 *  `members` are canonical field names (matching keys in DomainPack.types).
 *  Consumed by goldenmatch survivorship to promote correlated columns
 *  (address, person name, contact) from one winning source record. */
export interface FieldGroupSpec {
  readonly name: string;
  readonly members: string[];
  readonly category: string | null;
  /** Survivorship strategy for the group. Defaults to "most_complete" on the Python side. */
  readonly default_strategy: string;
  readonly date_hint: string | null;
}

/** One entity **role** a domain pack declares — a party a record refers to.
 *
 *  Distinct from `FieldSpec`, which describes a *field type*
 *  (`account_number`). A role describes a *party* (`lender`, `borrower`,
 *  `payor`) that several fields collectively identify. Field types answer
 *  "what is this column"; roles answer "whose is it".
 *
 *  `typical_types` is **corroboration, never a requirement** — its presence
 *  raises a detected layer's confidence, its absence never vetoes one. */
export interface RoleSpec {
  /** Canonical role identifier — matches the key under `DomainPack.roles`. */
  readonly name: string;
  readonly kind: IdentityKind;
  readonly name_hints: string[];
  readonly typical_types: string[];
  readonly description?: string;
}

export interface DomainPack {
  readonly name: string;
  readonly description: string;
  readonly types: Record<string, FieldSpec>;
  readonly groups: FieldGroupSpec[];
  readonly roles: Record<string, RoleSpec>;
}

/** One party a dataset refers to, and the columns that describe it.
 *
 *  An identity layer is a **group of columns describing one party** — not a
 *  per-column label. `lender_name`/`lender_id` are one layer;
 *  `borrower_name`/`borrower_ssn` are another. Framing it as column-grouping
 *  (rather than column-classification) is what keeps layer detection out of
 *  InferMap's deliberately 1:1 assignment engine: one role spans many columns,
 *  which the 1:1 model cannot express.
 *
 *  `evidence` is InferMap-internal; do not depend on its shape. */
export interface IdentityLayer {
  /** `UNKNOWN_ROLE` when a party is present but not recognised. */
  readonly role: string;
  readonly kind: IdentityKind;
  readonly columns: string[];
  readonly score: number;
  readonly reason: LayerReason;
  readonly evidence: Record<string, unknown>;
}

/** Result of identity-layer detection over one frame.
 *
 *  `domain` is the vertical from the existing `detectDomain` path, carried
 *  through unchanged for context. A single-entity dataset yields exactly one
 *  layer; columns belonging to no layer land in `unassigned` rather than being
 *  forced into the nearest one. */
export interface LayerDetectionResult {
  readonly layers: IdentityLayer[];
  readonly unassigned: string[];
  readonly domain: string | null;
  readonly schema_version?: number;
}

export const isUnknownRole = (l: IdentityLayer): boolean =>
  l.role === UNKNOWN_ROLE;

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
