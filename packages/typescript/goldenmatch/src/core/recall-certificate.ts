/**
 * Unsupervised recall estimation for a multi-matchkey run (no ground truth).
 *
 * Edge-safe port of Python `goldenmatch/core/recall_certificate.py`
 * (`certify_recall_df` + `estimate_recall`). No `node:*`, no fs/db.
 *
 * In production you can estimate PRECISION cheaply (sample matches, check them)
 * but RECALL is normally unknowable without labels -- you can't sample the true
 * matches you didn't find. This estimates the recall of a multi-matchkey /
 * multi-pass run WITHOUT ground truth, via capture-recapture (the dual-system
 * math used for census undercount): each matchkey/pass is treated as a
 * decorrelated "system"; the overlap structure of which systems matched each
 * pair estimates how many true pairs every system missed -> the recall of the
 * run's union.
 *
 * CRITICAL: this is a POINT ESTIMATE treating each pass as a decorrelated
 * system -- it is NOT a supervised/ground-truth recall number. A trustworthy
 * *lower bound* additionally needs a small labelled audit of the sub-threshold
 * candidate stratum; it cannot be obtained from the capture data alone. The
 * `note` field carries this framing verbatim -- preserve it; do not relabel the
 * estimate as a measured recall.
 *
 * Method + assumptions:
 *   * >= 3 decorrelated systems (multi_pass blocking and/or multiple matchkeys).
 *   * FALSE POSITIVES are ~all singletons: a spurious match by one system is
 *     rarely reproduced by an independent one, so the multi-capture cells f_k
 *     (k>=2) are ~FP-free. We fit the true-pair capture model from those cells
 *     and IGNORE the FP-contaminated singleton cell.
 *   * Homogeneous capture probability across true pairs. Under the binomial
 *     capture model, recall of the union = 1 - (1-p)^K, with p fit from the
 *     slope of log f_k - log C(K,k) over k>=2. Heterogeneity makes the estimate
 *     mildly optimistic (flagged via the overlap diagnostic).
 */
import type { GoldenMatchConfig, MatchkeyConfig, DedupeResult } from "./types.js";
import { getMatchkeys } from "./types.js";
import { dedupe } from "./api.js";
import { autoConfigureRows } from "./autoconfig.js";
import type { Row } from "./types.js";

/** A within-cluster record pair, canonicalized `a < b`. */
export type Pair = readonly [number, number];

/**
 * Result of an unsupervised recall estimate. `recall` is null when the capture
 * structure can't support an estimate (see `note`). Mirrors Python
 * `RecallEstimate`.
 */
export interface RecallEstimate {
  /** Point estimate in [0,1], or null when not estimable. */
  recall: number | null;
  nSystems: number;
  foundPairs: number;
  /** Fitted p (per-system capture prob). */
  perSystemCaptureProb: number;
  /** Decorrelation diagnostic in [0,1]. */
  meanOverlap: number;
  captureHistogram: Record<number, number>;
  estimable: boolean;
  note: string;
}

/** Binomial coefficient C(n, k) via a product (exact for the small K here). */
function comb(n: number, k: number): number {
  if (k < 0 || k > n) return 0;
  const kk = Math.min(k, n - k);
  let num = 1;
  let den = 1;
  for (let i = 0; i < kk; i++) {
    num *= n - i;
    den *= i + 1;
  }
  return num / den;
}

/**
 * Fit p from the FP-free higher-order cells: regress log f_k - log C(K,k) on k
 * for k>=2; slope = logit(p). Returns null if <2 usable cells. Mirrors Python
 * `_fit_capture_prob`.
 */
function fitCaptureProb(counts: Map<string, number>, K: number): number | null {
  const ck = new Map<number, number>();
  for (let k = 1; k <= K; k++) ck.set(k, 0);
  for (const v of counts.values()) {
    if (v >= 1 && v <= K) ck.set(v, (ck.get(v) ?? 0) + 1);
  }
  const pts: Array<[number, number]> = [];
  for (let k = 2; k <= K; k++) {
    const c = ck.get(k) ?? 0;
    if (c > 0) pts.push([k, c]);
  }
  if (pts.length < 2) return null;

  const xs = pts.map(([k]) => k);
  const ys = pts.map(([k, c]) => Math.log(c) - Math.log(comb(K, k)));
  const n = xs.length;
  const mx = xs.reduce((a, b) => a + b, 0) / n;
  const my = ys.reduce((a, b) => a + b, 0) / n;
  let sxx = 0;
  for (const x of xs) sxx += (x - mx) ** 2;
  if (sxx === 0) return null;
  let sxy = 0;
  for (let i = 0; i < n; i++) sxy += (xs[i]! - mx) * (ys[i]! - my);
  const b = sxy / sxx;
  return 1.0 / (1.0 + Math.exp(-b));
}

function pairKey(p: Pair): string {
  return `${p[0]},${p[1]}`;
}

/**
 * Estimate the recall of the UNION of `pairsets`, where each set is the matches
 * found by one decorrelated system (matchkey/pass). No labels needed. Mirrors
 * Python `estimate_recall`.
 */
export function estimateRecall(pairsets: readonly ReadonlySet<string>[]): RecallEstimate {
  const K = pairsets.length;

  const union = new Set<string>();
  const counts = new Map<string, number>();
  for (const ps of pairsets) {
    for (const p of ps) {
      union.add(p);
      counts.set(p, (counts.get(p) ?? 0) + 1);
    }
  }

  const hist: Record<number, number> = {};
  for (let k = 1; k <= K; k++) hist[k] = 0;
  for (const v of counts.values()) {
    if (v >= 1 && v <= K) hist[v] = (hist[v] ?? 0) + 1;
  }

  const overlaps: number[] = [];
  for (let a = 0; a < K; a++) {
    for (let b = a + 1; b < K; b++) {
      const A = pairsets[a]!;
      const B = pairsets[b]!;
      if (A.size > 0 || B.size > 0) {
        let inter = 0;
        const [small, large] = A.size <= B.size ? [A, B] : [B, A];
        for (const x of small) if (large.has(x)) inter += 1;
        const uni = A.size + B.size - inter;
        overlaps.push(uni === 0 ? 0 : inter / uni);
      }
    }
  }
  const meanOverlap =
    overlaps.length > 0 ? overlaps.reduce((a, b) => a + b, 0) / overlaps.length : 0.0;

  if (K < 3) {
    return {
      recall: null,
      nSystems: K,
      foundPairs: union.size,
      perSystemCaptureProb: 0.0,
      meanOverlap,
      captureHistogram: hist,
      estimable: false,
      note:
        "need >=3 decorrelated systems (enable multi_pass blocking or use " +
        ">=3 matchkeys) to estimate recall",
    };
  }

  const p = fitCaptureProb(counts, K);
  if (p === null || !(p > 0.0 && p < 1.0)) {
    return {
      recall: null,
      nSystems: K,
      foundPairs: union.size,
      perSystemCaptureProb: p ?? 0.0,
      meanOverlap,
      captureHistogram: hist,
      estimable: false,
      note:
        "too few multi-captured pairs to estimate (systems too correlated, " +
        "or too few matches)",
    };
  }

  const recall = 1.0 - (1.0 - p) ** K;
  let note =
    "point estimate (no labels); a trustworthy lower bound needs a small " +
    "labelled audit";
  if (meanOverlap > 0.85) {
    note +=
      "; WARNING: high system overlap -> systems correlated, estimate may be optimistic";
  }

  return {
    recall,
    nSystems: K,
    foundPairs: union.size,
    perSystemCaptureProb: p,
    meanOverlap,
    captureHistogram: hist,
    estimable: true,
    note,
  };
}

/**
 * K decorrelated systems from a config's matchkeys/passes. If <3 matchkeys,
 * split a multi-field matchkey into per-field systems (each field a pass).
 * Mirrors Python `build_decorrelated_systems`.
 */
export function buildDecorrelatedSystems(
  matchkeys: readonly MatchkeyConfig[],
): MatchkeyConfig[][] {
  let systems: MatchkeyConfig[][] = matchkeys.map((mk) => [mk]);
  if (systems.length < 3) {
    for (const mk of matchkeys) {
      const flds = mk.fields ?? [];
      if (flds.length >= 3) {
        systems = flds.map((f, i) => [
          { ...mk, fields: [f], name: `${mk.name}__f${i}` } as MatchkeyConfig,
        ]);
        break;
      }
    }
  }
  return systems;
}

/**
 * Convert a dedupe `clusters` map (cluster -> members) to the set of
 * within-cluster (row_id, row_id) pairs, canonicalized `a < b` and keyed as
 * "a,b" strings. Mirrors Python `clusters_to_pairs`.
 */
export function clustersToPairs(clusters: DedupeResult["clusters"]): Set<string> {
  const out = new Set<string>();
  for (const cl of clusters.values()) {
    const ms = [...cl.members].map((m) => Math.trunc(m)).sort((a, b) => a - b);
    for (let i = 0; i < ms.length; i++) {
      for (let j = i + 1; j < ms.length; j++) {
        out.add(`${ms[i]},${ms[j]}`);
      }
    }
  }
  return out;
}

/**
 * Unsupervised recall estimate for a set of rows (no ground truth). Runs each
 * matchkey/pass as a decorrelated system through the real pipeline and applies
 * the FP-aware capture-recapture estimator. Mirrors Python `certify_recall_df`.
 */
export async function certifyRecallRows(
  rows: readonly Row[],
  config?: GoldenMatchConfig,
): Promise<RecallEstimate> {
  const cfg = config ?? autoConfigureRows(rows);
  const systems = buildDecorrelatedSystems(getMatchkeys(cfg));
  const pairsets: Array<Set<string>> = [];
  for (const sysMks of systems) {
    const sub: GoldenMatchConfig = { ...cfg, matchkeys: sysMks };
    const res = await dedupe(rows, { config: sub });
    pairsets.push(clustersToPairs(res.clusters));
  }
  return estimateRecall(pairsets);
}

/**
 * MCP/wire response shape for `certify_recall` -- matches the Python handler
 * `{estimated_recall, n_systems, found_pairs, system_overlap, estimable, note}`.
 * `note` carries the lower-bound / capture-recapture framing verbatim.
 */
export interface CertifyRecallResponse {
  estimated_recall: number | null;
  n_systems: number;
  found_pairs: number;
  system_overlap: number;
  estimable: boolean;
  note: string;
}

/** Serialize a `RecallEstimate` to the Python-parity MCP response shape. */
export function toCertifyRecallResponse(est: RecallEstimate): CertifyRecallResponse {
  return {
    estimated_recall: est.recall,
    n_systems: est.nSystems,
    found_pairs: est.foundPairs,
    system_overlap: Math.round(est.meanOverlap * 1000) / 1000,
    estimable: est.estimable,
    note: est.note,
  };
}
