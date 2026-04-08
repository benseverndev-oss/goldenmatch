// Vendored string distance utilities.
//
// jaroWinklerSimilarity matches rapidfuzz.distance.JaroWinkler.similarity:
// prefix scaling p=0.1, max prefix length 4, boost applied only when base Jaro
// similarity >= 0.7 (classic Winkler threshold).

export function jaroSimilarity(s1: string, s2: string): number {
  if (s1 === s2) return 1;
  const len1 = s1.length;
  const len2 = s2.length;
  if (len1 === 0 || len2 === 0) return 0;

  const matchWindow = Math.max(0, Math.floor(Math.max(len1, len2) / 2) - 1);
  const s1Matches = new Array<boolean>(len1).fill(false);
  const s2Matches = new Array<boolean>(len2).fill(false);

  let matches = 0;
  for (let i = 0; i < len1; i++) {
    const start = Math.max(0, i - matchWindow);
    const end = Math.min(i + matchWindow + 1, len2);
    for (let j = start; j < end; j++) {
      if (s2Matches[j]) continue;
      if (s1[i] !== s2[j]) continue;
      s1Matches[i] = true;
      s2Matches[j] = true;
      matches++;
      break;
    }
  }

  if (matches === 0) return 0;

  // Count transpositions (half the out-of-order matched chars)
  let k = 0;
  let transpositions = 0;
  for (let i = 0; i < len1; i++) {
    if (!s1Matches[i]) continue;
    while (!s2Matches[k]) k++;
    if (s1[i] !== s2[k]) transpositions++;
    k++;
  }
  transpositions = transpositions / 2;

  return (
    (matches / len1 + matches / len2 + (matches - transpositions) / matches) / 3
  );
}

export function jaroWinklerSimilarity(
  s1: string,
  s2: string,
  prefixScale = 0.1
): number {
  const jaro = jaroSimilarity(s1, s2);
  if (jaro < 0.7) return jaro;

  let prefix = 0;
  const maxPrefix = Math.min(4, s1.length, s2.length);
  for (let i = 0; i < maxPrefix; i++) {
    if (s1[i] === s2[i]) prefix++;
    else break;
  }
  return jaro + prefix * prefixScale * (1 - jaro);
}

// Levenshtein distance (vendored for future use by AliasScorer config or
// other fuzzy matching needs). O(len1 * len2) time, O(min) space.
export function levenshteinDistance(s1: string, s2: string): number {
  if (s1 === s2) return 0;
  if (s1.length === 0) return s2.length;
  if (s2.length === 0) return s1.length;

  // Ensure s1 is the shorter — smaller working array
  if (s1.length > s2.length) [s1, s2] = [s2, s1];
  const m = s1.length;
  const n = s2.length;

  let prev = new Array<number>(m + 1);
  let curr = new Array<number>(m + 1);
  for (let i = 0; i <= m; i++) prev[i] = i;

  for (let j = 1; j <= n; j++) {
    curr[0] = j;
    for (let i = 1; i <= m; i++) {
      const cost = s1[i - 1] === s2[j - 1] ? 0 : 1;
      curr[i] = Math.min(
        prev[i]! + 1, // deletion
        curr[i - 1]! + 1, // insertion
        prev[i - 1]! + cost // substitution
      );
    }
    [prev, curr] = [curr, prev];
  }
  return prev[m]!;
}
