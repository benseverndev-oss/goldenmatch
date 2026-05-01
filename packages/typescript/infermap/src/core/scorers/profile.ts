// Profile scorer — compares statistical profiles of two fields.
// Mirrors infermap/scorers/profile.py.
import type { FieldInfo, ScorerResult } from "../types.js";
import { makeScorerResult } from "../types.js";
import type { Scorer } from "./base.js";

const fmt2 = (n: number): string => n.toFixed(2);

function avgValueLength(samples: readonly string[]): number {
  const clean = samples.filter((s) => s != null && String(s).trim() !== "");
  if (clean.length === 0) return 0;
  let total = 0;
  for (const s of clean) total += String(s).length;
  return total / clean.length;
}

function similarity(a: number, b: number): number {
  return Math.max(0, 1 - Math.abs(a - b));
}

/**
 * Profile comparison dimensions and weights:
 *   dtype match        0.4
 *   null rate          0.2
 *   uniqueness rate    0.2
 *   value length       0.1
 *   cardinality ratio  0.1
 */
export class ProfileScorer implements Scorer {
  readonly name = "ProfileScorer";
  readonly weight = 0.5;

  score(source: FieldInfo, target: FieldInfo): ScorerResult | null {
    if (source.valueCount === 0 || target.valueCount === 0) return null;

    let total = 0;
    const parts: string[] = [];

    const dtypeMatch = source.dtype === target.dtype ? 1 : 0;
    total += 0.4 * dtypeMatch;
    parts.push(`dtype=${dtypeMatch ? "match" : "mismatch"}`);

    const nullSim = similarity(source.nullRate, target.nullRate);
    total += 0.2 * nullSim;
    parts.push(`null_sim=${fmt2(nullSim)}`);

    const uniqSim = similarity(source.uniqueRate, target.uniqueRate);
    total += 0.2 * uniqSim;
    parts.push(`uniq_sim=${fmt2(uniqSim)}`);

    const srcLen = avgValueLength(source.sampleValues);
    const tgtLen = avgValueLength(target.sampleValues);
    const maxLen = Math.max(srcLen, tgtLen, 1);
    const lenSim = 1 - Math.abs(srcLen - tgtLen) / maxLen;
    total += 0.1 * lenSim;
    parts.push(`len_sim=${fmt2(lenSim)}`);

    const srcCard = source.uniqueRate * source.valueCount;
    const tgtCard = target.uniqueRate * target.valueCount;
    const maxCard = Math.max(srcCard, tgtCard, 1);
    const cardSim = 1 - Math.abs(srcCard - tgtCard) / maxCard;
    total += 0.1 * cardSim;
    parts.push(`card_sim=${fmt2(cardSim)}`);

    return makeScorerResult(total, `Profile comparison: ${parts.join(", ")}`);
  }
}
