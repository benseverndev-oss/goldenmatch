/**
 * Edit-distance / string-similarity primitives — the sanctioned pure-TS
 * fallback for the Rust `goldenmatch-score-core` kernel.
 *
 * These live in their own **zero-import leaf module** (extracted from
 * `core/scorer.ts`, which re-exports them so every existing import keeps
 * working) for one reason: they are the suite's single source of truth for
 * these primitives, and a sibling package that needs them should be able to
 * import ONLY them. `core/scorer.ts` transitively pulls the reference-data
 * tables (given names, surnames, legal forms) and the WASM backend registry —
 * a disproportionate payload for three edit-distance functions, and the reason
 * infermap kept a vendored copy that drifted (single-kernel-collapse R5,
 * `context-network/architecture/single-kernel-collapse-roadmap.md`).
 *
 * Nothing here may grow an import. Edge-safe by construction (no `node:*`).
 */

/**
 * Jaro similarity between two strings.
 *
 * matchWindow = floor(max(lenA, lenB) / 2) - 1
 * Count matches (chars within window) and transpositions.
 * jaro = (m/lenA + m/lenB + (m - t/2) / m) / 3
 */
export function jaro(a: string, b: string): number {
  if (a === b) return 1.0;
  const ca = Array.from(a);
  const cb = Array.from(b);
  const lenA = ca.length;
  const lenB = cb.length;
  if (lenA === 0 || lenB === 0) return 0.0;

  const matchWindow = Math.max(Math.floor(Math.max(lenA, lenB) / 2) - 1, 0);

  const aMatched = new Uint8Array(lenA); // 0 = unmatched
  const bMatched = new Uint8Array(lenB);
  let matches = 0;

  // Find matching characters
  for (let i = 0; i < lenA; i++) {
    const lo = Math.max(0, i - matchWindow);
    const hi = Math.min(lenB - 1, i + matchWindow);
    for (let j = lo; j <= hi; j++) {
      if (bMatched[j] !== 0 || ca[i] !== cb[j]) continue;
      aMatched[i] = 1;
      bMatched[j] = 1;
      matches++;
      break;
    }
  }

  if (matches === 0) return 0.0;

  // Count transpositions
  let transpositions = 0;
  let k = 0;
  for (let i = 0; i < lenA; i++) {
    if (aMatched[i] === 0) continue;
    while (bMatched[k] === 0) k++;
    if (ca[i] !== cb[k]) transpositions++;
    k++;
  }

  return (
    (matches / lenA +
      matches / lenB +
      (matches - Math.floor(transpositions / 2)) / matches) /
    3
  );
}

/**
 * Jaro-Winkler similarity.
 * Adds a bonus for a common prefix of up to 4 characters, scaling factor 0.1.
 *
 * This pure-TS implementation is ALIGNED with rapidfuzz (the Python /
 * score-core / WASM source of truth): codepoint iteration, floored
 * transposition (t/2), and the Winkler prefix bonus applied ONLY above the
 * strict jaro > 0.7 boost threshold (#879 closed the three prior known
 * divergences). A 2005-pair rapidfuzz sweep — repeated-character words,
 * non-BMP/accented codepoints, near-duplicates, multi-token phrases, and real
 * names — measured a max absolute error of 5.6e-17 across jaro_winkler /
 * levenshtein / token_sort, i.e. floating-point-identical to rapidfuzz. The
 * committed regression gate is `tests/parity/scorer-rapidfuzz.test.ts`
 * (fixture from emit_scorer_parity_fixtures.py). The opt-in WASM backend runs
 * the same rapidfuzz kernel, so pure-TS ≈ WASM holds too.
 */
export function jaroWinkler(a: string, b: string): number {
  const jaroSim = jaro(a, b);
  if (jaroSim === 0.0) return 0.0;

  // Common prefix up to 4 chars (codepoints, not UTF-16 code units)
  const ca = Array.from(a);
  const cb = Array.from(b);
  const maxPrefix = Math.min(4, Math.min(ca.length, cb.length));
  let prefix = 0;
  for (let i = 0; i < maxPrefix; i++) {
    if (ca[i] === cb[i]) prefix++;
    else break;
  }

  // rapidfuzz applies the Winkler prefix bonus ONLY when jaro > 0.7 (strict).
  if (jaroSim <= 0.7) return jaroSim;
  return jaroSim + prefix * 0.1 * (1 - jaroSim);
}

/**
 * Levenshtein edit distance (classic DP, 2-row optimization).
 */
export function levenshteinDistance(a: string, b: string): number {
  const ca = Array.from(a);
  const cb = Array.from(b);
  const lenA = ca.length;
  const lenB = cb.length;
  if (lenA === 0) return lenB;
  if (lenB === 0) return lenA;

  // Two-row DP
  let prev = new Uint32Array(lenB + 1);
  let curr = new Uint32Array(lenB + 1);

  for (let j = 0; j <= lenB; j++) prev[j] = j;

  for (let i = 1; i <= lenA; i++) {
    curr[0] = i;
    for (let j = 1; j <= lenB; j++) {
      const cost = ca[i - 1] === cb[j - 1] ? 0 : 1;
      curr[j] = Math.min(
        prev[j]! + 1,      // deletion
        curr[j - 1]! + 1,  // insertion
        prev[j - 1]! + cost, // substitution
      );
    }
    // Swap rows
    [prev, curr] = [curr, prev];
  }

  return prev[lenB]!;
}

/**
 * Normalized Levenshtein similarity: 1 - distance / max(lenA, lenB).
 */
export function levenshteinSimilarity(a: string, b: string): number {
  if (a === b) return 1.0;
  const maxLen = Math.max(Array.from(a).length, Array.from(b).length);
  if (maxLen === 0) return 1.0;
  return 1 - levenshteinDistance(a, b) / maxLen;
}

/**
 * Damerau-Levenshtein edit distance (adjacent-transposition / OSA). A swapped
 * pair of adjacent chars costs ONE edit, not two -- the mirror of Python/Rust
 * rapidfuzz `DamerauLevenshtein` for the short digit strings the `date` scorer
 * compares (score-core `date_similarity`). Three-row DP for the transposition
 * lookback.
 */
export function damerauLevenshteinDistance(a: string, b: string): number {
  const ca = Array.from(a);
  const cb = Array.from(b);
  const lenA = ca.length;
  const lenB = cb.length;
  if (lenA === 0) return lenB;
  if (lenB === 0) return lenA;

  let prevPrev = new Uint32Array(lenB + 1);
  let prev = new Uint32Array(lenB + 1);
  let curr = new Uint32Array(lenB + 1);
  for (let j = 0; j <= lenB; j++) prev[j] = j;

  for (let i = 1; i <= lenA; i++) {
    curr[0] = i;
    for (let j = 1; j <= lenB; j++) {
      const cost = ca[i - 1] === cb[j - 1] ? 0 : 1;
      let v = Math.min(
        prev[j]! + 1, // deletion
        curr[j - 1]! + 1, // insertion
        prev[j - 1]! + cost, // substitution
      );
      // Adjacent transposition: ca[i-1]==cb[j-2] && ca[i-2]==cb[j-1].
      if (i > 1 && j > 1 && ca[i - 1] === cb[j - 2] && ca[i - 2] === cb[j - 1]) {
        v = Math.min(v, prevPrev[j - 2]! + 1);
      }
      curr[j] = v;
    }
    [prevPrev, prev, curr] = [prev, curr, prevPrev];
  }
  return prev[lenB]!;
}
