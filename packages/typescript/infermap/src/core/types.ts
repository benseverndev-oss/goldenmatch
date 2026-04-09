// Core data types. Mirrors infermap/types.py.
// Uses interfaces + factory functions instead of classes so results are plain
// JSON-serializable — important for parity tests and Next.js Server → Client payloads.

export const VALID_DTYPES = [
  "string",
  "integer",
  "float",
  "boolean",
  "date",
  "datetime",
] as const;

export type Dtype = (typeof VALID_DTYPES)[number];

export interface FieldInfo {
  name: string;
  dtype: Dtype;
  sampleValues: string[];
  nullRate: number;
  uniqueRate: number;
  valueCount: number;
  metadata: Record<string, unknown>;
}

export interface SchemaInfo {
  fields: FieldInfo[];
  sourceName: string;
  requiredFields: string[];
}

export interface ScorerResult {
  score: number; // clamped to [0, 1]
  reasoning: string;
}

export interface FieldMapping {
  source: string;
  target: string;
  confidence: number;
  breakdown: Record<string, ScorerResult>;
  reasoning: string;
}

export interface MapResult {
  mappings: FieldMapping[];
  unmappedSource: string[];
  unmappedTarget: string[];
  warnings: string[];
  metadata: Record<string, unknown>;
  scoreMatrix?: Record<string, Record<string, number>>;
}

// ---------- factories + normalization ----------

const isValidDtype = (d: unknown): d is Dtype =>
  typeof d === "string" && (VALID_DTYPES as readonly string[]).includes(d);

export function makeFieldInfo(input: {
  name: string;
  dtype?: string;
  sampleValues?: string[];
  nullRate?: number;
  uniqueRate?: number;
  valueCount?: number;
  metadata?: Record<string, unknown>;
}): FieldInfo {
  return {
    name: input.name,
    dtype: isValidDtype(input.dtype) ? input.dtype : "string",
    sampleValues: input.sampleValues ?? [],
    nullRate: input.nullRate ?? 0,
    uniqueRate: input.uniqueRate ?? 0,
    valueCount: input.valueCount ?? 0,
    metadata: input.metadata ?? {},
  };
}

export function makeSchemaInfo(input: {
  fields: FieldInfo[];
  sourceName?: string;
  requiredFields?: string[];
}): SchemaInfo {
  return {
    fields: input.fields,
    sourceName: input.sourceName ?? "",
    requiredFields: input.requiredFields ?? [],
  };
}

export function clampScore(score: number): number {
  if (Number.isNaN(score)) return 0;
  return Math.max(0, Math.min(1, score));
}

export function makeScorerResult(score: number, reasoning: string): ScorerResult {
  return { score: clampScore(score), reasoning };
}

// ---------- MapResult helpers (mirror Python .report() / .to_json()) ----------

const round = (n: number, digits: number): number => {
  const factor = 10 ** digits;
  return Math.round(n * factor) / factor;
};

export interface MapResultReport {
  mappings: Array<{
    source: string;
    target: string;
    confidence: number;
    breakdown: Record<string, { score: number; reasoning: string }>;
    reasoning: string;
  }>;
  unmapped_source: string[];
  unmapped_target: string[];
  warnings: string[];
}

export function mapResultToReport(result: MapResult): MapResultReport {
  return {
    mappings: result.mappings.map((m) => ({
      source: m.source,
      target: m.target,
      confidence: round(m.confidence, 3),
      breakdown: Object.fromEntries(
        Object.entries(m.breakdown).map(([k, r]) => [
          k,
          { score: round(r.score, 3), reasoning: r.reasoning },
        ])
      ),
      reasoning: m.reasoning,
    })),
    unmapped_source: result.unmappedSource,
    unmapped_target: result.unmappedTarget,
    warnings: result.warnings,
  };
}

export function mapResultToJson(result: MapResult): string {
  return JSON.stringify(mapResultToReport(result), null, 2);
}
