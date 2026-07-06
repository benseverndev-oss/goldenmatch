/**
 * Cross-surface parity — the TypeScript `cluster.distribution` analyzer result must
 * match the Python-locked `cluster_distribution_result.json` exactly.
 *
 * `cluster_distribution_result.json` is a byte-identical copy of
 * `packages/python/goldenanalysis/tests/fixtures/cluster_distribution_result.json`
 * (the file Python's `test_cluster_distribution_parity.py` locks). Unlike
 * frameSummary, cluster.distribution has NO engine-specific fields, so this is a raw
 * `toEqual` of `{metrics, tables}` — no projection. Float values (singleton_ratio,
 * reduction_ratio) are identical IEEE-754 doubles on both surfaces (same ops, same
 * operand order, integer inputs), so exact equality holds.
 */

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { ClusterDistributionAnalyzer } from "../../src/core/analyzers/clusterDist.js";
import type { AnalyzerInput } from "../../src/core/types.js";

const __dirname = dirname(fileURLToPath(import.meta.url));
const FIXTURE = join(__dirname, "..", "fixtures", "cluster_distribution_result.json");

const CLUSTERS = {
  "0": { members: [0], size: 1 },
  "1": { members: [1], size: 1 },
  "2": { members: [2, 3], size: 2 },
  "3": { members: [4, 5, 6], size: 3 },
  "4": { members: [7, 8, 9, 10], size: 4 },
  "5": { members: [11, 12, 13, 14, 15, 16], size: 6 },
};

describe("parity: cluster.distribution vs python", () => {
  it("analyzer result matches the python-locked fixture exactly", () => {
    const expected = JSON.parse(readFileSync(FIXTURE, "utf-8"));
    const input: AnalyzerInput = {
      dataset: "customers",
      artifacts: { clusters: CLUSTERS, match_stats: { total_records: 17 } },
    };
    const r = new ClusterDistributionAnalyzer().run(input);
    expect({ metrics: r.metrics, tables: r.tables }).toEqual(expected);
  });
});
