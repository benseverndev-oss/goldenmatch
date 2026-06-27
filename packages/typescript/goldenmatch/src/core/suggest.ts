/**
 * suggest.ts — the TS healer surface (config-suggestion loop).
 *
 * The TS analog of the Python healer (`core/suggest/adapter.py` +
 * `core/suggest/{apply,health}.py` + the surface helpers). It drives the SAME
 * shared Rust kernel (`suggest-core`, compiled to wasm) through the lean
 * registry in `suggestWasmBackend.ts`. The kernel itself is OPT-IN: until
 * `enableSuggestWasm()` registers a backend, every function here is
 * graceful-empty (returns `[]` / a no-op heal), and NOTHING throws. This mirrors
 * Python's default-OFF native gate (`pip install goldenmatch[native]`).
 *
 * Edge-safe: no `node:*` imports. `process.env` is read only behind a
 * `typeof process` guard (the kill-switch).
 */

import type {
  Row,
  GoldenMatchConfig,
  MatchkeyConfig,
  MatchkeyField,
  NegativeEvidenceField,
  ClusterInfo,
  DedupeResult,
  SerializedSuggestion,
} from "./types.js";
import { getMatchkeys } from "./types.js";
import type { PostflightReport } from "./autoconfigVerify.js";
import { getSuggestWasmBackend } from "./suggestWasmBackend.js";
import {
  buildColumnSignals,
  type ColumnSignalCluster,
} from "./suggestColumnSignals.js";
import { runDedupePipeline } from "./pipeline.js";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/**
 * A single config suggestion returned by the kernel. Mirrors the Rust
 * `Suggestion` struct / Python `Suggestion` dataclass (camelCase here, the
 * kernel JSON is snake_case — parsed by {@link parseSuggestions}).
 */
export interface Suggestion {
  readonly id: string;
  readonly kind: string;
  readonly target: string;
  readonly currentValue: unknown;
  readonly proposedValue: unknown;
  readonly rationale: string;
  readonly predictedEffect: string;
  readonly confidence: number;
  readonly patch: Readonly<Record<string, unknown>>;
  readonly evidence: Readonly<Record<string, unknown>>;
}

/** Why the free default-pipeline trigger fired (or null = no headroom). */
export interface HeadroomReason {
  /** `threshold_adjustment` = postflight already proposed a threshold move;
   *  `score_distribution` = the score histogram is bimodal (sub-cutoff mass). */
  readonly signal: "threshold_adjustment" | "score_distribution";
  readonly detail: string;
}

export interface SuggestOptions {
  /** Re-run the pipeline per candidate and drop health-worsening ones. */
  readonly verify?: boolean;
}

export interface HealOptions {
  /** Max apply-and-re-run steps before stopping (default 5). */
  readonly stepCap?: number;
}

export interface HealResult {
  readonly config: GoldenMatchConfig;
  readonly trail: readonly Suggestion[];
  readonly result: DedupeResult;
}

// ---------------------------------------------------------------------------
// Constants (mirror Python adapter.py)
// ---------------------------------------------------------------------------

/** Cap on candidates re-run during verification (cost guard). */
const MAX_VERIFY_CANDIDATES = 8;

/** Keep a suggestion if cand_health >= baseline_health - EPS. */
const VERIFY_EPS = 1e-6;

// Cluster-health proxy constants (port of `suggest/health.py`).
const COLLAPSE_PENALTY = 0.5;
const COLLAPSE_FLOOR_CLUSTER = 0.25;

// ---------------------------------------------------------------------------
// serializeSuggestions
// ---------------------------------------------------------------------------

/**
 * Project full {@link Suggestion}s onto the wire shape carried on
 * {@link DedupeResult.suggestions} (mirror of Python `serialize_suggestions`).
 * `verified` is caller-supplied (NOT read off the suggestion).
 */
export function serializeSuggestions(
  suggestions: readonly Suggestion[],
  options: { readonly verified: boolean },
): SerializedSuggestion[] {
  return suggestions.map((s) => ({
    id: s.id,
    kind: s.kind,
    target: s.target,
    rationale: s.rationale,
    verified: options.verified,
    patch: s.patch,
  }));
}

// ---------------------------------------------------------------------------
// headroomSignal — the free default-pipeline trigger
// ---------------------------------------------------------------------------

/**
 * Score-distribution trigger for the free default surface. Fires when the
 * postflight report shows the config has headroom: either a threshold
 * {@link PostflightReport.adjustments | adjustment} was proposed, OR the score
 * histogram is bimodal (sub-cutoff mass present).
 *
 * `undefined` report → null (no postflight ran ⇒ nothing to act on).
 *
 * NOTE — documented divergence from Python: Python's `headroom_signal` ORs this
 * score-distribution half with a controller-health (dip) half. The TS
 * `PostflightReport` carries no controller-history/health/dip field, so the TS
 * trigger mirrors only the score-distribution half. The postflight emits a
 * "score distribution is unimodal" advisory exactly when the histogram is NOT
 * bimodal, so its ABSENCE is the faithful "bimodal scoreHistogram" signal.
 */
export function headroomSignal(
  report: PostflightReport | undefined,
): HeadroomReason | null {
  if (report === undefined || report === null) return null;

  const thresholdAdj = report.adjustments.find((a) => a.field === "threshold");
  if (thresholdAdj !== undefined) {
    return {
      signal: "threshold_adjustment",
      detail:
        `threshold ${String(thresholdAdj.fromValue)} -> ` +
        `${String(thresholdAdj.toValue)} (${thresholdAdj.reason})`,
    };
  }

  if (isBimodalDistribution(report)) {
    return {
      signal: "score_distribution",
      detail:
        "bimodal score distribution: sub-cutoff mass suggests threshold/scorer headroom",
    };
  }

  return null;
}

/** Bimodal iff the postflight did NOT emit its unimodal advisory. */
function isBimodalDistribution(report: PostflightReport): boolean {
  return !report.advisories.some((a) => a.toLowerCase().includes("unimodal"));
}

// ---------------------------------------------------------------------------
// suggestFromResult — build the 3 kernel inputs, call the backend
// ---------------------------------------------------------------------------

/**
 * Run the healer kernel against a completed {@link DedupeResult}. Builds the
 * three kernel inputs (scored-pairs / clusters / column-signals), packs config
 * + priors, calls the registered wasm backend, and parses the JSON array into
 * typed {@link Suggestion}s.
 *
 * Graceful-empty: a null backend (wasm not enabled) or any kernel error → `[]`.
 * When `verify` is true, each candidate (capped at 8) is re-run through the
 * pipeline with its patch applied and dropped if it worsens cluster health.
 */
export async function suggestFromResult(
  result: DedupeResult,
  rows: readonly Row[],
  options?: SuggestOptions,
): Promise<Suggestion[]> {
  const backend = getSuggestWasmBackend();
  if (backend === null) return [];

  const config = result.config;
  const clusters = [...result.clusters.values()];

  const scoredPairsJson = JSON.stringify({
    score: result.scoredPairs.map((p) => p.score),
    n_pairs: result.scoredPairs.length,
  });
  const clustersJson = JSON.stringify(
    clusters.map((c) => ({ quality: c.clusterQuality, oversized: c.oversized })),
  );
  const columnSignals = buildColumnSignals(
    rows,
    clusters as unknown as ColumnSignalCluster[],
    config,
  );
  const columnSignalsJson = JSON.stringify(columnSignals);
  const configJson = JSON.stringify(configSummary(config));
  const priorsJson = JSON.stringify({ counts: {} });

  let raw: string;
  try {
    raw = backend.suggestReview({
      scored_pairs: scoredPairsJson,
      clusters: clustersJson,
      column_signals: columnSignalsJson,
      config: configJson,
      priors: priorsJson,
    });
  } catch {
    return [];
  }

  const suggestions = parseSuggestions(raw);
  const verify = options?.verify ?? false;
  if (!verify || suggestions.length === 0) return suggestions;

  const baselineHealth = clusterHealth(clusters, rows.length);
  return verifySuggestions(suggestions, rows, config, baselineHealth);
}

// ---------------------------------------------------------------------------
// maybeSuggest — kill-switch + trigger gate, then suggestFromResult
// ---------------------------------------------------------------------------

/**
 * Gate {@link suggestFromResult} behind the kill-switch
 * (`GOLDENMATCH_SUGGEST_ON_DEDUPE=0`) and the free trigger
 * ({@link headroomSignal}). When either says "no", returns `[]` WITHOUT
 * touching the backend (the cost guarantee). `config` is part of the surface
 * for parity with the Python `maybe_suggest`; the kernel inputs are derived
 * from `result.config`, which equals it on the default path.
 */
export async function maybeSuggest(
  result: DedupeResult,
  rows: readonly Row[],
  _config: GoldenMatchConfig,
  options?: SuggestOptions,
): Promise<Suggestion[]> {
  if (suggestKillSwitchActive()) return [];
  if (headroomSignal(result.postflightReport) === null) return [];
  return suggestFromResult(result, rows, options);
}

// ---------------------------------------------------------------------------
// reviewConfig — explicit "review this config" entry (no trigger gate)
// ---------------------------------------------------------------------------

/**
 * Run a dedupe over `rows` with `config`, then ask the kernel for suggestions.
 * Unlike {@link maybeSuggest}, this is the explicit-request path — no kill-switch
 * / trigger gate (the user asked for it). Still graceful-empty when wasm is off.
 */
export async function reviewConfig(
  rows: readonly Row[],
  config: GoldenMatchConfig,
  options?: SuggestOptions,
): Promise<Suggestion[]> {
  const result = await runDedupePipeline(rows, config, {});
  return suggestFromResult(result, rows, options);
}

// ---------------------------------------------------------------------------
// heal — bounded apply-and-re-run loop
// ---------------------------------------------------------------------------

/**
 * Bounded healer loop (port of the Python `heal`): repeatedly verified-suggest,
 * apply the top suggestion's patch, re-run, and cycle-guard (never re-apply the
 * same patch). Returns the healed config, the applied trail, and the final
 * result. Graceful-empty: wasm off ⇒ zero steps, original config + result back.
 */
export async function heal(
  rows: readonly Row[],
  config: GoldenMatchConfig,
  options?: HealOptions,
): Promise<HealResult> {
  const stepCap = options?.stepCap ?? 5;

  let currentConfig = config;
  let result = await runDedupePipeline(rows, currentConfig, {});
  const trail: Suggestion[] = [];
  const applied = new Set<string>();

  for (let step = 0; step < stepCap; step++) {
    const suggestions = await suggestFromResult(result, rows, { verify: true });
    if (suggestions.length === 0) break;

    // Pick the top suggestion whose patch hasn't already been applied.
    const next = suggestions.find((s) => !applied.has(patchSig(s)));
    if (next === undefined) break; // cycle guard: nothing new to try

    let candidateConfig: GoldenMatchConfig;
    try {
      candidateConfig = applyPatch(currentConfig, next);
    } catch {
      break; // unappliable patch — stop healing
    }

    applied.add(patchSig(next));
    currentConfig = candidateConfig;
    result = await runDedupePipeline(rows, currentConfig, {});
    trail.push(next);
  }

  return { config: currentConfig, trail, result };
}

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

/** Read the kill-switch env var behind a non-Node guard (edge-safe). */
function suggestKillSwitchActive(): boolean {
  return (
    typeof process !== "undefined" &&
    process?.env?.GOLDENMATCH_SUGGEST_ON_DEDUPE === "0"
  );
}

/** Stable identity of a patch for the heal cycle-guard. */
function patchSig(s: Suggestion): string {
  return JSON.stringify(s.patch);
}

/**
 * Reduced, frame-free view of the config the kernel reads (mirror of Python
 * `_config_summary`): `{matchkeys:[{name,kind,threshold,fields:[{field,scorer,
 * weight}]}], negative_evidence:[...]}`.
 */
function configSummary(config: GoldenMatchConfig): {
  matchkeys: {
    name: string;
    kind: string;
    threshold: number | null;
    fields: { field: string; scorer: string | null; weight: number | null }[];
  }[];
  negative_evidence: string[];
} {
  const matchkeys = [];
  const ne: string[] = [];
  for (const mk of getMatchkeys(config)) {
    const fields = mk.fields.map((f) => ({
      field: f.field ?? "",
      scorer: f.scorer ?? null,
      weight: f.weight ?? null,
    }));
    matchkeys.push({
      name: mk.name,
      kind: mk.type ?? "weighted",
      threshold: mk.threshold ?? null,
      fields,
    });
    for (const n of mk.negativeEvidence ?? []) {
      if (!ne.includes(n.field)) ne.push(n.field);
    }
  }
  return { matchkeys, negative_evidence: ne };
}

/** Parse the kernel's JSON array into typed suggestions (lenient, never throws). */
function parseSuggestions(raw: string): Suggestion[] {
  let items: unknown;
  try {
    items = JSON.parse(raw);
  } catch {
    return [];
  }
  if (!Array.isArray(items)) return [];

  const out: Suggestion[] = [];
  for (const item of items) {
    if (typeof item !== "object" || item === null) continue;
    const o = item as Record<string, unknown>;
    const confRaw = o["confidence"];
    out.push({
      id: String(o["id"] ?? ""),
      kind: String(o["kind"] ?? ""),
      target: String(o["target"] ?? ""),
      currentValue: o["current_value"] ?? null,
      proposedValue: o["proposed_value"] ?? null,
      rationale: String(o["rationale"] ?? ""),
      predictedEffect: String(o["predicted_effect"] ?? ""),
      confidence:
        typeof confRaw === "number" ? confRaw : Number(confRaw ?? 0) || 0,
      patch:
        typeof o["patch"] === "object" && o["patch"] !== null
          ? (o["patch"] as Record<string, unknown>)
          : {},
      evidence:
        typeof o["evidence"] === "object" && o["evidence"] !== null
          ? (o["evidence"] as Record<string, unknown>)
          : {},
    });
  }
  return out;
}

/**
 * Self-verification pass (port of `review_config` verify): re-run the pipeline
 * per candidate with its patch applied and keep it only if cluster health does
 * not regress past EPS. Beyond the cap, the tail passes through unverified.
 * A re-run / apply error conservatively KEEPS the suggestion.
 */
async function verifySuggestions(
  suggestions: readonly Suggestion[],
  rows: readonly Row[],
  config: GoldenMatchConfig,
  baselineHealth: number,
): Promise<Suggestion[]> {
  const nRecords = rows.length;
  const candidates = suggestions.slice(0, MAX_VERIFY_CANDIDATES);
  const tail = suggestions.slice(MAX_VERIFY_CANDIDATES);

  const verified: Suggestion[] = [];
  for (const s of candidates) {
    try {
      const candConfig = applyPatch(config, s);
      const candResult = await runDedupePipeline(rows, candConfig, {});
      const candHealth = clusterHealth(
        [...candResult.clusters.values()],
        nRecords,
      );
      if (candHealth >= baselineHealth - VERIFY_EPS) verified.push(s);
    } catch {
      // Conservative: keep a suggestion we couldn't check.
      verified.push(s);
    }
  }
  return [...verified, ...tail];
}

/**
 * Unsupervised cluster-health proxy (port of
 * `suggestion_health_from_clusters`): `matched_rate * avg_conf - penalty`,
 * where penalty ramps with the Herfindahl concentration above the floor.
 */
function clusterHealth(
  clusters: readonly ClusterInfo[],
  nRecords: number,
): number {
  if (nRecords === 0) return -1.0;

  const multi = clusters.filter((c) => c.size > 1 && !c.oversized);
  if (multi.length === 0) return 0.0;

  const nMatched = multi.reduce((a, c) => a + c.size, 0);
  const matchedRate = nMatched / nRecords;

  const avgConf =
    multi.reduce(
      (a, c) => a + (typeof c.confidence === "number" ? c.confidence : 0.5),
      0,
    ) / multi.length;

  const hhi = multi.reduce((a, c) => a + (c.size / nRecords) ** 2, 0);
  const over = (hhi - COLLAPSE_FLOOR_CLUSTER) / (1.0 - COLLAPSE_FLOOR_CLUSTER);
  const concentration = Math.min(1.0, Math.max(0.0, over));
  const penalty = COLLAPSE_PENALTY * concentration;

  return matchedRate * avgConf - penalty;
}

/**
 * Apply a suggestion's patch to a config, returning a NEW config (deep copy;
 * the input is never mutated). Port of `apply_suggestion`: supports
 * `set_threshold` / `set_scorer` / `add_negative_evidence`. Throws on an unknown
 * op or a missing matchkey/field (the caller decides keep-vs-stop).
 */
function applyPatch(
  config: GoldenMatchConfig,
  suggestion: Suggestion,
): GoldenMatchConfig {
  const next = deepClone(config);
  const patch = suggestion.patch;
  const op = String(patch["op"] ?? "");
  const mks = getMatchkeys(next);

  if (op === "set_threshold") {
    const mk = findMatchkey(mks, String(patch["matchkey"]));
    (mk as { threshold?: number }).threshold = Number(patch["value"]);
  } else if (op === "set_scorer") {
    const mk = findMatchkey(mks, String(patch["matchkey"]));
    const fname = String(patch["field"]);
    const f = mk.fields.find((x) => x.field === fname) as
      | MatchkeyField
      | undefined;
    if (f === undefined) {
      throw new Error(
        `patch references field ${fname} in matchkey ${mk.name} which does not exist`,
      );
    }
    (f as { scorer: string }).scorer = String(patch["scorer"]);
  } else if (op === "add_negative_evidence") {
    applyAddNegativeEvidence(mks, String(patch["field"]));
  } else {
    throw new Error(`unknown patch op: ${op}`);
  }

  return next;
}

function findMatchkey(
  mks: readonly MatchkeyConfig[],
  name: string,
): MatchkeyConfig {
  const mk = mks.find((m) => m.name === name);
  if (mk === undefined) {
    throw new Error(
      `patch references matchkey ${name} which does not exist in the config`,
    );
  }
  return mk;
}

/**
 * Add a NegativeEvidenceField to the first weighted (else exact, else first)
 * matchkey. Idempotent; mirrors `_apply_add_negative_evidence` defaults
 * (scorer "ensemble", threshold 0.4, penalty 0.3).
 */
function applyAddNegativeEvidence(
  mks: readonly MatchkeyConfig[],
  fieldName: string,
): void {
  if (mks.length === 0) return;
  const target =
    mks.find((m) => m.type === "weighted") ??
    mks.find((m) => m.type === "exact") ??
    mks[0]!;

  const mutable = target as { negativeEvidence?: NegativeEvidenceField[] };
  if (mutable.negativeEvidence === undefined || mutable.negativeEvidence === null) {
    mutable.negativeEvidence = [];
  }
  if (mutable.negativeEvidence.some((ne) => ne.field === fieldName)) return;

  mutable.negativeEvidence.push({
    field: fieldName,
    transforms: [],
    scorer: "ensemble",
    threshold: 0.4,
    penalty: 0.3,
  });
}

/** Structured deep clone (Node 18+ / Workers / Deno global), JSON fallback. */
function deepClone<T>(value: T): T {
  if (typeof structuredClone === "function") return structuredClone(value);
  return JSON.parse(JSON.stringify(value)) as T;
}
