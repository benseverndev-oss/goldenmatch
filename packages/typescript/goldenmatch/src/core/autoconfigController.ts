/**
 * autoconfigController.ts — Iterative auto-config controller.
 *
 * Port of Python ``goldenmatch/core/autoconfig_controller.py`` (v1.7/v1.8).
 *
 * Differences from Python by design (per the wave-1 plan):
 *
 * - **Async return.** The TS pipeline is async, so ``run()`` returns a Promise.
 *   The Python pipeline is sync.
 * - **No polars / no ProfileEmitter.** We assemble a ``ComplexityProfile`` from
 *   plain row arrays plus the synchronous ``runDedupePipeline`` outputs
 *   (clusters, scoredPairs). Sub-profile fields not derivable from those
 *   outputs are filled with healthy defaults rather than fabricated.
 * - **No cross-run AutoConfigMemory** (deferred to a later wave).
 * - **No LLM-scorer decoration** (deferred).
 *
 * Edge-safe: no `node:` imports.
 */

import type { GoldenMatchConfig, Row, ScoredPair } from "./types.js";
import {
  type ComplexityProfile,
  type StopReason as StopReasonType,
  HealthVerdict,
  StopReason,
  complexityHealth,
  computeDataProfile,
  makeBlockingProfile,
  makeClusterProfile,
  makeComplexityProfile,
  makeDataProfile,
  makeProfileMeta,
  makeScoringProfile,
} from "./complexityProfile.js";
import {
  RunHistory,
  RED_PROFILE,
  type HistoryEntry,
} from "./autoconfigHistory.js";
import type { RefitPolicy } from "./autoconfigPolicy.js";
import { autoConfigureRows } from "./autoconfig.js";
import { runDedupePipeline } from "./pipeline.js";
import { IndicatorContext } from "./indicators.js";
import { promoteNegativeEvidence } from "./autoconfigNegativeEvidence.js";
import { computeColumnPriors } from "./indicators.js";

// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

export interface ControllerBudget {
  readonly maxIterations: number;
  readonly maxSeconds: number;
  readonly sampleSizeDefault: number;
  readonly sampleSkipBelow: number;
  readonly convergeEpsilon: number;
  readonly driftThreshold: number;
}

export function makeControllerBudget(p: Partial<ControllerBudget> = {}): ControllerBudget {
  return {
    maxIterations: p.maxIterations ?? 3,
    maxSeconds: p.maxSeconds ?? 30.0,
    sampleSizeDefault: p.sampleSizeDefault ?? 2000,
    sampleSkipBelow: p.sampleSkipBelow ?? 5000,
    convergeEpsilon: p.convergeEpsilon ?? 0.05,
    driftThreshold: p.driftThreshold ?? 0.30,
  };
}

export interface ControllerOptions {
  readonly policy: RefitPolicy;
  readonly budget?: ControllerBudget;
}

export interface ControllerRunResult {
  readonly committedConfig: GoldenMatchConfig;
  readonly profile: ComplexityProfile;
  readonly history: RunHistory;
}

export class ConfigValidationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ConfigValidationError";
  }
}

// ---------------------------------------------------------------------------
// Last-run cache (mirrors Python ``_LAST_CONTROLLER_RUN`` ContextVar)
// ---------------------------------------------------------------------------

let _lastControllerRun: RunHistory | null = null;
export function getLastControllerRun(): RunHistory | null {
  return _lastControllerRun;
}
/** Test-only: clear the module-level cache. Internal API. */
export function _resetLastControllerRun(): void {
  _lastControllerRun = null;
}

// ---------------------------------------------------------------------------
// Controller
// ---------------------------------------------------------------------------

export class AutoConfigController {
  readonly policy: RefitPolicy;
  readonly budget: ControllerBudget;

  constructor(options: ControllerOptions) {
    this.policy = options.policy;
    this.budget = options.budget ?? makeControllerBudget();
  }

  async run(rows: readonly Row[]): Promise<ControllerRunResult> {
    // ---- Pathological gates ---------------------------------------------
    if (rows.length === 0) {
      throw new ConfigValidationError("no data to configure on");
    }
    const firstRow = rows[0] as Record<string, unknown>;
    const userCols = Object.keys(firstRow).filter((c) => !c.startsWith("__"));
    if (userCols.length === 0) {
      throw new ConfigValidationError("no usable columns");
    }
    let allNull = true;
    for (const col of userCols) {
      for (const r of rows) {
        const v = (r as Record<string, unknown>)[col];
        if (v !== null && v !== undefined && v !== "") {
          allNull = false;
          break;
        }
      }
      if (!allNull) break;
    }
    if (allNull) {
      throw new ConfigValidationError("no usable columns (all values null)");
    }

    // Short-circuit: single row or single user column → v0 + YELLOW sentinel
    if (rows.length === 1 || userCols.length === 1) {
      const v0 = autoConfigureRows(rows);
      const yellowProfile = this._yellowSentinelProfile(rows.length, userCols);
      const history = new RunHistory();
      _lastControllerRun = history;
      return { committedConfig: v0, profile: yellowProfile, history };
    }

    // ---- Iteration loop -------------------------------------------------
    let configV0 = autoConfigureRows(rows);
    // v1.11/v1.12: eager NE promotion runs once on the FULL df (not the
    // sample) before iteration, mirroring Python's
    // ``auto_configure_df`` pre-iteration pass. Walks both weighted and
    // exact matchkeys; threshold=None exact MKs get threshold=0.5 when NE
    // is added so Path Y activates.
    try {
      const priorsFull = computeColumnPriors(rows);
      configV0 = promoteNegativeEvidence(configV0, rows, priorsFull);
    } catch {
      // Defensive: if priors fail (e.g. degenerate data), continue with v0.
    }
    const sample = this._takeSample(rows);
    const history = new RunHistory();
    let configN: GoldenMatchConfig = configV0;
    const start = Date.now();

    for (let iteration = 0; iteration <= this.budget.maxIterations; iteration++) {
      const elapsedSec = (Date.now() - start) / 1000;
      if (elapsedSec > this.budget.maxSeconds && iteration > 0) {
        history.stopReason = StopReason.BUDGET_TIME;
        break;
      }
      const iterStart = Date.now();
      let profileN: ComplexityProfile;
      try {
        profileN = await this._assembleProfile(sample, configN, iteration);
      } catch (err) {
        const entry: HistoryEntry = {
          iteration,
          config: configN,
          profile: RED_PROFILE,
          decision: null,
          error: {
            exceptionType: (err as Error).name || "Error",
            tracebackSummary: ((err as Error).stack ?? String(err)).slice(0, 2000),
          },
          wallClockMs: Date.now() - iterStart,
        };
        history.append(entry);
        continue;
      }
      const entry: HistoryEntry = {
        iteration,
        config: configN,
        profile: profileN,
        decision: null,
        error: null,
        wallClockMs: Date.now() - iterStart,
      };
      history.append(entry);

      // Stop check: GREEN
      if (complexityHealth(profileN) === HealthVerdict.GREEN) {
        history.stopReason = StopReason.GREEN;
        break;
      }
      // Convergence guard (only when prior didn't fire a rule)
      if (history.profileDistanceToPrev() < this.budget.convergeEpsilon) {
        const prev =
          history.entries.length >= 2
            ? history.entries[history.entries.length - 2]
            : null;
        if (prev === null || prev === undefined || prev.decision === null) {
          history.stopReason = StopReason.CONVERGED;
          break;
        }
      }
      if (history.isOscillating()) {
        history.stopReason = StopReason.OSCILLATING;
        break;
      }

      // Provision a fresh IndicatorContext for this iteration. Cheap eager
      // indicators (column priors, sparsity) are memoized; the lazy ones
      // remain lazy until a rule reads them.
      const indicators = new IndicatorContext(sample, configN);
      // Ask the policy for the next config
      const next = this.policy.propose(profileN, configN, history, indicators);
      if (next === null) {
        history.stopReason = StopReason.POLICY_SATISFIED;
        break;
      }
      if (this._configsEqual(next, configN)) {
        history.stopReason = StopReason.POLICY_NO_PROGRESS;
        break;
      }
      configN = next;
    }

    history.elapsedMs = Date.now() - start;
    if (history.stopReason === null) {
      history.stopReason = StopReason.BUDGET_ITERATIONS;
    }

    // Append virtual v0 entry (iteration=-1) so pickCommitted can fall back
    // when every real iteration is worse than the v0 starting point.
    const v0Entry = await this._assembleV0Entry(sample, configV0, history);
    if (v0Entry !== null) history.append(v0Entry);

    const bestEntry = history.pickCommitted(0.9);
    if (bestEntry === null) {
      _lastControllerRun = history;
      return { committedConfig: configV0, profile: RED_PROFILE, history };
    }
    _lastControllerRun = history;
    return {
      committedConfig: bestEntry.config,
      profile: bestEntry.profile,
      history,
    };
  }

  // -------------------------------------------------------------------------
  // Internals
  // -------------------------------------------------------------------------

  private _yellowSentinelProfile(nRows: number, userCols: readonly string[]): ComplexityProfile {
    return makeComplexityProfile({
      data: makeDataProfile({
        nRows,
        nCols: userCols.length,
        columnTypes: Object.fromEntries(userCols.map((c) => [c, "unknown" as const])),
      }),
      blocking: makeBlockingProfile({
        nBlocks: Math.max(nRows, 1),
        totalComparisons: Math.max(nRows, 1),
        reductionRatio: 0.9,
        blockSizesP50: 1,
        blockSizesP95: 1,
        blockSizesP99: 1,
        blockSizesMax: 1,
        singletonBlockCount: 0,
        oversizedBlockCount: 0,
      }),
      scoring: makeScoringProfile({
        nPairsScored: 0,
        candidatesCompared: 1,
        dipStatistic: 0.01,
        massAboveThreshold: 0.01,
        massInBorderline: 0.0,
      }),
    });
  }

  private _takeSample(rows: readonly Row[]): readonly Row[] {
    if (rows.length < this.budget.sampleSkipBelow) return rows;
    const seed = this._seedFor(rows);
    const n = Math.min(this.budget.sampleSizeDefault, rows.length);
    // Deterministic Fisher-Yates-lite based on a fast hash-derived rng.
    const arr = rows.slice();
    const rand = mulberry32(seed);
    for (let i = arr.length - 1; i > 0; i--) {
      const j = Math.floor(rand() * (i + 1));
      const tmp = arr[i]!;
      arr[i] = arr[j]!;
      arr[j] = tmp;
    }
    return arr.slice(0, n);
  }

  private _seedFor(rows: readonly Row[]): number {
    if (rows.length === 0) return 0;
    const cols = Object.keys(rows[0] as object).join(",");
    const key = `${rows.length}|${cols}`;
    let h = 0x811c9dc5;
    for (let i = 0; i < key.length; i++) {
      h ^= key.charCodeAt(i);
      h = Math.imul(h, 0x01000193);
    }
    return h >>> 0;
  }

  private _configsEqual(a: GoldenMatchConfig, b: GoldenMatchConfig): boolean {
    return JSON.stringify(a) === JSON.stringify(b);
  }

  /**
   * Run a sample through the dedupe pipeline and synthesize a
   * ``ComplexityProfile``. Unlike Python this does not use a ProfileEmitter
   * — we derive sub-profile fields directly from the dedupe result.
   */
  private async _assembleProfile(
    sample: readonly Row[],
    config: GoldenMatchConfig,
    iteration: number,
  ): Promise<ComplexityProfile> {
    const data = computeDataProfile(sample);
    let scoring = makeScoringProfile();
    let blocking = makeBlockingProfile();
    let cluster = makeClusterProfile();
    try {
      const result = await runDedupePipeline(sample, config);
      scoring = this._scoringFromPairs(result.scoredPairs, config);
      blocking = this._blockingFromResult(sample.length, result.scoredPairs);
      cluster = this._clusterFromResult(result.clusters);
    } catch (err) {
      // Re-throw so the controller records an error entry.
      throw err;
    }
    return makeComplexityProfile({
      data,
      blocking,
      scoring,
      cluster,
      meta: makeProfileMeta({
        iteration,
        isSample: true,
        sampleSize: sample.length,
        nRowsFull: sample.length,
      }),
    });
  }

  private _scoringFromPairs(
    pairs: readonly ScoredPair[],
    config: GoldenMatchConfig,
  ): ReturnType<typeof makeScoringProfile> {
    if (pairs.length === 0) {
      return makeScoringProfile({
        // candidatesCompared==0 keeps this RED, matching Python's "nothing happened" path.
        candidatesCompared: 0,
        nPairsScored: 0,
        massAboveThreshold: 0,
      });
    }
    // Pull threshold from first weighted mk if any; default 0.85.
    let threshold = 0.85;
    for (const mk of config.matchkeys ?? []) {
      if (mk.type === "weighted") {
        threshold = mk.threshold;
        break;
      }
    }
    const histogram = new Array(20).fill(0);
    let above = 0;
    let borderline = 0;
    for (const p of pairs) {
      const idx = Math.min(19, Math.max(0, Math.floor(p.score * 20)));
      histogram[idx] = (histogram[idx] as number) + 1;
      if (p.score >= threshold) above += 1;
      if (Math.abs(p.score - threshold) <= 0.1) borderline += 1;
    }
    const total = pairs.length;
    // Crude dip statistic: variance of histogram counts. Healthy
    // distributions are spread enough to clear 0.01.
    let mean = 0;
    for (const c of histogram) mean += c as number;
    mean /= histogram.length;
    let variance = 0;
    for (const c of histogram) variance += ((c as number) - mean) ** 2;
    const dipStat = variance > 0 ? Math.min(0.1, variance / (total * total + 1)) : 0;

    return makeScoringProfile({
      candidatesCompared: total,
      nPairsScored: total,
      scoreHistogram: histogram,
      dipStatistic: dipStat,
      massAboveThreshold: above / total,
      massInBorderline: borderline / total,
    });
  }

  private _blockingFromResult(
    nRows: number,
    pairs: readonly ScoredPair[],
  ): ReturnType<typeof makeBlockingProfile> {
    // We don't have direct access to block stats from runDedupePipeline.
    // Use pair count as a coarse proxy for total comparisons; reductionRatio
    // estimates how many of the N*(N-1)/2 possible pairs were excluded.
    const possible = (nRows * (nRows - 1)) / 2;
    const totalCmp = Math.max(1, pairs.length);
    const reduction = possible > 0 ? 1 - totalCmp / possible : 0;
    return makeBlockingProfile({
      nBlocks: Math.max(1, Math.floor(nRows / Math.max(1, totalCmp))),
      totalComparisons: totalCmp,
      reductionRatio: Math.max(0, Math.min(1, reduction)),
      blockSizesP50: 1,
      blockSizesP95: 1,
      blockSizesP99: 1,
      blockSizesMax: 1,
      singletonBlockCount: 0,
      oversizedBlockCount: 0,
    });
  }

  private _clusterFromResult(
    clusters: ReadonlyMap<number, { members: readonly number[]; size: number }>,
  ): ReturnType<typeof makeClusterProfile> {
    const sizes: number[] = [];
    let max = 0;
    for (const c of clusters.values()) {
      sizes.push(c.size);
      if (c.size > max) max = c.size;
    }
    sizes.sort((a, b) => a - b);
    const p50 = sizes[Math.floor(sizes.length / 2)] ?? 0;
    const p99 = sizes[Math.max(0, Math.floor(0.99 * sizes.length) - 1)] ?? 0;
    return makeClusterProfile({
      nClusters: sizes.length,
      clusterSizeP50: p50,
      clusterSizeP99: p99,
      clusterSizeMax: max,
      transitivityRate: 1.0,  // tracked at union-find level; approximate as healthy
      oversizedClusterCount: 0,
    });
  }

  private async _assembleV0Entry(
    sample: readonly Row[],
    configV0: GoldenMatchConfig,
    history: RunHistory,
  ): Promise<HistoryEntry | null> {
    // Re-stamp iter-0 if it already profiled v0.
    if (
      history.entries.length > 0 &&
      history.entries[0]!.error === null &&
      this._configsEqual(history.entries[0]!.config, configV0)
    ) {
      const e0 = history.entries[0]!;
      return {
        iteration: -1,
        config: e0.config,
        profile: e0.profile,
        decision: null,
        error: null,
        wallClockMs: e0.wallClockMs,
      };
    }
    // Slow path: run the v0 config through the sample pipeline.
    const start = Date.now();
    try {
      const profile = await this._assembleProfile(sample, configV0, -1);
      return {
        iteration: -1,
        config: configV0,
        profile,
        decision: null,
        error: null,
        wallClockMs: Date.now() - start,
      };
    } catch {
      return null;
    }
  }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Mulberry32 PRNG — pure-JS, no deps, deterministic per seed. */
function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// Re-export StopReason for convenience.
export type { StopReasonType };
