/**
 * Baseline profile models — TypeScript port of goldencheck/baseline/models.py.
 * Edge-safe: no Node.js dependencies.
 */

// ---------------------------------------------------------------------------
// Interfaces
// ---------------------------------------------------------------------------

export interface StatProfile {
  distribution: string | null; // "normal", "log_normal", "exponential", "uniform", "categorical"
  params: Record<string, number>;
  entropy: number | null;
  benford: { conforming: boolean; chi2_pvalue: number } | null;
  bounds: { min: number; max: number; p01: number; p99: number } | null;
}

export interface FunctionalDependency {
  determinant: string;
  dependent: string;
  confidence: number;
}

export interface TemporalOrder {
  startCol: string;
  endCol: string;
  violationRate: number;
}

export interface CorrelationEntry {
  col1: string;
  col2: string;
  method: "pearson" | "cramers_v";
  value: number;
  strength: "strong" | "moderate";
}

export interface PatternGrammar {
  column: string;
  regex: string;
  coverage: number;
}

export interface ConfidencePrior {
  confidence: number;
  evidenceCount: number;
}

export interface BaselineProfile {
  sourceFilename: string | null;
  rowCount: number;
  columnCount: number;
  stats: Record<string, StatProfile>;
  functionalDeps: FunctionalDependency[];
  candidateKeys: string[];
  temporalOrders: TemporalOrder[];
  correlations: CorrelationEntry[];
  patterns: Record<string, PatternGrammar[]>;
  confidencePriors: Record<string, Record<string, ConfidencePrior>>;
  semanticTypes: Record<string, string>;
}

// ---------------------------------------------------------------------------
// Serialisation
// ---------------------------------------------------------------------------

/** Serialize a BaselineProfile to a JSON string. */
export function serializeBaseline(profile: BaselineProfile): string {
  return JSON.stringify(profile, null, 2);
}

/** Deserialize a BaselineProfile from a JSON string. */
export function deserializeBaseline(json: string): BaselineProfile {
  const raw = JSON.parse(json) as Record<string, unknown>;

  // Validate required top-level fields
  const profile: BaselineProfile = {
    sourceFilename: (raw.sourceFilename as string | null) ?? null,
    rowCount: (raw.rowCount as number) ?? 0,
    columnCount: (raw.columnCount as number) ?? 0,
    stats: (raw.stats as Record<string, StatProfile>) ?? {},
    functionalDeps: (raw.functionalDeps as FunctionalDependency[]) ?? [],
    candidateKeys: (raw.candidateKeys as string[]) ?? [],
    temporalOrders: (raw.temporalOrders as TemporalOrder[]) ?? [],
    correlations: (raw.correlations as CorrelationEntry[]) ?? [],
    patterns: (raw.patterns as Record<string, PatternGrammar[]>) ?? {},
    confidencePriors:
      (raw.confidencePriors as Record<string, Record<string, ConfidencePrior>>) ?? {},
    semanticTypes: (raw.semanticTypes as Record<string, string>) ?? {},
  };

  return profile;
}
