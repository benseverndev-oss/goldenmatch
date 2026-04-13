/**
 * Core types for GoldenFlow JS — ported from Python dataclasses and Pydantic models.
 * All types are readonly. Use factory functions for construction.
 */

// ---------------------------------------------------------------------------
// Transform Registry
// ---------------------------------------------------------------------------

export type TransformMode = "expr" | "series" | "dataframe";

/** Metadata for a registered transform. */
export interface TransformInfo {
  readonly name: string;
  readonly func: TransformFunction;
  readonly inputTypes: readonly string[];
  readonly autoApply: boolean;
  readonly priority: number;
  readonly mode: TransformMode;
}

/**
 * A transform function. Shape depends on mode:
 * - expr:      (column: string, ...params: unknown[]) => ExprTransform
 * - series:    (values: readonly ColumnValue[], ...params: unknown[]) => ColumnValue[] | [ColumnValue[], number[]]
 * - dataframe: (rows: readonly Row[], column: string) => Row[]
 */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type TransformFunction = (...args: any[]) => any;

// ---------------------------------------------------------------------------
// TabularData primitives
// ---------------------------------------------------------------------------

export type ColumnValue = string | number | boolean | null;
export type Row = Readonly<Record<string, unknown>>;

export type Dtype =
  | "string"
  | "integer"
  | "float"
  | "boolean"
  | "date"
  | "datetime"
  | "null";

// ---------------------------------------------------------------------------
// Manifest / Transform results
// ---------------------------------------------------------------------------

export interface TransformRecord {
  readonly column: string;
  readonly transform: string;
  readonly affectedRows: number;
  readonly totalRows: number;
  readonly sampleBefore: readonly string[];
  readonly sampleAfter: readonly string[];
}

export function makeTransformRecord(
  input: Pick<TransformRecord, "column" | "transform" | "affectedRows" | "totalRows"> &
    Partial<Pick<TransformRecord, "sampleBefore" | "sampleAfter">>,
): TransformRecord {
  return {
    sampleBefore: [],
    sampleAfter: [],
    ...input,
  };
}

export interface TransformError {
  readonly column: string;
  readonly transform: string;
  readonly row: number;
  readonly error: string;
}

export interface Manifest {
  readonly source: string;
  readonly records: readonly TransformRecord[];
  readonly errors: readonly TransformError[];
  readonly createdAt: string;
}

export function makeManifest(source: string): MutableManifest {
  return new MutableManifest(source);
}

/** Mutable manifest used during transform pipeline, then frozen. */
export class MutableManifest implements Manifest {
  readonly source: string;
  readonly records: TransformRecord[] = [];
  readonly errors: TransformError[] = [];
  readonly createdAt: string;

  constructor(source: string) {
    this.source = source;
    this.createdAt = new Date().toISOString();
  }

  addRecord(record: TransformRecord): void {
    this.records.push(record);
  }

  addError(column: string, transform: string, row: number, error: string): void {
    this.errors.push({ column, transform, row, error });
  }

  toDict(): Record<string, unknown> {
    return {
      source: this.source,
      created_at: this.createdAt,
      records: this.records.map((r) => ({
        column: r.column,
        transform: r.transform,
        affected_rows: r.affectedRows,
        total_rows: r.totalRows,
        sample_before: r.sampleBefore,
        sample_after: r.sampleAfter,
      })),
      errors: this.errors.map((e) => ({
        column: e.column,
        transform: e.transform,
        row: e.row,
        error: e.error,
      })),
      summary: {
        total_transforms: this.records.length,
        total_errors: this.errors.length,
        columns_affected: [...new Set(this.records.map((r) => r.column))],
      },
    };
  }
}

// ---------------------------------------------------------------------------
// TransformResult
// ---------------------------------------------------------------------------

export interface TransformResult {
  readonly rows: readonly Row[];
  readonly columns: readonly string[];
  readonly manifest: Manifest;
}

// ---------------------------------------------------------------------------
// Profiler types
// ---------------------------------------------------------------------------

export interface ColumnProfile {
  readonly name: string;
  readonly inferredType: string;
  readonly rowCount: number;
  readonly nullCount: number;
  readonly nullPct: number;
  readonly uniqueCount: number;
  readonly uniquePct: number;
  readonly sampleValues: readonly string[];
  readonly detectedFormat: string | null;
}

export function makeColumnProfile(
  input: Pick<ColumnProfile, "name" | "inferredType" | "rowCount" | "nullCount" | "nullPct" | "uniqueCount" | "uniquePct"> &
    Partial<Pick<ColumnProfile, "sampleValues" | "detectedFormat">>,
): ColumnProfile {
  return {
    sampleValues: [],
    detectedFormat: null,
    ...input,
  };
}

export interface DatasetProfile {
  readonly filePath: string;
  readonly rowCount: number;
  readonly columnCount: number;
  readonly columns: readonly ColumnProfile[];
}

// ---------------------------------------------------------------------------
// Diff types
// ---------------------------------------------------------------------------

export interface ColumnDiffDetail {
  readonly changedRows: number;
}

export interface DiffResult {
  readonly totalChanges: number;
  readonly changedColumns: readonly string[];
  readonly addedColumns: readonly string[];
  readonly removedColumns: readonly string[];
  readonly rowCountBefore: number;
  readonly rowCountAfter: number;
  readonly columnDetails: Readonly<Record<string, ColumnDiffDetail>>;
}

// ---------------------------------------------------------------------------
// Config types (Pydantic → interfaces + validator)
// ---------------------------------------------------------------------------

export interface TransformSpec {
  readonly column: string;
  readonly ops: readonly string[];
}

export interface SplitSpec {
  readonly source: string;
  readonly target: readonly string[];
  readonly method: string;
}

export interface FilterSpec {
  readonly column: string;
  readonly condition: string;
}

export interface DedupSpec {
  readonly columns: readonly string[];
  readonly keep: "first" | "last";
}

export interface MappingSpec {
  readonly source: string;
  readonly target: string | readonly string[];
  readonly transform: string | readonly string[] | null;
}

export interface GoldenFlowConfig {
  readonly source: string | null;
  readonly output: string | null;
  readonly transforms: readonly TransformSpec[];
  readonly splits: readonly SplitSpec[];
  readonly renames: Readonly<Record<string, string>>;
  readonly drop: readonly string[];
  readonly filters: readonly FilterSpec[];
  readonly dedup: DedupSpec | null;
  readonly mappings: readonly MappingSpec[];
}

export function makeConfig(
  input?: Partial<GoldenFlowConfig>,
): GoldenFlowConfig {
  return {
    source: null,
    output: null,
    transforms: [],
    splits: [],
    renames: {},
    drop: [],
    filters: [],
    dedup: null,
    mappings: [],
    ...input,
  };
}

// ---------------------------------------------------------------------------
// Schema Mapping
// ---------------------------------------------------------------------------

export interface ColumnMapping {
  readonly source: string;
  readonly target: string;
  readonly confidence: number;
  readonly transform: string | null;
}

// ---------------------------------------------------------------------------
// Domain packs
// ---------------------------------------------------------------------------

export interface DomainPack {
  readonly name: string;
  readonly description: string;
  readonly transforms: readonly string[];
  readonly defaultConfig: GoldenFlowConfig;
}

// ---------------------------------------------------------------------------
// History
// ---------------------------------------------------------------------------

export interface RunRecord {
  readonly runId: string;
  readonly source: string;
  readonly timestamp: string;
  readonly rows: number;
  readonly columns: number;
  readonly transformsApplied: number;
  readonly errors: number;
  readonly durationSeconds: number;
  readonly configHash: string | null;
  readonly manifestPath: string | null;
}
