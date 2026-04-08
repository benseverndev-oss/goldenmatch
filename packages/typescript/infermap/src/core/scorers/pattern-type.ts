// Pattern-type scorer — detects semantic types from sample values via regex.
// Mirrors infermap/scorers/pattern_type.py.
//
// All Python regexes are compatible with JS RegExp as-is: they use ^...$
// anchors with no multiline flag, so full-string matching is identical.
import type { FieldInfo, ScorerResult } from "../types.js";
import { makeScorerResult } from "../types.js";
import type { Scorer } from "./base.js";

/**
 * Ordered registry — iteration order matches insertion order, so earlier
 * entries take precedence on ambiguous samples. Matches the Python dict order.
 */
export const SEMANTIC_TYPES: Record<string, RegExp> = {
  email: /^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$/,
  uuid: /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/,
  date_iso: /^\d{4}-\d{2}-\d{2}$/,
  ip_v4: /^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$/,
  url: /^https?:\/\/[^\s]+$/,
  phone: /^[\+\d]?(\d[\s\-\.]?){7,14}\d$/,
  zip_us: /^\d{5}(-\d{4})?$/,
  currency: /^[\$\£\€]\s?\d[\d,]*(\.\d{1,2})?$/,
};

const cleanSamples = (samples: readonly string[]): string[] =>
  samples.filter((s) => s != null && String(s).trim() !== "");

function classifyWithPct(
  field: FieldInfo,
  threshold = 0.6
): { type: string | null; pct: number } {
  const samples = cleanSamples(field.sampleValues);
  if (samples.length === 0) return { type: null, pct: 0 };

  let bestType: string | null = null;
  let bestPct = 0;

  for (const [typeName, pattern] of Object.entries(SEMANTIC_TYPES)) {
    let matches = 0;
    for (const s of samples) {
      if (pattern.test(String(s).trim())) matches++;
    }
    const pct = matches / samples.length;
    if (pct > bestPct) {
      bestPct = pct;
      bestType = typeName;
    }
  }

  if (bestType !== null && bestPct >= threshold) {
    return { type: bestType, pct: bestPct };
  }
  return { type: null, pct: 0 };
}

export function classifyField(
  field: FieldInfo,
  threshold = 0.6
): string | null {
  return classifyWithPct(field, threshold).type;
}

const pctStr = (p: number): string => `${Math.round(p * 100)}%`;

export class PatternTypeScorer implements Scorer {
  readonly name = "PatternTypeScorer";
  readonly weight = 0.7;

  score(source: FieldInfo, target: FieldInfo): ScorerResult | null {
    const srcSamples = cleanSamples(source.sampleValues);
    const tgtSamples = cleanSamples(target.sampleValues);

    if (srcSamples.length === 0 || tgtSamples.length === 0) return null;

    const { type: srcType, pct: srcPct } = classifyWithPct(source);
    const { type: tgtType, pct: tgtPct } = classifyWithPct(target);

    if (srcType === null && tgtType === null) {
      return makeScorerResult(
        0.0,
        "No semantic type detected in either field's samples"
      );
    }

    if (srcType !== tgtType) {
      return makeScorerResult(
        0.0,
        `Semantic type mismatch: source='${srcType ?? "None"}' vs target='${tgtType ?? "None"}'`
      );
    }

    const combined = Math.min(srcPct, tgtPct);
    return makeScorerResult(
      combined,
      `Both fields classified as '${srcType}' (src=${pctStr(srcPct)}, tgt=${pctStr(tgtPct)})`
    );
  }
}
