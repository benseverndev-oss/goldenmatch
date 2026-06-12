/**
 * `match.rates` — match metrics from a GoldenMatch result's artifacts.
 *
 * Reads `scored_pairs` / `match_stats` (+ optional `recall_certificate` and
 * `match_threshold`) from `AnalyzerInput.artifacts` — the SAME snake_case keys the
 * Python sibling reads, so a serialized `PipeResult.artifacts` feeds this identically.
 * Degrades: emits the metrics its present artifacts support and omits the rest.
 * Parity with `packages/python/goldenanalysis/goldenanalysis/analyzers/match_rates.py`.
 */

import { histogram } from "../aggregate.js";
import type {
  AnalysisTable,
  Analyzer,
  AnalyzerInfo,
  AnalyzerInput,
  AnalyzerResult,
  Metric,
} from "../types.js";

const PRODUCES = [
  "match.pair_count",
  "match.match_rate",
  "match.threshold",
  "match.recall_estimate",
  "match.recall_safe_bound",
  "match.mean_pair_score",
];

function asNumber(value: unknown): number | null {
  return typeof value === "number" ? value : null;
}

/** Normalize a certificate (dict-ish) to [estimate, safeBound]; either may be null. */
function certValues(cert: unknown): [number | null, number | null] {
  if (cert === null || cert === undefined || typeof cert !== "object") return [null, null];
  const c = cert as Record<string, unknown>;
  return [asNumber(c["estimate"] ?? c["recall"]), asNumber(c["safe_bound"] ?? c["recall_lower"])];
}

export class MatchRatesAnalyzer implements Analyzer {
  readonly info: AnalyzerInfo = {
    name: "match.rates",
    consumes: ["scored_pairs", "match_stats"],
    produces: PRODUCES,
  };

  run(input: AnalyzerInput): AnalyzerResult {
    const art = input.artifacts;
    const scoredPairs = Array.isArray(art["scored_pairs"]) ? (art["scored_pairs"] as unknown[]) : [];
    const stats = (art["match_stats"] as Record<string, unknown> | undefined) ?? {};

    const metrics: Metric[] = [
      { key: "match.pair_count", value: scoredPairs.length, unit: "pairs", direction: "neutral" },
    ];
    if ("match_rate" in stats) {
      metrics.push({ key: "match.match_rate", value: Number(stats["match_rate"]), unit: "ratio", direction: "neutral" });
    }
    const threshold = art["match_threshold"];
    if (threshold !== null && threshold !== undefined) {
      metrics.push({ key: "match.threshold", value: Number(threshold), unit: "score", direction: "neutral" });
    }

    const [estimate, safeBound] = certValues(art["recall_certificate"]);
    if (estimate !== null) {
      metrics.push({ key: "match.recall_estimate", value: estimate, unit: "ratio", direction: "higher_better" });
    }
    if (safeBound !== null) {
      metrics.push({ key: "match.recall_safe_bound", value: safeBound, unit: "ratio", direction: "higher_better" });
    }

    const tables: AnalysisTable[] = [];
    if (scoredPairs.length > 0) {
      // Each pair is (...ids, score) — the score is the last element.
      const scores = scoredPairs.map((p) => {
        const arr = p as unknown[];
        return Number(arr[arr.length - 1]);
      });
      const meanScore = scores.reduce((a, b) => a + b, 0) / scores.length;
      metrics.push({ key: "match.mean_pair_score", value: meanScore, unit: "score", direction: "neutral" });
      const hist = histogram(scores, 10);
      tables.push({
        name: "score_histogram",
        columns: ["bin_left", "count"],
        rows: hist.map(([edge, count]) => [edge, count]),
      });
    }

    return { metrics, tables };
  }
}
