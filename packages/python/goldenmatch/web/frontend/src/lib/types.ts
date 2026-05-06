export type RunManifest = {
  run_name: string;
  generated_at: string;
  total_pairs: number;
  cluster_count: number;
  row_count: number;
};

export type MatchkeyType = "exact" | "weighted" | "probabilistic";

export type Matchkey = {
  column: string;
  scorer: string;
  weight: number;
  transforms: string[];
  /** Workbench-only override of the engine matchkey type. When omitted,
   *  preview/run pick exact for `scorer === "exact"` and weighted otherwise. */
  type?: MatchkeyType;
  /** Probabilistic-only: 2 = agree/disagree, 3 = agree/partial/disagree. */
  levels?: number;
  /** Probabilistic + levels=3: score >= this counts as a partial agree. */
  partial_threshold?: number;
  /** Probabilistic-only: EM iteration cap. */
  em_iterations?: number;
};

/** Mirrors goldenmatch.config.schemas.StandardizationConfig.rules — column
 *  → ordered list of standardizer names from STANDARDIZERS. */
export type StandardizationRules = Record<string, string[]>;

export type BlockingKey = {
  fields: string[];
  transforms: string[];
};

export type BlockingStrategy =
  | "static"
  | "adaptive"
  | "sorted_neighborhood"
  | "multi_pass"
  | "ann"
  | "canopy"
  | "ann_pairs"
  | "learned";

/** Slim view of the engine's BlockingConfig that the workbench surfaces.
 *
 *  No index signature — typos like `auto_sugest` are caught by excess-
 *  property checks. Advanced strategies (ann_*, learned_*, canopy, …) the
 *  workbench doesn't surface round-trip through the typed `extras` field;
 *  ``mergeBlockingForWire`` merges them back at request time. */
export type BlockingPayload = {
  strategy?: BlockingStrategy;
  keys?: BlockingKey[];
  passes?: BlockingKey[] | null;
  max_block_size?: number;
  skip_oversized?: boolean;
  auto_suggest?: boolean;
  auto_select?: boolean;
  /** Fields the workbench doesn't surface but came from the server (or the
   *  user's hand-edited YAML). Spread back onto the wire payload on save. */
  extras?: Record<string, unknown>;
};

/** Known field names — anything else lands in `extras`. */
const BLOCKING_KNOWN_KEYS = new Set([
  "strategy",
  "keys",
  "passes",
  "max_block_size",
  "skip_oversized",
  "auto_suggest",
  "auto_select",
  "extras",
]);

/** Split a raw blocking dict from the server into known fields + extras. */
export function parseBlockingFromServer(
  raw: Record<string, unknown> | null | undefined,
): BlockingPayload | null {
  if (!raw) return null;
  const out: BlockingPayload = {};
  const extras: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(raw)) {
    if (BLOCKING_KNOWN_KEYS.has(k)) {
      // Trusted: server validated against BlockingConfig. Cast at the
      // single boundary so callers get a typed object.
      (out as Record<string, unknown>)[k] = v;
    } else {
      extras[k] = v;
    }
  }
  if (Object.keys(extras).length > 0) out.extras = extras;
  return out;
}

/** Flatten BlockingPayload back to the wire shape for save / preview. */
export function serializeBlockingForWire(
  blocking: BlockingPayload | null | undefined,
): Record<string, unknown> | null {
  if (!blocking) return null;
  const { extras, ...known } = blocking;
  return { ...known, ...(extras ?? {}) };
}

export type RulesPayload = {
  threshold: number;
  matchkeys: Matchkey[];
  standardization?: StandardizationRules | null;
  blocking?: BlockingPayload | null;
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
