/**
 * config-optimizer.ts -- agentic config search over the ConfigEdit vocabulary.
 *
 * Port of Python `goldenmatch/core/config_optimizer.py`'s deterministic core:
 * `GridProposer` (single-round threshold sweep), `CoordinateDescentProposer`
 * (one lever family per round off the best config so far), and the
 * `optimizeConfig` loop (fingerprint dedup, max-rounds/max-trials caps,
 * ties resolve toward the warm-start baseline).
 *
 * Objectives: `"f1"` (run dedupe per trial, score vs ground-truth pairs --
 * fixture-verified against Python) and `"custom"` (caller-supplied scoreFn).
 * Python's `"confidence"` objective reads the controller's zero-label
 * ComplexityProfile, which the TS port does not carry yet -- DEFERRED
 * (documented), supply `groundTruth` or a `scoreFn` instead. `LLMProposer`
 * is likewise not ported (env-gated LLM territory); pass a custom `Proposer`.
 *
 * Scorer-family note: the default candidate set now includes `qgram` (a char
 * q-gram Jaccard scorer, ported to TS), matching Python's default sweep. Parity
 * fixtures still pin an explicit scorer tuple (without `qgram`) on both sides as
 * a determinism clamp, so the fixture is unaffected.
 */
import { dedupe } from "./api.js";
import {
  BlockingKeyEdit,
  BlockingStrategyEdit,
  MatchkeyTypeSwap,
  ScorerSwap,
  ThresholdShift,
  WeightShift,
  type ConfigEdit,
} from "./config-edits.js";
import { evaluateClusters } from "./evaluate.js";
import { getMatchkeys } from "./types.js";
import type { GoldenMatchConfig, MatchkeyConfig, Row } from "./types.js";

export const DEFAULT_THRESHOLD_OFFSETS: readonly number[] = [
  -0.1, -0.05, 0.0, 0.05, 0.1,
];

const PERTURBABLE_TYPES = new Set<string>(["weighted", "probabilistic"]);

export interface OptimizerTrial {
  readonly label: string;
  readonly config: GoldenMatchConfig;
  readonly objective: string;
  readonly score: number;
  readonly reasons: readonly string[];
  readonly error: string | null;
}

export interface OptimizeResult {
  readonly bestConfig: GoldenMatchConfig;
  readonly bestTrial: OptimizerTrial;
  readonly trials: readonly OptimizerTrial[];
  readonly objective: string;
  readonly sampleSize: number;
  readonly rounds: number;
  readonly proposer: string;
}

export interface SearchState {
  readonly baseConfig: GoldenMatchConfig;
  readonly objective: string;
  trials: OptimizerTrial[];
  round: number;
}

/** Highest score wins; full ties resolve toward the warm-start baseline. */
export function bestValidTrial(trials: readonly OptimizerTrial[]): OptimizerTrial | null {
  let best: OptimizerTrial | null = null;
  for (const t of trials) {
    if (t.error !== null) continue;
    if (best === null) {
      best = t;
      continue;
    }
    const tBase = t.label === "baseline" ? 1 : 0;
    const bBase = best.label === "baseline" ? 1 : 0;
    if (t.score > best.score || (t.score === best.score && tBase > bBase)) {
      best = t;
    }
  }
  return best;
}

function clamp(x: number, lo = 0.0, hi = 1.0): number {
  return Math.max(lo, Math.min(hi, x));
}

function thresholdOf(mk: MatchkeyConfig): number | undefined {
  return (mk as { threshold?: number }).threshold;
}

function perturbableMatchkeys(config: GoldenMatchConfig): MatchkeyConfig[] {
  return getMatchkeys(config).filter(
    (mk) => PERTURBABLE_TYPES.has(mk.type) && thresholdOf(mk) !== undefined,
  );
}

function withMatchkeys(
  config: GoldenMatchConfig,
  mks: readonly MatchkeyConfig[],
): GoldenMatchConfig {
  if (config.matchkeys !== undefined) return { ...config, matchkeys: mks };
  return { ...config, matchSettings: mks };
}

function offsetLabel(off: number): string {
  if (off === 0.0) return "baseline";
  const sign = off >= 0 ? "+" : "";
  return `threshold${sign}${off.toFixed(2)}`;
}

/**
 * Config variants with every perturbable matchkey threshold shifted by each
 * offset (clamped). Variants whose thresholds collapse to the same clamped
 * values are de-duplicated; exact-only configs return just the baseline.
 */
export function thresholdVariants(
  base: GoldenMatchConfig,
  offsets: readonly number[],
): Array<[string, GoldenMatchConfig]> {
  if (perturbableMatchkeys(base).length === 0) return [["baseline", base]];

  const variants: Array<[string, GoldenMatchConfig]> = [];
  const seen = new Set<string>();
  for (const off of offsets) {
    const thresholds: number[] = [];
    const mks = getMatchkeys(base).map((mk) => {
      const t = thresholdOf(mk);
      if (PERTURBABLE_TYPES.has(mk.type) && t !== undefined) {
        const nt = clamp(t + off);
        thresholds.push(nt);
        return { ...mk, threshold: nt } as MatchkeyConfig;
      }
      return mk;
    });
    const key = thresholds.map((t) => t.toFixed(6)).join(",");
    if (seen.has(key)) continue;
    seen.add(key);
    variants.push([offsetLabel(off), withMatchkeys(base, mks)]);
  }
  return variants;
}

export interface Proposer {
  readonly singleRound: boolean;
  propose(state: SearchState): Array<[string, GoldenMatchConfig]>;
}

/** Deterministic threshold sweep -- single round. */
export class GridProposer implements Proposer {
  readonly singleRound = true;

  constructor(
    private readonly offsets: readonly number[] = DEFAULT_THRESHOLD_OFFSETS,
  ) {}

  propose(state: SearchState): Array<[string, GoldenMatchConfig]> {
    if (state.round > 0) return [];
    return thresholdVariants(state.baseConfig, this.offsets);
  }
}

export interface CoordinateDescentOptions {
  readonly offsets?: readonly number[];
  readonly scorers?: readonly string[];
  readonly weightDeltas?: readonly number[];
  readonly blockingStrategies?: readonly string[];
  readonly blockingKeyAdds?: readonly (readonly string[])[];
}

/**
 * Deterministic multi-lever search: each round optimizes ONE lever family off
 * the best config so far -- thresholds, per-field scorer, per-field weight
 * (multi-field weighted matchkeys only), matchkey type, blocking strategy,
 * then candidate blocking keys. Skips empty families; returns [] when all are
 * exhausted. Default scorer candidates match Python's sweep (incl. `qgram`).
 */
export class CoordinateDescentProposer implements Proposer {
  readonly singleRound = false;
  private static readonly FAMILIES = [
    "threshold",
    "scorer",
    "weight",
    "mktype",
    "blocking",
    "blocking_key",
  ] as const;

  private readonly offsets: readonly number[];
  private readonly scorers: readonly string[];
  private readonly weightDeltas: readonly number[];
  private readonly blockingStrategies: readonly string[];
  private readonly blockingKeyAdds: readonly (readonly string[])[];
  private famIdx = 0;

  constructor(opts: CoordinateDescentOptions = {}) {
    this.offsets = opts.offsets ?? DEFAULT_THRESHOLD_OFFSETS;
    this.scorers = opts.scorers ?? [
      "token_sort",
      "ensemble",
      "levenshtein",
      "soundex_match",
      "qgram",
    ];
    this.weightDeltas = opts.weightDeltas ?? [-0.5, 0.5];
    this.blockingStrategies = opts.blockingStrategies ?? ["multi_pass"];
    this.blockingKeyAdds = opts.blockingKeyAdds ?? [];
  }

  private edits(family: string, base: GoldenMatchConfig): ConfigEdit[] {
    if (family === "threshold") {
      return this.offsets.map((o) => new ThresholdShift(o));
    }
    if (family === "scorer") {
      const out: ConfigEdit[] = [];
      for (const mk of perturbableMatchkeys(base)) {
        for (const f of mk.fields) {
          for (const sc of this.scorers) {
            if (f.scorer !== sc) out.push(new ScorerSwap(mk.name, f.field, sc));
          }
        }
      }
      return out;
    }
    if (family === "weight") {
      const out: ConfigEdit[] = [];
      for (const mk of getMatchkeys(base)) {
        if (mk.type !== "weighted" || mk.fields.length < 2) continue;
        for (const f of mk.fields) {
          for (const d of this.weightDeltas) {
            out.push(new WeightShift(mk.name, f.field, d));
          }
        }
      }
      return out;
    }
    if (family === "mktype") {
      const out: ConfigEdit[] = [];
      for (const mk of getMatchkeys(base)) {
        if (mk.type === "weighted") {
          out.push(new MatchkeyTypeSwap(mk.name, "probabilistic"));
        }
      }
      return out;
    }
    if (family === "blocking") {
      return this.blockingStrategies.map((s) => new BlockingStrategyEdit(s));
    }
    if (family === "blocking_key") {
      return this.blockingKeyAdds.map((f) => new BlockingKeyEdit("add", f));
    }
    return [];
  }

  propose(state: SearchState): Array<[string, GoldenMatchConfig]> {
    const best = bestValidTrial(state.trials);
    const base = best !== null ? best.config : state.baseConfig;
    while (this.famIdx < CoordinateDescentProposer.FAMILIES.length) {
      const family = CoordinateDescentProposer.FAMILIES[this.famIdx]!;
      this.famIdx += 1;
      const out: Array<[string, GoldenMatchConfig]> = [];
      for (const edit of this.edits(family, base)) {
        const cfg = edit.apply(base);
        if (cfg !== null) out.push([edit.label, cfg]);
      }
      if (out.length > 0) return out;
    }
    return [];
  }
}

function resolveProposer(
  proposer: string | Proposer,
  offsets: readonly number[],
): Proposer {
  if (typeof proposer !== "string") return proposer;
  const key = proposer.toLowerCase();
  if (key === "grid") return new GridProposer(offsets);
  if (key === "coordinate" || key === "coordinate_descent") {
    return new CoordinateDescentProposer({ offsets });
  }
  throw new Error(
    `unknown proposer '${proposer}'; use 'grid', 'coordinate', or a Proposer ` +
      "instance (LLMProposer is not ported -- pass a custom Proposer)",
  );
}

export type ScoreFn = (
  label: string,
  config: GoldenMatchConfig,
) => Promise<{ score: number; reasons?: readonly string[] }>;

async function scoreF1(
  config: GoldenMatchConfig,
  rows: readonly Row[],
  groundTruth: readonly (readonly [number, number])[],
): Promise<{ score: number; reasons: readonly string[] }> {
  const result = await dedupe(rows, { config });
  const allIds = rows.map((_, i) => i);
  const ev = evaluateClusters(result.clusters, groundTruth, allIds);
  return {
    score: ev.f1,
    reasons: [
      `P=${ev.precision.toFixed(3)} R=${ev.recall.toFixed(3)} ` +
        `(tp=${ev.truePositives} fp=${ev.falsePositives} fn=${ev.falseNegatives})`,
    ],
  };
}

function fingerprint(cfg: GoldenMatchConfig): string {
  try {
    return JSON.stringify(cfg);
  } catch {
    return String(cfg);
  }
}

export interface OptimizeOptions {
  readonly baseConfig: GoldenMatchConfig;
  readonly groundTruth?: readonly (readonly [number, number])[];
  readonly objective?: "auto" | "f1" | "custom";
  readonly scoreFn?: ScoreFn;
  readonly proposer?: string | Proposer;
  readonly thresholdOffsets?: readonly number[];
  readonly maxRounds?: number;
  readonly maxTrials?: number;
}

/**
 * Search the candidate-config space and return the best config + every scored
 * trial. Mirrors Python `optimize_config` for the f1 objective; the
 * `confidence` objective (zero-label profile) is not ported -- pass
 * `groundTruth` (f1) or a custom `scoreFn`.
 */
export async function optimizeConfig(
  rows: readonly Row[],
  opts: OptimizeOptions,
): Promise<OptimizeResult> {
  const {
    baseConfig,
    groundTruth,
    scoreFn,
    proposer = "grid",
    thresholdOffsets = DEFAULT_THRESHOLD_OFFSETS,
    maxRounds = 6,
    maxTrials,
  } = opts;

  let objective = opts.objective ?? "auto";
  if (objective === "auto") {
    if (groundTruth !== undefined) objective = "f1";
    else if (scoreFn !== undefined) objective = "custom";
    else {
      throw new Error(
        "optimizeConfig: the 'confidence' objective is not ported to TS; " +
          "supply groundTruth (f1) or a custom scoreFn",
      );
    }
  }
  if (objective === "f1" && groundTruth === undefined) {
    throw new Error("objective='f1' requires groundTruth pairs");
  }
  if (objective === "custom" && scoreFn === undefined) {
    throw new Error("objective='custom' requires scoreFn");
  }

  const prop = resolveProposer(proposer, thresholdOffsets);
  const proposerName =
    typeof proposer === "string" ? proposer : prop.constructor.name;

  const state: SearchState = {
    baseConfig,
    objective,
    trials: [],
    round: 0,
  };
  const seen = new Set<string>();

  while (state.round < maxRounds) {
    const candidates = prop.propose(state);
    if (candidates.length === 0) break;
    for (const [label, cfg] of candidates) {
      const fp = fingerprint(cfg);
      if (seen.has(fp)) continue;
      seen.add(fp);
      let trial: OptimizerTrial;
      try {
        const scored =
          objective === "f1"
            ? await scoreF1(cfg, rows, groundTruth!)
            : await scoreFn!(label, cfg);
        trial = {
          label,
          config: cfg,
          objective,
          score: scored.score,
          reasons: scored.reasons ?? [],
          error: null,
        };
      } catch (exc) {
        trial = {
          label,
          config: cfg,
          objective,
          score: Number.NEGATIVE_INFINITY,
          reasons: [],
          error: String(exc),
        };
      }
      state.trials.push(trial);
      if (maxTrials !== undefined && state.trials.length >= maxTrials) break;
    }
    state.round += 1;
    if (maxTrials !== undefined && state.trials.length >= maxTrials) break;
    if (prop.singleRound) break;
  }

  const best = bestValidTrial(state.trials) ?? state.trials[0]!;
  return {
    bestConfig: best.config,
    bestTrial: best,
    trials: state.trials,
    objective,
    sampleSize: rows.length,
    rounds: state.round,
    proposer: proposerName,
  };
}

/** Human-readable trial table; mirrors Python OptimizeResult.report(). */
export function formatOptimizeReport(result: OptimizeResult): string {
  const lines = [
    `ConfigOptimizer - objective=${result.objective}, proposer=${result.proposer}, ` +
      `rounds=${result.rounds}, scored_on=${result.sampleSize} rows, ` +
      `${result.trials.length} trial(s)`,
  ];
  const ranked = [...result.trials].sort((a, b) => b.score - a.score);
  for (const t of ranked) {
    const marker = t === result.bestTrial ? "*" : " ";
    if (t.error !== null) {
      lines.push(` ${marker} ${t.label.padEnd(16)} ERROR: ${t.error}`);
    } else {
      const detail = t.reasons.length > 0 ? `; ${t.reasons.join(", ")}` : "";
      lines.push(
        ` ${marker} ${t.label.padEnd(16)} ${result.objective}=${t.score.toFixed(4)}${detail}`,
      );
    }
  }
  return lines.join("\n");
}
