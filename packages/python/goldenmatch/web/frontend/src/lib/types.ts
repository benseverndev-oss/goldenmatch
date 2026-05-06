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

export type RulesPayload = { threshold: number; matchkeys: Matchkey[] };

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
};

export type ClusterDetail = {
  cluster_id: number;
  row_ids: number[];
  rows: { row_id: number; columns: Record<string, unknown> }[];
  pairs: Pair[];
};
