/**
 * Core types — mirrors goldencheck/models/finding.py + profile.py + config/schema.py.
 * Edge-safe: no Node.js dependencies.
 */

// --- Severity ---

export const Severity = {
  INFO: 1,
  WARNING: 2,
  ERROR: 3,
} as const;

export type Severity = (typeof Severity)[keyof typeof Severity];

export function severityLabel(s: Severity): string {
  switch (s) {
    case Severity.ERROR:
      return "ERROR";
    case Severity.WARNING:
      return "WARNING";
    case Severity.INFO:
      return "INFO";
  }
}

// --- Finding ---

export interface Finding {
  readonly severity: Severity;
  readonly column: string;
  readonly check: string;
  readonly message: string;
  readonly affectedRows: number;
  readonly sampleValues: readonly string[];
  readonly suggestion: string | null;
  readonly pinned: boolean;
  readonly source: string | null; // null = profiler, "llm", "baseline_drift"
  readonly confidence: number; // 0.0 – 1.0
  readonly metadata: Readonly<Record<string, unknown>>;
}

export type FindingInput = Pick<Finding, "severity" | "column" | "check" | "message"> &
  Partial<Omit<Finding, "severity" | "column" | "check" | "message">>;

/** Create a Finding with sensible defaults (mirrors Python dataclass defaults). */
export function makeFinding(input: FindingInput): Finding {
  return {
    affectedRows: 0,
    sampleValues: [],
    suggestion: null,
    pinned: false,
    source: null,
    confidence: 1.0,
    metadata: {},
    ...input,
  };
}

/** Immutable update — returns a new Finding with the given overrides. */
export function replaceFinding(f: Finding, overrides: Partial<Finding>): Finding {
  return { ...f, ...overrides };
}

// --- Column Profile ---

export interface ColumnProfile {
  readonly name: string;
  readonly inferredType: string;
  readonly nullCount: number;
  readonly nullPct: number;
  readonly uniqueCount: number;
  readonly uniquePct: number;
  readonly rowCount: number;
  readonly minValue: string | null;
  readonly maxValue: string | null;
  readonly mean: number | null;
  readonly stddev: number | null;
  readonly topValues: ReadonlyArray<readonly [string, number]>;
  readonly detectedFormat: string | null;
  readonly detectedPatterns: ReadonlyArray<readonly [string, number]>;
  readonly enumValues: readonly string[] | null;
}

export type ColumnProfileInput = Pick<
  ColumnProfile,
  "name" | "inferredType" | "nullCount" | "nullPct" | "uniqueCount" | "uniquePct" | "rowCount"
> &
  Partial<
    Omit<
      ColumnProfile,
      "name" | "inferredType" | "nullCount" | "nullPct" | "uniqueCount" | "uniquePct" | "rowCount"
    >
  >;

export function makeColumnProfile(input: ColumnProfileInput): ColumnProfile {
  return {
    minValue: null,
    maxValue: null,
    mean: null,
    stddev: null,
    topValues: [],
    detectedFormat: null,
    detectedPatterns: [],
    enumValues: null,
    ...input,
  };
}

// --- Dataset Profile ---

export interface DatasetProfile {
  readonly filePath: string;
  readonly rowCount: number;
  readonly columnCount: number;
  readonly columns: readonly ColumnProfile[];
}

// --- Health Score ---

export interface HealthScore {
  readonly grade: string; // A–F
  readonly points: number; // 0–100
}

/**
 * Calculate health score — mirrors DatasetProfile.health_score() in Python.
 * Per-column cap of -20 when findingsByColumn is provided.
 */
export function healthScore(
  findingsByColumn?: Readonly<Record<string, { errors?: number; warnings?: number }>>,
  errors?: number,
  warnings?: number,
): HealthScore {
  let points: number;

  if (findingsByColumn) {
    let totalDeduction = 0;
    for (const colData of Object.values(findingsByColumn)) {
      const colDeduction = (colData.errors ?? 0) * 10 + (colData.warnings ?? 0) * 3;
      totalDeduction += Math.min(colDeduction, 20);
    }
    points = Math.max(100 - totalDeduction, 0);
  } else {
    points = 100 - (errors ?? 0) * 10 - (warnings ?? 0) * 3;
    points = Math.max(points, 0);
  }

  let grade: string;
  if (points >= 90) grade = "A";
  else if (points >= 80) grade = "B";
  else if (points >= 70) grade = "C";
  else if (points >= 60) grade = "D";
  else grade = "F";

  return { grade, points };
}

// --- Scan Result ---

export interface ScanResult {
  readonly findings: readonly Finding[];
  readonly profile: DatasetProfile;
}

// --- Config types (mirrors goldencheck.yml / config/schema.py) ---

export interface Settings {
  readonly sampleSize: number;
  readonly severityThreshold: string;
  readonly failOn: string;
}

export interface ColumnRule {
  readonly type: string;
  readonly required?: boolean | undefined;
  readonly nullable?: boolean | undefined;
  readonly format?: string | undefined;
  readonly unique?: boolean | undefined;
  readonly range?: readonly [number, number] | undefined;
  readonly enum?: readonly string[] | undefined;
  readonly outlierStddev?: number | undefined;
}

export interface RelationRule {
  readonly type: string;
  readonly columns: readonly string[];
}

export interface IgnoreEntry {
  readonly column: string;
  readonly check: string;
}

export interface GoldenCheckConfig {
  readonly version: number;
  readonly settings: Settings;
  readonly columns: Readonly<Record<string, ColumnRule>>;
  readonly relations: readonly RelationRule[];
  readonly ignore: readonly IgnoreEntry[];
}

export function defaultSettings(): Settings {
  return { sampleSize: 100_000, severityThreshold: "warning", failOn: "error" };
}

export function defaultConfig(): GoldenCheckConfig {
  return {
    version: 1,
    settings: defaultSettings(),
    columns: {},
    relations: [],
    ignore: [],
  };
}

// --- Semantic types ---

export interface TypeDef {
  readonly nameHints: readonly string[];
  readonly valueSignals: Readonly<Record<string, unknown>>;
  readonly suppress: readonly string[];
}

export interface ColumnClassification {
  readonly typeName: string | null;
  readonly source: "name" | "value" | "llm" | "none";
}
