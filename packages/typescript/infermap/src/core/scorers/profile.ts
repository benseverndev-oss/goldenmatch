// Profile scorer — compares statistical profiles of two fields.
// Mirrors infermap/scorers/profile.py.
import type { FieldInfo, ScorerResult } from "../types.js";
import { makeScorerResult } from "../types.js";
import type { Scorer } from "./base.js";
import { getInfermapBackend } from "../wasm/backend.js";

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

/** Pure five-add profile math — the single source for the pure fallback AND the
 *  WASM parity oracle. Byte-identical to infermap-core::profile_score. Caller owns
 *  the abstain (valueCount===0), avg-length reduction, and reasoning. */
export function _profileScorePure(
  srcDtype: string,
  tgtDtype: string,
  srcNull: number,
  tgtNull: number,
  srcUniq: number,
  tgtUniq: number,
  srcValCount: number,
  tgtValCount: number,
  srcAvgLen: number,
  tgtAvgLen: number,
): number {
  let total = 0;
  total += 0.4 * (srcDtype === tgtDtype ? 1 : 0);
  total += 0.2 * similarity(srcNull, tgtNull);
  total += 0.2 * similarity(srcUniq, tgtUniq);
  const maxLen = Math.max(srcAvgLen, tgtAvgLen, 1);
  total += 0.1 * (1 - Math.abs(srcAvgLen - tgtAvgLen) / maxLen);
  const srcCard = srcUniq * srcValCount;
  const tgtCard = tgtUniq * tgtValCount;
  const maxCard = Math.max(srcCard, tgtCard, 1);
  total += 0.1 * (1 - Math.abs(srcCard - tgtCard) / maxCard);
  return total;
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

    const srcLen = avgValueLength(source.sampleValues);
    const tgtLen = avgValueLength(target.sampleValues);

    const backend = getInfermapBackend();
    const total = backend
      ? backend.profileScore(
          source.dtype, target.dtype,
          source.nullRate, target.nullRate,
          source.uniqueRate, target.uniqueRate,
          source.valueCount, target.valueCount,
          srcLen, tgtLen,
        )
      : _profileScorePure(
          source.dtype, target.dtype,
          source.nullRate, target.nullRate,
          source.uniqueRate, target.uniqueRate,
          source.valueCount, target.valueCount,
          srcLen, tgtLen,
        );

    const dtypeMatch = source.dtype === target.dtype ? 1 : 0;
    const nullSim = similarity(source.nullRate, target.nullRate);
    const uniqSim = similarity(source.uniqueRate, target.uniqueRate);
    const maxLen = Math.max(srcLen, tgtLen, 1);
    const lenSim = 1 - Math.abs(srcLen - tgtLen) / maxLen;
    const srcCard = source.uniqueRate * source.valueCount;
    const tgtCard = target.uniqueRate * target.valueCount;
    const maxCard = Math.max(srcCard, tgtCard, 1);
    const cardSim = 1 - Math.abs(srcCard - tgtCard) / maxCard;
    const parts = [
      `dtype=${dtypeMatch ? "match" : "mismatch"}`,
      `null_sim=${fmt2(nullSim)}`,
      `uniq_sim=${fmt2(uniqSim)}`,
      `len_sim=${fmt2(lenSim)}`,
      `card_sim=${fmt2(cardSim)}`,
    ];
    return makeScorerResult(total, `Profile comparison: ${parts.join(", ")}`);
  }
}
