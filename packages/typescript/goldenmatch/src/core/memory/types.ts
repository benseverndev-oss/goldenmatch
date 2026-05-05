/**
 * memory/types.ts -- Learning Memory v0.4.0 type definitions.
 *
 * Edge-safe: no `node:` imports.
 *
 * Mirrors the Python source-of-truth at
 * packages/python/goldenmatch/goldenmatch/core/memory/store.py. TS interfaces
 * use camelCase; the JSON wire format (correctionToJSON / correctionFromJSON)
 * uses snake_case to match Python.
 */

// ---------------------------------------------------------------------------
// Source / decision literal unions (StrEnum-equivalent)
// ---------------------------------------------------------------------------

/**
 * Origin of a correction. High-trust sources (steward, boost, unmerge) map to
 * trust 1.0; everything else maps to 0.5. See `trustForSource`.
 */
export type CorrectionSource =
  | "steward"
  | "boost"
  | "unmerge"
  | "agent"
  | "llm"
  | "api";

/** Canonical correction decisions. Mirrors Python's `Decision` StrEnum. */
export type Decision = "approve" | "reject";

/**
 * Sources that confer human-level trust. Pair decisions originating here are
 * weighted with `trust = 1.0` so the learner cannot be drowned out by
 * lower-trust agent / llm / api signals.
 */
export const HIGH_TRUST_SOURCES: ReadonlySet<CorrectionSource> = new Set<CorrectionSource>([
  "steward",
  "boost",
  "unmerge",
]);

/**
 * Return 1.0 for human-trust sources (steward / boost / unmerge), 0.5 else.
 *
 * Centralizes the trust mapping so call sites cannot drift. Accepts a raw
 * string for callers that haven't narrowed their value yet; unknown values
 * fall back to the agent-tier 0.5 trust.
 */
export function trustForSource(source: CorrectionSource | string): number {
  return HIGH_TRUST_SOURCES.has(source as CorrectionSource) ? 1.0 : 0.5;
}

// ---------------------------------------------------------------------------
// Correction
// ---------------------------------------------------------------------------

/**
 * A single pair decision stored in memory.
 *
 * `idA <= idB` is canonicalized at insertion time by the store. `fieldHash` is
 * SHA-256[:16] of the concatenated matchkey field values; `recordHash` is
 * `<recordHashA>:<recordHashB>` with `__row_id__` excluded so corrections
 * survive row reordering across runs.
 */
export interface Correction {
  readonly id: string;
  readonly idA: number;
  readonly idB: number;
  readonly decision: Decision;
  readonly source: CorrectionSource;
  readonly trust: number;
  readonly fieldHash: string;
  readonly recordHash: string;
  readonly originalScore: number;
  readonly matchkeyName: string | null;
  readonly reason: string | null;
  readonly dataset: string | null;
  readonly createdAt: Date;
}

// ---------------------------------------------------------------------------
// LearnedAdjustment
// ---------------------------------------------------------------------------

/** Output of the rule learner. `fieldWeights` stays null in v0.4.0 (stub). */
export interface LearnedAdjustment {
  readonly matchkeyName: string;
  readonly threshold: number | null;
  readonly fieldWeights: Record<string, number> | null;
  readonly sampleSize: number;
  readonly learnedAt: Date;
}

// ---------------------------------------------------------------------------
// CorrectionStats
// ---------------------------------------------------------------------------

/**
 * Outcome of `applyCorrections`. `failed` / `error` are populated only when
 * `applyCorrections` itself crashed; postflight uses the sentinel to surface
 * "Memory: FAILED -- see logs".
 */
export interface CorrectionStats {
  readonly applied: number;
  readonly stale: number;
  readonly staleAmbiguous: number;
  readonly staleUnanchorable: number;
  readonly stalePairs: ReadonlyArray<readonly [number, number]>;
  readonly totalPairs: number;
  readonly failed?: boolean;
  readonly error?: string;
}

// ---------------------------------------------------------------------------
// MemoryStore interface
// ---------------------------------------------------------------------------

/**
 * Backend-agnostic persistence interface. All methods async so InMemoryStore
 * (which calls async hash routines during apply) and SqliteMemoryStore (sync
 * better-sqlite3 internally) are interchangeable to callers.
 */
export interface MemoryStore {
  addCorrection(c: Correction): Promise<void>;
  getCorrection(
    idA: number,
    idB: number,
    dataset: string | null,
  ): Promise<Correction | null>;
  getCorrections(opts?: { dataset?: string | null }): Promise<Correction[]>;
  countCorrections(dataset?: string | null): Promise<number>;
  correctionsSince(since: Date): Promise<Correction[]>;
  saveAdjustment(a: LearnedAdjustment): Promise<void>;
  getAdjustment(matchkeyName: string): Promise<LearnedAdjustment | null>;
  getAllAdjustments(): Promise<LearnedAdjustment[]>;
  lastLearnTime(): Promise<Date | null>;
  close?(): Promise<void>;
}

// ---------------------------------------------------------------------------
// JSON wire format (snake_case, matches Python)
// ---------------------------------------------------------------------------

/** Snake_case JSON wire shape. ISO-8601 UTC timestamp. */
export interface CorrectionJSON {
  readonly id: string;
  readonly id_a: number;
  readonly id_b: number;
  readonly decision: Decision;
  readonly source: CorrectionSource;
  readonly trust: number;
  readonly field_hash: string;
  readonly record_hash: string;
  readonly original_score: number;
  readonly matchkey_name: string | null;
  readonly reason: string | null;
  readonly dataset: string | null;
  readonly created_at: string;
}

function toIsoUtc(d: Date): string {
  // Match Python's `.isoformat().replace("+00:00", "Z")` shape: drop a trailing
  // ".000" milliseconds segment when sub-second precision is zero so the JSON
  // matches Python's default output for whole-second timestamps. JS Dates
  // store ms precision only, so this is the lone normalization needed.
  const iso = d.toISOString(); // "YYYY-MM-DDTHH:mm:ss.sssZ"
  return iso.replace(/\.000Z$/, "Z");
}

/** Serialize a Correction to the cross-language JSON wire format. */
export function correctionToJSON(c: Correction): CorrectionJSON {
  return {
    id: c.id,
    id_a: c.idA,
    id_b: c.idB,
    decision: c.decision,
    source: c.source,
    trust: c.trust,
    field_hash: c.fieldHash,
    record_hash: c.recordHash,
    original_score: c.originalScore,
    matchkey_name: c.matchkeyName,
    reason: c.reason,
    dataset: c.dataset,
    created_at: toIsoUtc(c.createdAt),
  };
}

/** Parse a CorrectionJSON back into a Correction. Inverse of correctionToJSON. */
export function correctionFromJSON(j: CorrectionJSON): Correction {
  return {
    id: j.id,
    idA: j.id_a,
    idB: j.id_b,
    decision: j.decision,
    source: j.source,
    trust: j.trust,
    fieldHash: j.field_hash,
    recordHash: j.record_hash,
    originalScore: j.original_score,
    matchkeyName: j.matchkey_name,
    reason: j.reason,
    dataset: j.dataset,
    createdAt: new Date(j.created_at),
  };
}

// ---------------------------------------------------------------------------
// Re-exports of caller-facing config types (defined in core/types.ts)
// ---------------------------------------------------------------------------

export type { MemoryConfig, LearningConfig } from "../types.js";
