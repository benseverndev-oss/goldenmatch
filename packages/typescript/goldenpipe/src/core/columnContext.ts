/**
 * Column context — shared column metadata flowing between pipeline stages.
 * Port of goldenpipe/models/column_context.py.
 *
 * Built by GoldenCheck (scan), enriched by GoldenFlow (transform), consumed by
 * GoldenMatch (auto-config) to avoid re-profiling.
 *
 * Edge-safe: no `node:` imports.
 */

import type { Row } from "./models.js";

// ---------------------------------------------------------------------------
// Enums (string-literal unions in TS)
// ---------------------------------------------------------------------------

export const ColumnType = {
  NAME: "name",
  EMAIL: "email",
  PHONE: "phone",
  DATE: "date",
  GEO: "geo",
  ADDRESS: "address",
  ZIP: "zip",
  IDENTIFIER: "identifier",
  NUMERIC: "numeric",
  STRING: "string",
  DESCRIPTION: "description",
} as const;
export type ColumnType = (typeof ColumnType)[keyof typeof ColumnType];

export const CardinalityBand = {
  UNSET: "",
  LOW: "low",
  MID: "mid",
  HIGH: "high",
  SKIP: "skip",
} as const;
export type CardinalityBand = (typeof CardinalityBand)[keyof typeof CardinalityBand];

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** Floor — below this, the signal is too weak to act on. */
export const MIN_CONFIDENCE = 0.3;

/** Identifier types — columns that identify an entity (used for matching). */
const IDENTIFIER_TYPES: ReadonlySet<ColumnType> = new Set([
  ColumnType.NAME,
  ColumnType.EMAIL,
  ColumnType.PHONE,
]);

/** Types that should never be identifiers regardless of cardinality. */
const NEVER_IDENTIFIER_TYPES: ReadonlySet<ColumnType> = new Set([
  ColumnType.DATE,
  ColumnType.NUMERIC,
  ColumnType.IDENTIFIER,
]);

// ---------------------------------------------------------------------------
// ColumnContext
// ---------------------------------------------------------------------------

export interface ColumnContext {
  name: string;
  inferredType: ColumnType;
  nullRate: number;
  cardinality: number;
  isIdentifier: boolean;
  transformsApplied: string[];
  findings: string[];
  confidence: number;
  cardinalityBand: CardinalityBand;
}

/** Construct a ColumnContext, validating invariants (port of `__post_init__`). */
export function makeColumnContext(
  input: Partial<ColumnContext> & { name: string },
): ColumnContext {
  const ctx: ColumnContext = {
    name: input.name,
    inferredType: input.inferredType ?? ColumnType.STRING,
    nullRate: input.nullRate ?? 0.0,
    cardinality: input.cardinality ?? 0,
    isIdentifier: input.isIdentifier ?? false,
    transformsApplied: input.transformsApplied ?? [],
    findings: input.findings ?? [],
    confidence: input.confidence ?? 0.5,
    cardinalityBand: input.cardinalityBand ?? CardinalityBand.UNSET,
  };
  if (!ctx.name) {
    throw new Error("ColumnContext.name must be non-empty");
  }
  if (!(ctx.nullRate >= 0.0 && ctx.nullRate <= 1.0)) {
    throw new Error(`nullRate must be in [0, 1], got ${ctx.nullRate}`);
  }
  if (ctx.cardinality < 0) {
    throw new Error(`cardinality must be >= 0, got ${ctx.cardinality}`);
  }
  if (!(ctx.confidence >= 0.0 && ctx.confidence <= 1.0)) {
    throw new Error(`confidence must be in [0, 1], got ${ctx.confidence}`);
  }
  return ctx;
}

// ---------------------------------------------------------------------------
// Name-based classification (regex heuristics)
// ---------------------------------------------------------------------------

const NAME_PATTERNS =
  /(^name$|first.?name|last.?name|full.?name|fname|lname|surname|given.?name|middle)/i;
const EMAIL_PATTERNS = /(email|e.?mail|email.?addr)/i;
const PHONE_PATTERNS = /(phone|tel|mobile|fax|cell)/i;
const ZIP_PATTERNS = /(zip|postal|postcode|zip.?code)/i;
const ADDRESS_PATTERNS = /(address|street|addr|line.?1|line.?2)/i;
const GEO_PATTERNS = /(city|^state$|state.?cd|^country$|province|region|county)/i;
const DATE_PATTERNS = /(date|_dt$|_date$|registr|created|updated|birth.?d|dob)/i;
const ID_PATTERNS = /(^id$|^key$|^code$|^sku$|_id$|_key$)/i;

/** Classify a column by name pattern matching. Returns null when no match. */
export function classifyByName(colName: string): ColumnType | null {
  if (DATE_PATTERNS.test(colName)) return ColumnType.DATE;
  if (EMAIL_PATTERNS.test(colName)) return ColumnType.EMAIL;
  if (ZIP_PATTERNS.test(colName)) return ColumnType.ZIP;
  if (GEO_PATTERNS.test(colName)) return ColumnType.GEO;
  if (ADDRESS_PATTERNS.test(colName)) return ColumnType.ADDRESS;
  if (PHONE_PATTERNS.test(colName)) return ColumnType.PHONE;
  if (NAME_PATTERNS.test(colName)) return ColumnType.NAME;
  if (ID_PATTERNS.test(colName)) return ColumnType.IDENTIFIER;
  return null;
}

/** Map a profiler dtype string to a ColumnType. */
export function normalizeDtype(rawType: string): ColumnType {
  const t = rawType.toLowerCase().trim();
  if (t.includes("int") || t.includes("float")) return ColumnType.NUMERIC;
  if (t.includes("date") || t.includes("time")) return ColumnType.DATE;
  if (t.includes("bool")) return ColumnType.STRING;
  return ColumnType.STRING;
}

// ---------------------------------------------------------------------------
// Cardinality IQR banding
// ---------------------------------------------------------------------------

/** Classify each column's cardinality as low/mid/high using IQR quartiles. */
function computeCardinalityBands(contexts: ColumnContext[]): void {
  const stringContexts = contexts.filter(
    (c) => c.inferredType !== ColumnType.NUMERIC && c.inferredType !== ColumnType.DATE,
  );
  if (stringContexts.length < 3) {
    return;
  }

  const cardinalities = stringContexts.map((c) => c.cardinality).sort((a, b) => a - b);
  const n = cardinalities.length;
  // Python uses integer-division indices: q1 = card[n // 4], q3 = card[3 * n // 4].
  const q1 = cardinalities[Math.floor(n / 4)]!;
  const q3 = cardinalities[Math.floor((3 * n) / 4)]!;

  for (const ctx of contexts) {
    if (ctx.inferredType === ColumnType.NUMERIC || ctx.inferredType === ColumnType.DATE) {
      ctx.cardinalityBand = CardinalityBand.SKIP;
      continue;
    }
    if (ctx.cardinality <= q1) {
      ctx.cardinalityBand = CardinalityBand.LOW;
    } else if (ctx.cardinality >= q3) {
      ctx.cardinalityBand = CardinalityBand.HIGH;
    } else {
      ctx.cardinalityBand = CardinalityBand.MID;
    }
  }
}

/** Refine `isIdentifier` using cardinality band as a second signal. */
function applyCardinalitySignal(contexts: ColumnContext[]): void {
  for (const ctx of contexts) {
    if (NEVER_IDENTIFIER_TYPES.has(ctx.inferredType)) {
      ctx.isIdentifier = false;
      continue;
    }

    const hasNameSignal = IDENTIFIER_TYPES.has(ctx.inferredType);
    const band = ctx.cardinalityBand;

    if (hasNameSignal && band === CardinalityBand.MID) {
      ctx.isIdentifier = true;
      ctx.confidence = Math.min(ctx.confidence + 0.15, 1.0);
    } else if (hasNameSignal && band === CardinalityBand.LOW) {
      ctx.isIdentifier = false;
      ctx.confidence = Math.max(ctx.confidence - 0.2, MIN_CONFIDENCE);
    } else if (hasNameSignal && band === CardinalityBand.HIGH) {
      ctx.isIdentifier = true;
      ctx.confidence = Math.min(ctx.confidence + 0.05, 1.0);
    } else if (!hasNameSignal && band === CardinalityBand.MID) {
      if (ctx.inferredType === ColumnType.STRING) {
        ctx.isIdentifier = true;
        ctx.confidence = 0.5;
      }
    } else if (!hasNameSignal && band === CardinalityBand.LOW) {
      ctx.isIdentifier = false;
    } else if (!hasNameSignal && band === CardinalityBand.HIGH) {
      ctx.isIdentifier = false;
    }

    if (ctx.nullRate > 0.3 && ctx.isIdentifier) {
      ctx.confidence = Math.max(ctx.confidence - 0.1, MIN_CONFIDENCE);
    }
  }
}

// ---------------------------------------------------------------------------
// Context builders
// ---------------------------------------------------------------------------

/** Minimal shape of a GoldenCheck ColumnProfile that we consume. */
export interface ColumnProfileLike {
  name: string;
  inferredType?: string;
  nullPct?: number;
  uniqueCount?: number;
}

/** Minimal shape of a GoldenCheck finding that we consume. */
export interface FindingLike {
  column?: string;
  check?: string;
  message?: string;
}

/**
 * Build ColumnContexts from GoldenCheck scan results. Combines three signals:
 * 1. Column-name heuristics (regex patterns).
 * 2. Profile data (null rate, cardinality, dtype).
 * 3. Cardinality IQR bands.
 */
export function buildContextsFromCheck(
  findings: readonly FindingLike[],
  columnProfiles: readonly ColumnProfileLike[] | null | undefined,
): ColumnContext[] {
  if (!columnProfiles || columnProfiles.length === 0) {
    return [];
  }

  const contexts = new Map<string, ColumnContext>();
  for (const cp of columnProfiles) {
    let semanticType = classifyByName(cp.name);
    if (!semanticType) {
      semanticType = normalizeDtype(cp.inferredType ?? "string");
    }
    const ctx = makeColumnContext({
      name: cp.name,
      inferredType: semanticType,
      nullRate: cp.nullPct ?? 0.0,
      cardinality: cp.uniqueCount ?? 0,
      isIdentifier: IDENTIFIER_TYPES.has(semanticType),
      confidence: semanticType !== ColumnType.STRING ? 0.8 : 0.4,
    });
    contexts.set(cp.name, ctx);
  }

  const contextList = [...contexts.values()];
  computeCardinalityBands(contextList);
  applyCardinalitySignal(contextList);

  for (const f of findings) {
    const colName = f.column;
    if (!colName || !contexts.has(colName)) continue;
    const ctx = contexts.get(colName)!;
    const check = f.check ?? "";
    const message = String(f.message ?? "").slice(0, 80);
    ctx.findings.push(`${check}: ${message}`);
  }

  return contextList;
}

/** Minimal shape of a GoldenFlow manifest record we consume. */
export interface ManifestRecordLike {
  column?: string;
  transform?: string;
  affectedRows?: number;
}

/** Enrich ColumnContexts with GoldenFlow transform information. */
export function enrichContextsFromFlow(
  contexts: ColumnContext[],
  records: readonly ManifestRecordLike[] | null | undefined,
): void {
  if (!records) return;
  const lookup = new Map(contexts.map((c) => [c.name, c]));

  for (const record of records) {
    const colName = record.column;
    const transform = record.transform;
    const affected = record.affectedRows ?? 0;
    if (!colName || !lookup.has(colName)) continue;
    const ctx = lookup.get(colName)!;
    if (affected > 0 && transform) {
      ctx.transformsApplied.push(transform);
    }
    if (transform && transform.toLowerCase().includes("date")) {
      ctx.inferredType = ColumnType.DATE;
      ctx.isIdentifier = false;
      ctx.confidence = 0.95;
    }
  }
}

// ---------------------------------------------------------------------------
// Cardinality helper used by the match adapter (counts distinct non-null vals)
// ---------------------------------------------------------------------------

export function distinctNonNull(rows: readonly Row[], col: string): number {
  const seen = new Set<unknown>();
  for (const row of rows) {
    const v = row[col];
    if (v === null || v === undefined || v === "") continue;
    seen.add(v);
  }
  return seen.size;
}

export function nullRateOf(rows: readonly Row[], col: string): number {
  if (rows.length === 0) return 1.0;
  let nulls = 0;
  for (const row of rows) {
    const v = row[col];
    if (v === null || v === undefined || v === "") nulls += 1;
  }
  return nulls / rows.length;
}
