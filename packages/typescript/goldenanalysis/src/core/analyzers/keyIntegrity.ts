/**
 * `key.integrity` — metric-aware key-integrity metrics from a certificate.
 *
 * Reads a `key_certificate` (a GoldenMatch KeyIntegrityCertificate, serialized to
 * a plain object over the wire) from `AnalyzerInput.artifacts` and projects it
 * into the suite's Metric / AnalysisTable reporting shape — the read-side sibling
 * of `match.rates` (which consumes a recall certificate).
 *
 * The certificate answers "is the entity key a semantic model declares actually
 * trustworthy?": unique-at-grain, per-measure fan-out, and (opt-in) entity
 * fragmentation / undercount. This analyzer only *reports* it — the computation
 * and the advisory contract live in goldenmatch.
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

function asNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
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

    // `estimate` is a property on the dataclass; recompute for the dict path
    // (the serialized certificate carries no `estimate` field).
    let uniqueness = asNumber(cert["estimate"]);
    if (uniqueness === null) {
      const nGroups = asNumber(cert["n_key_groups"]) ?? 0;
      const dupes = asNumber(cert["duplicate_key_groups"]) ?? 0;
      uniqueness = nGroups ? 1.0 - dupes / nGroups : 1.0;
    }
    metrics.push({ key: "key.uniqueness", value: uniqueness, unit: "ratio", direction: "higher_better" });

    metrics.push({
      key: "key.duplicate_groups",
      value: Math.trunc(asNumber(cert["duplicate_key_groups"]) ?? 0),
      unit: "groups",
      direction: "lower_better",
    });
    metrics.push({
      key: "key.max_fan_out",
      value: asNumber(cert["max_fan_out"]) ?? 1.0,
      unit: "ratio",
      direction: "lower_better",
    });

    const undercount = asNumber(cert["undercount_estimate"]);
    if (undercount !== null) {
      metrics.push({ key: "key.undercount_estimate", value: undercount, unit: "ratio", direction: "lower_better" });
    }
    const fragmented = asNumber(cert["fragmented_entities"]);
    if (fragmented !== null) {
      metrics.push({
        key: "key.fragmented_entities",
        value: Math.trunc(fragmented),
        unit: "entities",
        direction: "lower_better",
      });
    }

    const fanOut = cert["measure_fan_out"];
    if (fanOut !== null && typeof fanOut === "object") {
      const rows: (string | number)[][] = Object.entries(fanOut as Record<string, unknown>).map(
        ([measure, ratio]) => [measure, Number(ratio)],
      );
      if (rows.length > 0) {
        tables.push({ name: "measure_fan_out", columns: ["measure", "fan_out_ratio"], rows });
      }
    }

    return { metrics, tables };
  }
}
