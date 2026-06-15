/**
 * session.ts — AgentSession orchestrator (core, edge-safe).
 * Edge-safe: no `node:` imports. Ported from goldenmatch/core/agent.py.
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
import { ReviewQueue } from "../review-queue.js";
import {
  profileForAgent,
  selectStrategy,
  buildAlternatives,
} from "./strategy.js";
import type {
  AnalyzeResult,
  AnalyzeFieldProfile,
  Telemetry,
} from "./types.js";

/** Round to n decimal places (half-up). Matches the test expectations and is
 *  close enough to Python's round() for the rounded reasoning payload; the
 *  parity emitter is the authoritative judge for fixtures. */
function round(value: number, digits: number): number {
  const factor = 10 ** digits;
  return Math.round(value * factor) / factor;
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
}
