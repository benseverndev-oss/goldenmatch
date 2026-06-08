/** Analyzer registry — a hard-coded map (no entry-points in TS). Mirrors the Python
 * registry's fallback table (`registry.py`). */

import { ClusterDistributionAnalyzer } from "./analyzers/clusterDist.js";
import { FrameSummaryAnalyzer } from "./analyzers/frameSummary.js";
import { MatchRatesAnalyzer } from "./analyzers/matchRates.js";
import { QualityRollupAnalyzer } from "./analyzers/qualityRollup.js";
import type { Analyzer } from "./types.js";

const FACTORIES: Record<string, () => Analyzer> = {
  "frame.summary": () => new FrameSummaryAnalyzer(),
  "match.rates": () => new MatchRatesAnalyzer(),
  "cluster.distribution": () => new ClusterDistributionAnalyzer(),
  "quality.rollup": () => new QualityRollupAnalyzer(),
};

export function availableAnalyzers(): string[] {
  return Object.keys(FACTORIES).sort();
}

export function loadAnalyzer(name: string): Analyzer {
  const factory = FACTORIES[name];
  if (factory === undefined) {
    throw new Error(`unknown analyzer ${name}; available: ${availableAnalyzers().join(", ")}`);
  }
  return factory();
}

/** Analyzers that consume a generic `frame` (the default set for `analyze`). */
export function frameCompatibleAnalyzers(): string[] {
  return availableAnalyzers().filter((name) => loadAnalyzer(name).info.consumes.includes("frame"));
}
