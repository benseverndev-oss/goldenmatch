/**
 * SchemaMapper — auto-map source columns to target columns.
 */

import type { ColumnMapping, GoldenFlowConfig, Row } from "../types.js";
import { makeConfig } from "../types.js";
import { profileDataframe } from "../engine/profiler-bridge.js";
import { nameSimilarity } from "./name-similarity.js";
import { profileSimilarity } from "./profile-similarity.js";

export class SchemaMapper {
  readonly autoThreshold: number;
  readonly suggestThreshold: number;

  constructor(autoThreshold = 0.9, suggestThreshold = 0.6) {
    this.autoThreshold = autoThreshold;
    this.suggestThreshold = suggestThreshold;
  }

  map(sourceRows: readonly Row[], targetRows: readonly Row[]): ColumnMapping[] {
    const sourceProfile = profileDataframe(sourceRows);
    const targetProfile = profileDataframe(targetRows);

    const sourceProfiles = new Map(sourceProfile.columns.map((c) => [c.name, c]));
    const targetProfiles = new Map(targetProfile.columns.map((c) => [c.name, c]));

    const sourceCols =
      sourceRows.length > 0 ? Object.keys(sourceRows[0]!) : [];
    const targetCols =
      targetRows.length > 0 ? Object.keys(targetRows[0]!) : [];

    const mappings: ColumnMapping[] = [];
    const usedTargets = new Set<string>();

    for (const sCol of sourceCols) {
      let bestMatch: ColumnMapping | null = null;
      let bestScore = 0;

      for (const tCol of targetCols) {
        if (usedTargets.has(tCol)) continue;

        const nScore = nameSimilarity(sCol, tCol);

        let pScore = 0;
        const sp = sourceProfiles.get(sCol);
        const tp = targetProfiles.get(tCol);
        if (sp && tp) {
          pScore = profileSimilarity(sp, tp);
        }

        const combined = 0.7 * nScore + 0.3 * pScore;

        if (combined > bestScore && combined >= this.suggestThreshold) {
          bestScore = combined;
          bestMatch = {
            source: sCol,
            target: tCol,
            confidence: Math.round(combined * 1000) / 1000,
            transform: null,
          };
        }
      }

      if (bestMatch) {
        mappings.push(bestMatch);
        usedTargets.add(bestMatch.target);
      }
    }

    return mappings;
  }

  toConfig(mappings: readonly ColumnMapping[]): GoldenFlowConfig {
    return makeConfig({
      mappings: mappings.map((m) => ({
        source: m.source,
        target: m.target,
        transform: m.transform,
      })),
    });
  }
}
