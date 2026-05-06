export type RunManifest = {
  run_name: string;
  generated_at: string;
  total_pairs: number;
  cluster_count: number;
  row_count: number;
};

export type Matchkey = {
  column: string;
  scorer: string;
  weight: number;
  transforms: string[];
};

/** Mirrors goldenmatch.config.schemas.StandardizationConfig.rules — column
 *  → ordered list of standardizer names from STANDARDIZERS. */
export type StandardizationRules = Record<string, string[]>;

export type RulesPayload = {
  threshold: number;
  matchkeys: Matchkey[];
  standardization?: StandardizationRules | null;
};

export const STANDARDIZERS = [
  "email", "name_proper", "name_upper", "name_lower",
  "phone", "zip5", "address", "state", "strip", "trim_whitespace",
] as const;

export type Standardizer = (typeof STANDARDIZERS)[number];

export type Project = {
  project_root: string;
  config_path: string | null;
  rules: RulesPayload;
  runs: RunManifest[];
};

export type ClusterSummary = {
  cluster_id: number;
  size: number;
  max_score: number | null;
  min_score: number | null;
  representative_row_id: number;
};

export type FieldBreakdown = {
  field: string;
  scorer: string;
  value_a: unknown;
  value_b: unknown;
  score: number;
  weight: number;
  diff_type: string;
};

export type Pair = {
  row_id_a: number;
  row_id_b: number;
  score: number;
  cluster_id: number;
  fields: FieldBreakdown[];
  /** One-line template explanation (server-side, zero LLM cost). Absent for
   *  ground-truth-only stub pairs in the evaluation FN list. */
  prose?: string;
};

export type ClusterDetail = {
  cluster_id: number;
  row_ids: number[];
  rows: { row_id: number; columns: Record<string, unknown> }[];
  pairs: Pair[];
};

export const SCORERS = [
  "exact", "jaro_winkler", "levenshtein", "token_sort", "soundex_match",
  "embedding", "record_embedding", "ensemble", "dice", "jaccard",
] as const;

export const TRANSFORMS = [
  "lowercase", "uppercase", "strip", "strip_all", "soundex", "metaphone",
  "digits_only", "alpha_only", "normalize_whitespace",
  "token_sort", "first_token", "last_token",
] as const;

export type Scorer = (typeof SCORERS)[number];
export type Transform = (typeof TRANSFORMS)[number];

// FastAPI 422 detail entry shape.
export type PydanticError = {
  loc: (string | number)[];
  msg: string;
  type?: string;
};
