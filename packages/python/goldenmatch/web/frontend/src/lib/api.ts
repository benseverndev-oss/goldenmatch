import type {
  ClusterDetail,
  ClusterSummary,
  Project,
  RulesPayload,
  RunManifest,
} from "./types";

export type ClustersPage = {
  items: ClusterSummary[];
  cursor: number | null;
  total: number;
};

export type LabelRecord = {
  row_id_a: number;
  row_id_b: number;
  label: "match" | "non_match";
  note?: string | null;
  ts: string;
};

/** Per-run label record — the global label record plus the cluster_id of
 *  the pair within this run. */
export type RunLabelRecord = LabelRecord & { cluster_id: number };

export type PreviewResponse = { run_name: string };
export type SaveRulesResponse = { saved: boolean; path: string };

export type EvaluationSummary = {
  tp: number;
  fp: number;
  fn: number;
  precision: number;
  recall: number;
  f1: number;
  predicted_pairs: number;
  ground_truth_pairs: number;
  label_counts: { positives: number; negatives: number; total: number };
  confirmed_fp: number;
  unlabeled_fp: number;
};

export type CompareSummary = {
  unchanged: number;
  merged: number;
  partitioned: number;
  overlapping: number;
  rc: number;
  cc1: number;
  cc2: number;
  sc1: number;
  sc2: number;
  twi: number;
  unchanged_pct: number;
  merged_pct: number;
  partitioned_pct: number;
  overlapping_pct: number;
};

export type ClusterCase = {
  cluster_id: number;
  case: "unchanged" | "merged" | "partitioned" | "overlapping";
  members: number[];
  er2_clusters: Record<string, number[]>;
};

export type CompareResponse = {
  run_a: string;
  run_b: string;
  summary: CompareSummary;
  cases: ClusterCase[];
};

export type SensitivityPoint = {
  value: number;
  cluster_count_a: number;
  cluster_count_b: number;
  unchanged: number;
  merged: number;
  partitioned: number;
  overlapping: number;
  twi: number;
};

export type SensitivityResponse = {
  field: string;
  baseline_value: number | null;
  sample_n: number;
  stability: {
    best_value: number;
    best_unchanged_pct: number;
    points: { value: number; unchanged: number; merged: number; partitioned: number; overlapping: number; twi: number }[];
  };
  points: SensitivityPoint[];
};

export type EvaluationResponse = {
  summary: EvaluationSummary;
  tp: import("./types").Pair[];
  fp_confirmed: import("./types").Pair[];
  fp_unlabeled: import("./types").Pair[];
  fn: import("./types").Pair[];
};

export type RunResponse = {
  run_name: string;
  row_count: number;
  cluster_count: number;
  total_pairs: number;
  lineage_path: string;
  clusters_path: string;
  auto_config: boolean;
  llm_boost: boolean;
};

export type WebSettings = {
  llm_boost_default: boolean;
  llm_provider: "openai" | "anthropic";
  llm_max_cost_usd: number;
  llm_max_calls: number;
  review_band_lo: number;
  review_band_hi: number;
  preview_sample_n: number;
};

export type SettingsResponse = WebSettings & {
  llm_keys_present: { openai: boolean; anthropic: boolean };
  _path: string;
};

const json = async <T>(resp: Response): Promise<T> => {
  if (!resp.ok) throw new Error(`${resp.status} ${await resp.text()}`);
  return resp.json() as Promise<T>;
};

const post = <T>(path: string, body: unknown): Promise<T> =>
  fetch(path, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  }).then((r) => json<T>(r));

export const api = {
  project: (): Promise<Project> =>
    fetch("/api/v1/project").then((r) => json<Project>(r)),
  run: (name: string): Promise<RunManifest> =>
    fetch(`/api/v1/runs/${name}`).then((r) => json<RunManifest>(r)),
  clusters: (name: string, cursor?: number): Promise<ClustersPage> =>
    fetch(`/api/v1/runs/${name}/clusters?cursor=${cursor ?? 0}`).then((r) =>
      json<ClustersPage>(r),
    ),
  cluster: (name: string, id: number): Promise<ClusterDetail> =>
    fetch(`/api/v1/runs/${name}/clusters/${id}`).then((r) =>
      json<ClusterDetail>(r),
    ),
  rules: (): Promise<RulesPayload> =>
    fetch("/api/v1/rules").then((r) => json<RulesPayload>(r)),
  putRules: (body: RulesPayload): Promise<RulesPayload> =>
    fetch("/api/v1/rules", {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }).then((r) => json<RulesPayload>(r)),
  saveRules: (): Promise<SaveRulesResponse> =>
    fetch("/api/v1/rules/save", { method: "POST" }).then((r) =>
      json<SaveRulesResponse>(r),
    ),
  preview: (body: {
    rules: RulesPayload;
    sample: { n: number; seed: number };
  }): Promise<PreviewResponse> => post<PreviewResponse>("/api/v1/preview", body),
  postLabel: (body: {
    row_id_a: number;
    row_id_b: number;
    label: "match" | "non_match";
    note?: string;
  }): Promise<LabelRecord> => post<LabelRecord>("/api/v1/labels", body),
  runLabels: (name: string): Promise<RunLabelRecord[]> =>
    fetch(`/api/v1/runs/${name}/labels`).then((r) =>
      json<RunLabelRecord[]>(r),
    ),
  runEvaluation: (name: string): Promise<EvaluationResponse> =>
    fetch(`/api/v1/runs/${name}/evaluation`).then((r) =>
      json<EvaluationResponse>(r),
    ),
  unmerge: (
    name: string,
    body: { mode: "record" | "cluster"; cluster_id: number; row_id?: number },
  ): Promise<{ run_name: string; mode: string; broken_pairs: number; cluster_count: number }> =>
    post(`/api/v1/runs/${name}/unmerge`, body),
  runReview: (
    name: string,
    opts?: { lo?: number; hi?: number; includeLabeled?: boolean; limit?: number },
  ): Promise<import("./types").Pair[]> => {
    const params = new URLSearchParams();
    if (opts?.lo != null) params.set("lo", String(opts.lo));
    if (opts?.hi != null) params.set("hi", String(opts.hi));
    if (opts?.includeLabeled) params.set("include_labeled", "true");
    if (opts?.limit != null) params.set("limit", String(opts.limit));
    const qs = params.toString();
    return fetch(
      `/api/v1/runs/${name}/review${qs ? `?${qs}` : ""}`,
    ).then((r) => json<import("./types").Pair[]>(r));
  },
  autoconfig: (): Promise<RulesPayload> =>
    fetch("/api/v1/autoconfig", { method: "POST" }).then((r) =>
      json<RulesPayload>(r),
    ),
  executeRun: (body?: {
    auto_config?: boolean;
    llm_boost?: boolean;
    rules?: RulesPayload;
  }): Promise<RunResponse> => post<RunResponse>("/api/v1/run", body ?? {}),
  settings: (): Promise<SettingsResponse> =>
    fetch("/api/v1/settings").then((r) => json<SettingsResponse>(r)),
  compare: (run_a: string, run_b: string): Promise<CompareResponse> =>
    post<CompareResponse>("/api/v1/compare", { run_a, run_b }),
  sensitivity: (body: {
    field: string;
    start: number;
    stop: number;
    step: number;
    sample_n: number;
    rules?: RulesPayload;
  }): Promise<SensitivityResponse> => post<SensitivityResponse>("/api/v1/sensitivity", body),
  putSettings: (body: WebSettings): Promise<SettingsResponse> =>
    fetch("/api/v1/settings", {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }).then((r) => json<SettingsResponse>(r)),
};
