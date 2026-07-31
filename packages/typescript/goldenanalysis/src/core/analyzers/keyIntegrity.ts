/**
 * `key.integrity` — metric-aware key-integrity metrics from a certificate.
 *
 * Reads a `key_certificate` (a serialized GoldenMatch `KeyIntegrityCertificate` or a
 * dict of the same shape) from `AnalyzerInput.artifacts` and projects it into the
 * suite's `Metric` / `AnalysisTable` reporting shape — the read-side sibling of
 * `match.rates` (which consumes a recall certificate). It only *reports* the cert; the
 * computation + advisory contract live in goldenmatch, so no ER pipeline / kernel is
 * touched here (pure arithmetic, exactly like `matchRates`).
 *
 * Parity with `packages/python/goldenanalysis/goldenanalysis/analyzers/key_integrity.py`.
 */

import type {
  AnalysisTable,
  Analyzer,
  AnalyzerInfo,
  AnalyzerInput,
  AnalyzerResult,
  Metric,
} from "../types.js";

const PRODUCES = [
  "key.uniqueness",
  "key.duplicate_groups",
  "key.max_fan_out",
  "key.undercount_estimate",
  "key.fragmented_entities",
];

/** Read a field from a serialized certificate (plain object); `null` when absent. */
function get(cert: Record<string, unknown>, name: string): unknown {
  const v = cert[name];
  return v === undefined ? null : v;
}

export class KeyIntegrityAnalyzer implements Analyzer {
  readonly info: AnalyzerInfo = {
    name: "key.integrity",
    consumes: ["key_certificate"],
    produces: PRODUCES,
  };

  run(input: AnalyzerInput): AnalyzerResult {
    const metrics: Metric[] = [];
    const tables: AnalysisTable[] = [];

    const raw = input.artifacts["key_certificate"];
    if (raw === null || raw === undefined || typeof raw !== "object") {
      return { metrics, tables };
    }
    const cert = raw as Record<string, unknown>;

    // `estimate` is a property on the Python dataclass; a serialized cert omits it,
    // so recompute from the group counts (mirrors KeyIntegrityCertificate.estimate:
    // 1 - duplicate_key_groups / n_key_groups, and 1.0 when there are no groups).
    let uniqueness = get(cert, "estimate");
    if (uniqueness === null) {
      const nGroups = Number(get(cert, "n_key_groups") ?? 0) || 0;
      const dupes = Number(get(cert, "duplicate_key_groups") ?? 0) || 0;
      uniqueness = nGroups ? 1.0 - dupes / nGroups : 1.0;
    }
    metrics.push({ key: "key.uniqueness", value: Number(uniqueness), unit: "ratio", direction: "higher_better" });
    metrics.push({
      key: "key.duplicate_groups",
      // Python: int(_get(cert, "duplicate_key_groups", 0) or 0) — falsy -> 0.
      value: Math.trunc(Number(get(cert, "duplicate_key_groups")) || 0),
      unit: "groups",
      direction: "lower_better",
    });
    metrics.push({
      key: "key.max_fan_out",
      // Python: float(_get(cert, "max_fan_out", 1.0) or 1.0) — falsy (incl. 0.0) -> 1.0.
      value: Number(get(cert, "max_fan_out")) || 1.0,
      unit: "ratio",
      direction: "lower_better",
    });

    const undercount = get(cert, "undercount_estimate");
    if (undercount !== null) {
      metrics.push({ key: "key.undercount_estimate", value: Number(undercount), unit: "ratio", direction: "lower_better" });
    }
    const fragmented = get(cert, "fragmented_entities");
    if (fragmented !== null) {
      metrics.push({ key: "key.fragmented_entities", value: Math.trunc(Number(fragmented)), unit: "entities", direction: "lower_better" });
    }

    const fanOutRaw = get(cert, "measure_fan_out");
    const fanOut = fanOutRaw && typeof fanOutRaw === "object" ? (fanOutRaw as Record<string, unknown>) : {};
    const fanEntries = Object.entries(fanOut);
    if (fanEntries.length > 0) {
      tables.push({
        name: "measure_fan_out",
        columns: ["measure", "fan_out_ratio"],
        rows: fanEntries.map(([measure, ratio]) => [measure, Number(ratio)]),
      });
    }

    return { metrics, tables };
  }
}
