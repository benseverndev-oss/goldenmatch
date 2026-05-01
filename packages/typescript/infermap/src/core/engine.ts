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
import { AliasScorer } from "./scorers/alias.js";
import { optimalAssign } from "./assignment/hungarian.js";
import type { Calibrator } from "./calibration.js";
import { mergeDomains } from "./dictionaries/index.js";

// Minimum number of non-abstain scorer contributors required to keep a score.
export const MIN_CONTRIBUTORS = 2;

const DELIMITERS = new Set(["_", "-", ".", " "]);

/**
 * Find a common delimiter-bounded affix across *names*, else "".
 * Mirrors Python engine._common_affix_tokens.
 */
export function commonAffixTokens(
  names: readonly string[],
  atStart: boolean
): string {
  if (names.length < 2) return "";
  const shortest = Math.min(...names.map((n) => n.length));
  let i = 0;
  if (atStart) {
    while (i < shortest && names.every((n) => n[i] === names[0]![i])) i++;
  } else {
    while (
      i < shortest &&
      names.every((n) => n[n.length - 1 - i] === names[0]![names[0]!.length - 1 - i])
    )
      i++;
  }
  const candidate = atStart
    ? names[0]!.slice(0, i)
    : i > 0
      ? names[0]!.slice(names[0]!.length - i)
      : "";
  if (candidate.length < 2) return "";
  if (atStart) {
    for (let pos = candidate.length - 1; pos >= 0; pos--) {
      if (DELIMITERS.has(candidate[pos]!)) return candidate.slice(0, pos + 1);
    }
    return "";
  } else {
    for (let pos = 0; pos < candidate.length; pos++) {
      if (DELIMITERS.has(candidate[pos]!)) return candidate.slice(pos);
    }
    return "";
  }
}

/** Populate FieldInfo.canonicalName on each field. Mutates *fields*. */
export function populateCanonicalNames(fields: FieldInfo[]): void {
  const names = fields.map((f) => f.name);
  const prefix = commonAffixTokens(names, true);
  const suffix = commonAffixTokens(names, false);
  for (const f of fields) {
    let canonical = f.name;
    if (prefix && canonical.startsWith(prefix)) canonical = canonical.slice(prefix.length);
    if (suffix && canonical.endsWith(suffix))
      canonical = canonical.slice(0, canonical.length - suffix.length);
    f.canonicalName = canonical || f.name;
  }
}

function cloneFields(fields: readonly FieldInfo[]): FieldInfo[] {
  return fields.map((f) => ({
    ...f,
    sampleValues: f.sampleValues.slice(),
    metadata: { ...f.metadata },
  }));
}

function defaultScorersWithDomains(domains: readonly string[]): Scorer[] {
  const ordered: string[] = [];
  if (!domains.includes("generic")) ordered.push("generic");
  ordered.push(...domains);
  const merged = mergeDomains(ordered);
  const scorers = defaultScorers();
  for (let i = 0; i < scorers.length; i++) {
    if (scorers[i]!.name === "AliasScorer") {
      scorers[i] = new AliasScorer({ aliases: merged });
      break;
    }
  }
  return scorers;
}

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
  /** If true, attach the full MxN score matrix to MapResult.scoreMatrix. */
  returnScoreMatrix?: boolean;
  /**
   * Optional post-assignment confidence calibrator. Applied AFTER
   * `optimalAssign` has picked mappings, so it never changes WHICH mappings
   * are chosen — only the confidence attached to each. min_confidence
   * filtering happens during assignment on raw scores; calibration is about
   * user-facing trust, not assignment behavior.
   */
  calibrator?: Calibrator;
  /**
   * Domain dictionaries to load in addition to `generic`. When set, the
   * engine builds a per-instance AliasScorer whose alias dict merges
   * generic + requested domains, and swaps it into the default scorer list.
   */
  domains?: readonly string[];
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
  readonly returnScoreMatrix: boolean;
  readonly calibrator: Calibrator | undefined;
  readonly domains: readonly string[] | undefined;
  private readonly onScorerError: NonNullable<MapEngineOptions["onScorerError"]>;

  constructor(options: MapEngineOptions = {}) {
    this.minConfidence = options.minConfidence ?? 0.2;
    this.domains = options.domains;
    if (options.scorers) {
      this.scorers = options.scorers;
    } else if (options.domains) {
      this.scorers = defaultScorersWithDomains(options.domains);
    } else {
      this.scorers = defaultScorers();
    }
    this.returnScoreMatrix = options.returnScoreMatrix ?? false;
    this.calibrator = options.calibrator;
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

    // Deep-copy both sides so we can populate canonicalName + mutate metadata
    // without leaking into the caller's schemas.
    const srcFields = cloneFields(sourceSchema.fields);
    const tgtFields = cloneFields(targetSchema.fields);

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

    // Populate canonical_name on each field (affix-stripped).
    populateCanonicalNames(srcFields);
    populateCanonicalNames(tgtFields);

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

    // Post-assignment calibration. Applied to confidence only — it does not
    // change which mappings were picked, only the score attached to each.
    if (this.calibrator && mappings.length > 0) {
      const raw = mappings.map((m) => m.confidence);
      const cal = this.calibrator.transform(raw);
      if (cal.length !== mappings.length) {
        throw new Error(
          `Calibrator.transform() returned ${cal.length} values for ${mappings.length} mappings`
        );
      }
      for (let i = 0; i < mappings.length; i++) {
        const c = cal[i]!;
        if (!Number.isFinite(c)) {
          throw new Error(
            `Calibrator.transform() returned non-finite value ${c} at index ${i}`
          );
        }
        mappings[i]!.confidence = c;
      }
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

    // Optional: expose the full score matrix for MRR computation in the benchmark.
    let scoreMatrixDict: Record<string, Record<string, number>> | undefined;
    if (this.returnScoreMatrix) {
      scoreMatrixDict = {};
      for (let i = 0; i < M; i++) {
        const row: Record<string, number> = {};
        for (let j = 0; j < N; j++) {
          row[tgtFields[j]!.name] = scoreMatrix[i]![j]!;
        }
        scoreMatrixDict[srcFields[i]!.name] = row;
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
      ...(scoreMatrixDict !== undefined ? { scoreMatrix: scoreMatrixDict } : {}),
    };
  }
}
