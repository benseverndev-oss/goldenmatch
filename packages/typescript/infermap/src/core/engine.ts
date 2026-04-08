// MapEngine — orchestrates scoring and assignment on pre-extracted schemas.
// Mirrors infermap/engine.py. Provider-based schema extraction lives in
// src/core/providers (Step 11); this module operates on SchemaInfo directly
// so core is edge-safe and free of Node-only deps.

import type {
  FieldInfo,
  FieldMapping,
  MapResult,
  ScorerResult,
  SchemaInfo,
} from "./types.js";
import type { Scorer } from "./scorers/base.js";
import { defaultScorers } from "./scorers/registry.js";
import { optimalAssign } from "./assignment/hungarian.js";

// Minimum number of non-abstain scorer contributors required to keep a score.
export const MIN_CONTRIBUTORS = 2;

export interface MapEngineOptions {
  minConfidence?: number;
  scorers?: Scorer[];
  /** Logger callback invoked when a scorer throws. Defaults to console.warn. */
  onScorerError?: (info: {
    scorer: string;
    source: string;
    target: string;
    error: unknown;
  }) => void;
}

export interface MapSchemasOptions {
  /** Extra required target field names, merged with schema.requiredFields. */
  required?: string[];
  /** Schema-file alias source: merges its fields' metadata.aliases into target. */
  schemaFile?: SchemaInfo;
}

export class MapEngine {
  readonly minConfidence: number;
  readonly scorers: readonly Scorer[];
  private readonly onScorerError: NonNullable<MapEngineOptions["onScorerError"]>;

  constructor(options: MapEngineOptions = {}) {
    this.minConfidence = options.minConfidence ?? 0.3;
    this.scorers = options.scorers ?? defaultScorers();
    this.onScorerError =
      options.onScorerError ??
      (({ scorer, source, target, error }) => {
        // eslint-disable-next-line no-console
        console.warn(
          `Scorer ${scorer} raised for (${source}, ${target}): ${String(error)}`
        );
      });
  }

  /**
   * Core mapping path: given two pre-extracted schemas, return a MapResult.
   * This is the edge-safe entry point — providers/extractors layer on top.
   */
  mapSchemas(
    sourceSchema: SchemaInfo,
    targetSchema: SchemaInfo,
    opts: MapSchemasOptions = {}
  ): MapResult {
    const t0 = performance.now();

    const srcFields = sourceSchema.fields;
    // Clone target fields so we can mutate metadata without affecting caller.
    const tgtFields: FieldInfo[] = targetSchema.fields.map((f) => ({
      ...f,
      metadata: { ...f.metadata },
    }));

    // Merge required fields: target schema + caller-supplied + schema_file
    const requiredSet = new Set<string>(targetSchema.requiredFields);
    for (const r of opts.required ?? []) requiredSet.add(r);

    if (opts.schemaFile) {
      const sfByName = new Map<string, FieldInfo>();
      for (const f of opts.schemaFile.fields) sfByName.set(f.name, f);
      for (const tgt of tgtFields) {
        const sfField = sfByName.get(tgt.name);
        if (!sfField) continue;
        const extra = Array.isArray(sfField.metadata["aliases"])
          ? (sfField.metadata["aliases"] as unknown[]).filter(
              (x): x is string => typeof x === "string"
            )
          : [];
        const existing = Array.isArray(tgt.metadata["aliases"])
          ? (tgt.metadata["aliases"] as unknown[]).filter(
              (x): x is string => typeof x === "string"
            )
          : [];
        // Preserve order, dedupe
        const merged = Array.from(new Set([...existing, ...extra]));
        if (merged.length > 0) tgt.metadata["aliases"] = merged;
      }
      for (const r of opts.schemaFile.requiredFields) requiredSet.add(r);
    }

    const M = srcFields.length;
    const N = tgtFields.length;

    const scoreMatrix: number[][] = Array.from({ length: M }, () =>
      new Array<number>(N).fill(0)
    );
    const breakdownMatrix: Array<Array<Record<string, ScorerResult>>> =
      Array.from({ length: M }, () =>
        Array.from({ length: N }, () => ({}))
      );

    for (let i = 0; i < M; i++) {
      const src = srcFields[i]!;
      for (let j = 0; j < N; j++) {
        const tgt = tgtFields[j]!;
        const contributors: Array<{
          name: string;
          result: ScorerResult;
          weight: number;
        }> = [];

        for (const sc of this.scorers) {
          let result: ScorerResult | null = null;
          try {
            result = sc.score(src, tgt);
          } catch (error) {
            this.onScorerError({
              scorer: sc.name,
              source: src.name,
              target: tgt.name,
              error,
            });
            result = null;
          }
          if (result !== null) {
            contributors.push({ name: sc.name, result, weight: sc.weight });
          }
        }

        let combined = 0;
        if (contributors.length >= MIN_CONTRIBUTORS) {
          let totalWeight = 0;
          let weightedSum = 0;
          for (const c of contributors) {
            totalWeight += c.weight;
            weightedSum += c.result.score * c.weight;
          }
          combined = totalWeight > 0 ? weightedSum / totalWeight : 0;
        }

        scoreMatrix[i]![j] = combined;
        const breakdown: Record<string, ScorerResult> = {};
        for (const c of contributors) breakdown[c.name] = c.result;
        breakdownMatrix[i]![j] = breakdown;
      }
    }

    const assignments = optimalAssign(scoreMatrix, this.minConfidence);
    const assignedSrc = new Set<number>();
    const assignedTgt = new Set<number>();
    const mappings: FieldMapping[] = [];

    for (const a of assignments) {
      assignedSrc.add(a.sourceIdx);
      assignedTgt.add(a.targetIdx);
      const src = srcFields[a.sourceIdx]!;
      const tgt = tgtFields[a.targetIdx]!;
      const bd = breakdownMatrix[a.sourceIdx]![a.targetIdx]!;
      const reasoning = Object.entries(bd)
        .map(([name, res]) => `${name}: ${res.reasoning}`)
        .join("; ");
      mappings.push({
        source: src.name,
        target: tgt.name,
        confidence: a.score,
        breakdown: bd,
        reasoning,
      });
    }

    const unmappedSource: string[] = [];
    for (let i = 0; i < M; i++)
      if (!assignedSrc.has(i)) unmappedSource.push(srcFields[i]!.name);
    const unmappedTarget: string[] = [];
    for (let j = 0; j < N; j++)
      if (!assignedTgt.has(j)) unmappedTarget.push(tgtFields[j]!.name);

    // Warnings for unmapped required target fields
    const warnings: string[] = [];
    const mappedTargets = new Set(mappings.map((m) => m.target));
    for (const reqField of requiredSet) {
      if (mappedTargets.has(reqField)) continue;
      const tgtIdx = tgtFields.findIndex((tf) => tf.name === reqField);
      let bestCandidate: string | null = null;
      let bestScore = 0;
      if (tgtIdx >= 0) {
        for (let i = 0; i < M; i++) {
          const s = scoreMatrix[i]![tgtIdx]!;
          if (s > bestScore) {
            bestScore = s;
            bestCandidate = srcFields[i]!.name;
          }
        }
      }
      if (bestCandidate) {
        warnings.push(
          `Required target field '${reqField}' is unmapped. Best candidate: '${bestCandidate}' (score=${bestScore.toFixed(3)})`
        );
      } else {
        warnings.push(
          `Required target field '${reqField}' is unmapped and no candidate found.`
        );
      }
    }

    const elapsed = (performance.now() - t0) / 1000;
    return {
      mappings,
      unmappedSource,
      unmappedTarget,
      warnings,
      metadata: {
        elapsed_seconds: Math.round(elapsed * 10000) / 10000,
        source_field_count: M,
        target_field_count: N,
        mapping_count: mappings.length,
        min_confidence: this.minConfidence,
      },
    };
  }
}
