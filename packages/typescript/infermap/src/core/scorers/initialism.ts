// Initialism / abbreviation scorer. Mirrors infermap/scorers/initialism.py.
//
// Matches cases where one field name is an abbreviation formed by taking a
// non-empty prefix of each token from the other, in order. Examples:
//
//   assay_id          <-> ASSI    (ASS + I)
//   confidence_score  <-> CONSC   (CON + SC)
//   relationship_type <-> RELATIT (RELATI + T)
//
// Abstains (returns null) on non-abbreviation pairs.

import type { FieldInfo, ScorerResult } from "../types.js";
import { makeScorerResult } from "../types.js";
import type { Scorer } from "./base.js";

/**
 * Split a field name into lowercase tokens. Handles snake_case, kebab-case,
 * camelCase, PascalCase. Numbers are their own tokens.
 */
export function tokenize(name: string): string[] {
  const cleaned = name.trim().replace(/[_\-. ]+/g, " ");
  const tokens: string[] = [];
  // Match camelCase/PascalCase runs: acronym runs before a CamelWord, Word, or all-caps run, or digits.
  const re = /[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+/g;
  for (const chunk of cleaned.split(/\s+/)) {
    if (!chunk) continue;
    const matches = chunk.match(re);
    if (!matches) continue;
    for (const m of matches) tokens.push(m.toLowerCase());
  }
  return tokens;
}

/**
 * Is *target* a concatenation of non-empty prefixes of *sourceTokens* in
 * order, using every source token exactly once? DP search.
 */
export function isPrefixConcat(target: string, sourceTokens: string[]): boolean {
  const t = target.toLowerCase();
  const nSrc = sourceTokens.length;
  const nTgt = t.length;
  if (nSrc === 0 || nTgt === 0) return false;
  // Guard against pathological inputs — abort if the DP table would be
  // unreasonably large. 200 chars per side is generous for field names.
  if (nTgt > 200 || nSrc > 50) return false;
  // dp[i][j] = can target[0:j] be consumed using sourceTokens[0:i]
  const dp: boolean[][] = Array.from({ length: nSrc + 1 }, () =>
    new Array<boolean>(nTgt + 1).fill(false)
  );
  dp[0]![0] = true;
  for (let i = 1; i <= nSrc; i++) {
    const tok = sourceTokens[i - 1]!;
    for (let j = 1; j <= nTgt; j++) {
      const maxK = Math.min(tok.length, j);
      for (let k = 1; k <= maxK; k++) {
        if (dp[i - 1]![j - k]! && t.slice(j - k, j) === tok.slice(0, k)) {
          dp[i]![j] = true;
          break;
        }
      }
    }
  }
  return dp[nSrc]![nTgt]!;
}

/**
 * Return a score in (0, 1] if one side abbreviates the other, else null.
 */
export function scorePair(nameA: string, nameB: string): number | null {
  const tokA = tokenize(nameA);
  const tokB = tokenize(nameB);
  const joinedA = tokA.join("");
  const joinedB = tokB.join("");
  if (!joinedA || !joinedB) return null;
  if (joinedA === joinedB) return null;

  let long: string;
  let short: string;
  if (isPrefixConcat(joinedB, tokA)) {
    long = joinedA;
    short = joinedB;
  } else if (isPrefixConcat(joinedA, tokB)) {
    long = joinedB;
    short = joinedA;
  } else {
    return null;
  }
  const ratio = short.length / long.length;
  return 0.6 + 0.35 * ratio;
}

export class InitialismScorer implements Scorer {
  readonly name = "InitialismScorer";
  readonly weight = 0.75;

  score(source: FieldInfo, target: FieldInfo): ScorerResult | null {
    const srcName = source.canonicalName ?? source.name;
    const tgtName = target.canonicalName ?? target.name;
    const s = scorePair(srcName, tgtName);
    if (s === null) return null;
    return makeScorerResult(
      s,
      `Initialism/abbreviation match: '${srcName}' <-> '${tgtName}' (score=${s.toFixed(3)})`
    );
  }
}
