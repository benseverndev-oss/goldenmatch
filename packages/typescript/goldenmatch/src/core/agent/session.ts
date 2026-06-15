/**
 * session.ts — AgentSession orchestrator (core, edge-safe).
 * Edge-safe: no Node.js imports. Ported from goldenmatch/core/agent.py.
 *
 * Operates on `Row[]` (Python operates on a Polars DataFrame). Delegates to
 * the existing TS core primitives: dedupe / match / autoConfigureRowsIterate /
 * gatePairs / ReviewQueue / evaluateClusters.
 */

import type {
  Row,
  GoldenMatchConfig,
  DedupeResult,
  MatchResult,
} from "../types.js";
import { ReviewQueue, gatePairs } from "../review-queue.js";
import { dedupe, match } from "../api.js";
import { autoConfigureRowsIterate } from "../autoconfig.js";
import { evaluateClusters } from "../evaluate.js";
import type { RunHistory } from "../autoconfigHistory.js";
import {
  profileForAgent,
  selectStrategy,
  buildAlternatives,
  decisionToConfig,
} from "./strategy.js";
import type {
  StrategyDecision,
  AnalyzeResult,
  AnalyzeFieldProfile,
  Telemetry,
  AutoconfigResult,
  DeduplicateResult,
  MatchSourcesResult,
  CompareResult,
  StrategyMetrics,
} from "./types.js";

/** Round to n decimal places (half-up). Matches the test expectations and is
 *  close enough to Python's round() for the rounded reasoning payload; the
 *  parity emitter is the authoritative judge for fixtures. */
function round(value: number, digits: number): number {
  const factor = 10 ** digits;
  return Math.round(value * factor) / factor;
}

/**
 * Build the minimal cross-surface telemetry blob from a controller RunHistory
 * (mirrors Python `_capture_telemetry`'s fallback path: serialize_telemetry is
 * NOT ported here). RunHistory exposes `stopReason` (a string-valued enum), so
 * we surface it as `stop_reason` when present. It does NOT expose a `health`
 * field directly (health lives on the per-entry ComplexityProfile), so the
 * optional `health` key is omitted in Wave 1.
 */
function captureTelemetry(history: RunHistory, source: string): Telemetry {
  const stopReason = history.stopReason;
  return {
    available: true,
    source,
    ...(stopReason !== null ? { stop_reason: stopReason } : {}),
  };
}

export class AgentSession {
  data: readonly Row[] | null = null;
  config: GoldenMatchConfig | null = null;
  result: DedupeResult | MatchResult | null = null;
  readonly reviewQueue: ReviewQueue = new ReviewQueue();
  reasoning: Record<string, unknown> = {};
  lastTelemetry: Telemetry | null = null;

  // ── analyze ────────────────────────────────────────────────────────────

  /**
   * Profile rows + select a strategy + build alternatives (port of
   * `analyze`). Sync. Stashes and returns the reasoning payload, with profile
   * fields rounded (uniqueness/null_rate to 4dp, avg_length to 1dp).
   */
  analyze(rows: readonly Row[]): AnalyzeResult {
    this.data = rows;

    const profile = profileForAgent(rows);
    const decision = selectStrategy(profile);
    const alternatives = buildAlternatives(decision);

    const fields: AnalyzeFieldProfile[] = profile.fields.map((f) => ({
      name: f.name,
      type: f.type,
      uniqueness: round(f.uniqueness, 4),
      null_rate: round(f.null_rate, 4),
      avg_length: round(f.avg_length, 1),
    }));

    const reasoning: AnalyzeResult = {
      profile: {
        row_count: profile.row_count,
        fields,
        has_sensitive: profile.has_sensitive,
      },
      strategy: decision.strategy,
      why: decision.why,
      domain: decision.domain,
      strong_ids: decision.strong_ids,
      fuzzy_fields: decision.fuzzy_fields,
      backend: decision.backend,
      auto_execute: decision.auto_execute,
      alternatives,
    };

    this.reasoning = reasoning;
    return reasoning;
  }

  // ── autoconfigure ────────────────────────────────────────────────────────

  /**
   * Run the iterative AutoConfigController on the rows; return the committed
   * config + telemetry (port of `autoconfigure`). Async: the underlying
   * `autoConfigureRowsIterate` is a Promise. Telemetry is stashed on
   * `this.lastTelemetry`.
   */
  async autoconfigure(rows: readonly Row[]): Promise<AutoconfigResult> {
    this.data = rows;
    const { config, history } = await autoConfigureRowsIterate(rows);
    this.config = config;
    const telemetry = captureTelemetry(history, "autoconfigure");
    this.lastTelemetry = telemetry;
    return { config, telemetry };
  }

  // ── deduplicate ──────────────────────────────────────────────────────────

  /**
   * Full deduplication with profiling, strategy, gating, and review queue
   * (port of `deduplicate`). Async.
   *
   * Profile + decision back the `reasoning` payload only; the actual dedupe
   * config is the caller's explicit config or the iterative auto-config one.
   * `last_telemetry` is `{available:false, source:"deduplicate"}` (the
   * stateless dispatch path can't read the controller ContextVar — Python
   * parity).
   */
  async deduplicate(
    rows: readonly Row[],
    config?: GoldenMatchConfig,
  ): Promise<DeduplicateResult> {
    this.data = rows;

    // Profile + decision are kept ONLY for the `reasoning` field.
    const profile = profileForAgent(rows);
    const decision = selectStrategy(profile);

    let cfg: GoldenMatchConfig | undefined = config;
    if (cfg === undefined) {
      const { config: committed } = await autoConfigureRowsIterate(rows);
      cfg = committed;
    }
    this.config = cfg;

    const result: DedupeResult = await dedupe(rows, { config: cfg });
    this.result = result;

    // Gate scored pairs through the review queue.
    const scoredPairs = result.scoredPairs;
    const gated = gatePairs(scoredPairs);

    // Populate the in-memory review queue with borderline pairs.
    for (const item of gated.needsReview) {
      this.reviewQueue.add({
        idA: item.idA,
        idB: item.idB,
        score: item.score,
      });
    }

    const confidence_distribution = {
      auto_merged: gated.autoApproved.length,
      review: gated.needsReview.length,
      auto_rejected: gated.rejected.length,
      total_pairs: scoredPairs.length,
    };

    const reasoning: Record<string, unknown> = {
      strategy: decision.strategy,
      why: decision.why,
      domain: decision.domain,
      strong_ids: decision.strong_ids,
      fuzzy_fields: decision.fuzzy_fields,
    };
    this.reasoning = reasoning;

    // Stateless dispatch path: telemetry not readable cross-request.
    this.lastTelemetry = { available: false, source: "deduplicate" };

    return {
      results: result,
      reasoning,
      confidence_distribution,
      storage: "memory",
    };
  }

  // ── matchSources ─────────────────────────────────────────────────────────

  /**
   * Match two row sources with profiling + strategy (port of `match_sources`).
   * Async. Profile/decision back the `reasoning` payload only.
   */
  async matchSources(
    rowsA: readonly Row[],
    rowsB: readonly Row[],
    config?: GoldenMatchConfig,
  ): Promise<MatchSourcesResult> {
    const profile = profileForAgent(rowsA);
    const decision = selectStrategy(profile);

    let cfg: GoldenMatchConfig | undefined = config;
    if (cfg === undefined) {
      const { config: committed } = await autoConfigureRowsIterate(rowsA);
      cfg = committed;
    }
    this.config = cfg;

    const result: MatchResult = await match(rowsA, rowsB, { config: cfg });
    this.result = result;

    const reasoning: Record<string, unknown> = {
      strategy: decision.strategy,
      why: decision.why,
      domain: decision.domain,
      strong_ids: decision.strong_ids,
      fuzzy_fields: decision.fuzzy_fields,
    };
    this.reasoning = reasoning;

    return { results: result, reasoning };
  }

  // ── compareStrategies ────────────────────────────────────────────────────

  /**
   * Run >=1 candidate strategies on the same rows and compare proxy metrics
   * (port of `compare_strategies`). Async. When `groundTruth` pairs are
   * supplied, scores each strategy's clusters with `evaluateClusters`.
   */
  async compareStrategies(
    rows: readonly Row[],
    groundTruth?: readonly (readonly [number, number])[],
  ): Promise<CompareResult> {
    this.data = rows;

    const profile = profileForAgent(rows);
    const decision = selectStrategy(profile);

    const strategiesToRun: StrategyDecision[] = [decision];

    // Add an exact-only variant if there are strong IDs (and not already it).
    if (decision.strong_ids.length > 0 && decision.strategy !== "exact_only") {
      strategiesToRun.push({
        strategy: "exact_only",
        why: "Comparison: exact matching only.",
        domain: null,
        strong_ids: decision.strong_ids,
        fuzzy_fields: [],
        backend: null,
        auto_execute: true,
      });
    }

    // Add a fuzzy-only variant if there are fuzzy fields (and not already it).
    if (decision.fuzzy_fields.length > 0 && decision.strategy !== "fuzzy") {
      strategiesToRun.push({
        strategy: "fuzzy",
        why: "Comparison: fuzzy matching only.",
        domain: null,
        strong_ids: [],
        fuzzy_fields: decision.fuzzy_fields,
        backend: null,
        auto_execute: true,
      });
    }

    const allIds: number[] = rows.map((_, i) => i);
    const strategies: Record<string, StrategyMetrics> = {};

    for (const strat of strategiesToRun) {
      const cfg = decisionToConfig(strat);
      try {
        const res = await dedupe(rows, { config: cfg });
        let multiClusters = 0;
        let totalMatched = 0;
        for (const c of res.clusters.values()) {
          if (c.size > 1) {
            multiClusters += 1;
            totalMatched += c.size;
          }
        }
        const matchRate =
          rows.length > 0 ? totalMatched / rows.length : 0.0;

        const metrics: StrategyMetrics = {
          clusters: multiClusters,
          match_rate: round(matchRate, 4),
          total_pairs: res.scoredPairs.length,
        };

        if (groundTruth !== undefined) {
          try {
            const ev = evaluateClusters(res.clusters, groundTruth, allIds);
            strategies[strat.strategy] = {
              ...metrics,
              precision: round(ev.precision, 4),
              recall: round(ev.recall, 4),
              f1: round(ev.f1, 4),
            };
            continue;
          } catch (exc) {
            strategies[strat.strategy] = {
              ...metrics,
              error: String(exc),
            };
            continue;
          }
        }

        strategies[strat.strategy] = metrics;
      } catch (exc) {
        strategies[strat.strategy] = { error: String(exc) };
      }
    }

    return { recommended: decision.strategy, strategies };
  }
}
