/**
 * `cluster.distribution` — cluster-size shape from a GoldenMatch result.
 *
 * Reads `clusters` (and optionally `match_stats` for the record count) from
 * `AnalyzerInput.artifacts`. Parity with
 * `packages/python/goldenanalysis/goldenanalysis/analyzers/cluster_dist.py`.
 */

import { quantile } from "../aggregate.js";
import type {
  AnalysisTable,
  Analyzer,
  AnalyzerInfo,
  AnalyzerInput,
  AnalyzerResult,
  Metric,
} from "../types.js";

const PRODUCES = [
  "cluster.count",
  "cluster.record_count",
  "cluster.singleton_ratio",
  "cluster.size_p50",
  "cluster.size_p95",
  "cluster.size_max",
  "cluster.reduction_ratio",
];

/** A cluster is `{size}` / `{members:[...]}` (dict) or a bare size (int). */
function clusterSize(c: unknown): number {
  if (c !== null && typeof c === "object") {
    const obj = c as Record<string, unknown>;
    if (typeof obj["size"] === "number") return obj["size"];
    const members = obj["members"];
    return Array.isArray(members) ? members.length : 0;
  }
  return Number(c);
}

export class ClusterDistributionAnalyzer implements Analyzer {
  readonly info: AnalyzerInfo = {
    name: "cluster.distribution",
    consumes: ["clusters"],
    produces: PRODUCES,
  };

  run(input: AnalyzerInput): AnalyzerResult {
    const clusters = input.artifacts["clusters"];
    // NOTE: an empty object `{}` is truthy in JS — guard on key count, not falsiness.
    if (clusters === null || clusters === undefined || typeof clusters !== "object") {
      return { metrics: [], tables: [] };
    }
    const values = Object.values(clusters as Record<string, unknown>);
    if (values.length === 0) return { metrics: [], tables: [] };

    const sizes = values.map(clusterSize);
    const count = values.length;
    const stats = (input.artifacts["match_stats"] as Record<string, unknown> | undefined) ?? {};
    const sumSizes = sizes.reduce((a, b) => a + b, 0);
    // Prefer the engine's own record total; fall back to summed cluster sizes.
    const recordCount = typeof stats["total_records"] === "number" ? stats["total_records"] : sumSizes;
    const singletons = sizes.filter((s) => s === 1).length;

    const metrics: Metric[] = [
      { key: "cluster.count", value: count, unit: "clusters", direction: "neutral" },
      { key: "cluster.record_count", value: recordCount, unit: "rows", direction: "neutral" },
      { key: "cluster.singleton_ratio", value: count ? singletons / count : 0, unit: "ratio", direction: "neutral" },
      { key: "cluster.size_p50", value: quantile(sizes, 0.5), unit: "rows", direction: "neutral" },
      { key: "cluster.size_p95", value: quantile(sizes, 0.95), unit: "rows", direction: "neutral" },
      { key: "cluster.size_max", value: sizes.length ? Math.max(...sizes) : 0, unit: "rows", direction: "neutral" },
      {
        key: "cluster.reduction_ratio",
        value: recordCount ? 1 - count / recordCount : 0,
        unit: "ratio",
        direction: "neutral",
      },
    ];

    // Discrete size histogram, buckets 1 / 2 / 3 / "4+".
    const n1 = sizes.filter((s) => s === 1).length;
    const n2 = sizes.filter((s) => s === 2).length;
    const n3 = sizes.filter((s) => s === 3).length;
    const n4 = sizes.filter((s) => s >= 4).length;
    const table: AnalysisTable = {
      name: "cluster_size_histogram",
      columns: ["size", "count"],
      rows: [
        [1, n1],
        [2, n2],
        [3, n3],
        ["4+", n4],
      ],
    };

    return { metrics, tables: [table] };
  }
}
