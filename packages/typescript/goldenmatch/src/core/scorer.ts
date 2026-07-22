/**
 * scorer.ts — Fuzzy scoring module for GoldenMatch.
 * Edge-safe: no Node.js imports, pure TypeScript only.
 *
 * Ports goldenmatch/core/scorer.py. The Python version uses `rapidfuzz`
 * for vectorized NxN scoring. Here we implement all algorithms in pure TS.
 */

import type {
  Row,
  MatchkeyField,
  MatchkeyConfig,
  PairKey,
  ScoredPair,
  BlockResult,
  WeightedMatchkey,
} from "./types.js";
import { makeScoredPair } from "./types.js";
import { pairKey } from "./cluster.js";
import { applyTransforms, soundex } from "./transforms.js";
import { applyNegativeEvidence } from "./autoconfigNegativeEvidence.js";
import { getScorerBackend, WASM_COVERED_SCORERS } from "./wasm/backend.js";
import { areEquivalent } from "./refdata/givenNames.js";
import { isAvailable as surnamesAvailable, surnameRank, surnameIdf } from "./refdata/surnames.js";
import { LEGAL_FORMS } from "./refdata/business.js";

// ---------------------------------------------------------------------------
// Helper: coerce unknown to string | null
// ---------------------------------------------------------------------------

/** Convert unknown value to string or null. */
export function asString(v: unknown): string | null {
  if (v === null || v === undefined) return null;
  if (typeof v === "string") return v;
  return String(v);
}

// ---------------------------------------------------------------------------
// Embedding scorer shim (gap 2 — `embedding` / `record_embedding`)
// ---------------------------------------------------------------------------

/**
 * A synchronous text embedder: maps a string to a dense vector. Real
 * embedders (`src/core/embedder.ts`'s `Embedder`, Vertex/OpenAI/Voyage) are
 * async network clients and CANNOT be reproduced numerically across
 * languages — Python uses Vertex/torch embeddings the TS port can't match
 * bit-for-bit. So the `embedding` / `record_embedding` scorers are an
 * **API-parity** case, not a golden-value case: they exist, route through a
 * pluggable embedder, and compute cosine similarity. Tests inject a
 * deterministic stub embedder (no torch / no Vertex). Without a registered
 * embedder the scorers throw a clear, actionable error rather than the
 * generic "Unknown scorer".
 */
export type SyncTextEmbedder = (text: string) => readonly number[];

let _embedder: SyncTextEmbedder | null = null;

/** Register the synchronous embedder used by the `embedding` /
 *  `record_embedding` scorers. Pass `null` to clear (test isolation). */
export function setSyncEmbedder(embedder: SyncTextEmbedder | null): void {
  _embedder = embedder;
}

/** The currently-registered synchronous embedder, or null. */
export function getSyncEmbedder(): SyncTextEmbedder | null {
  return _embedder;
}

/** Cosine similarity of two vectors. Returns 0 when either has zero norm. */
export function cosineSimilarity(
  a: readonly number[],
  b: readonly number[],
): number {
  let dot = 0;
  let na = 0;
  let nb = 0;
  const n = Math.min(a.length, b.length);
  for (let i = 0; i < n; i++) {
    const av = a[i]!;
    const bv = b[i]!;
    dot += av * bv;
    na += av * av;
    nb += bv * bv;
  }
  const denom = Math.sqrt(na) * Math.sqrt(nb);
  return denom === 0 ? 0 : dot / denom;
}

/** Score two strings via the registered embedder's cosine similarity.
 *  Used by both `embedding` and `record_embedding`. */
function embeddingScore(a: string, b: string): number {
  if (_embedder === null) {
    throw new Error(
      'embedding scorer requires a registered embedder: call ' +
        'setSyncEmbedder(fn) before scoring (real embeddings via Vertex/' +
        'OpenAI/Voyage are async and edge-unsafe; the sync shim accepts a ' +
        'stub for tests). See src/core/scorer.ts.',
    );
  }
  const va = _embedder(a);
  const vb = _embedder(b);
  return cosineSimilarity(va, vb);
}

// ---------------------------------------------------------------------------
// Scoring algorithms — pure TS
// ---------------------------------------------------------------------------

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

/** The 8 packed digits of an ISO-8601 `YYYY-MM-DD` string, or null. Mirror of
 * score-core `iso_date_digits` (strict; ranges not validated). */
function isoDateDigits(s: string): string | null {
  if (s.length !== 10 || s[4] !== "-" || s[7] !== "-") return null;
  const digits = s.slice(0, 4) + s.slice(5, 7) + s.slice(8, 10);
  return /^\d{8}$/.test(digits) ? digits : null;
}

/**
 * Date-aware similarity (#1858). `jaro_winkler` scores unrelated ISO birthdays
 * 0.80+; this parses `YYYY-MM-DD` and uses Damerau-Levenshtein over the canonical
 * digits so a typo (0.90) is far above an unrelated date (0.00). Mirrors
 * score-core `date_similarity` / the Python `_date_similarity_py`. Non-ISO input
 * degrades to `levenshtein`.
 */
export function dateSimilarity(a: string, b: string): number {
  const da = isoDateDigits(a);
  const db = isoDateDigits(b);
  if (da !== null && db !== null) {
    const d = damerauLevenshteinDistance(da, db);
    if (d === 0) return 1.0;
    if (d === 1) return 0.9;
    if (d === 2) return 0.75;
    return 0.0;
  }
  return levenshteinSimilarity(a, b);
}

/**
 * Indel (insertion+deletion) edit distance.
 *
 * Like Levenshtein but without substitutions — a substitution costs 2
 * (one delete + one insert) instead of 1. This matches the distance
 * metric used by rapidfuzz's Indel ratio, which underlies
 * `rapidfuzz.fuzz.token_sort_ratio` in Python.
 */
export function indelDistance(a: string, b: string): number {
  if (a === b) return 0;
  const ca = Array.from(a);
  const cb = Array.from(b);
  const m = ca.length;
  const n = cb.length;
  if (m === 0) return n;
  if (n === 0) return m;
  let prev = new Uint32Array(n + 1);
  let curr = new Uint32Array(n + 1);
  for (let j = 0; j <= n; j++) prev[j] = j;
  for (let i = 1; i <= m; i++) {
    curr[0] = i;
    for (let j = 1; j <= n; j++) {
      if (ca[i - 1] === cb[j - 1]) {
        curr[j] = prev[j - 1]!;
      } else {
        // Only insert or delete allowed — cost 1 each. No substitution.
        curr[j] = Math.min(prev[j]! + 1, curr[j - 1]! + 1);
      }
    }
    [prev, curr] = [curr, prev];
  }
  return prev[n]!;
}

/**
 * Indel normalized similarity: `1 - d_indel / (len_a + len_b)`.
 * Matches rapidfuzz's `Indel.normalized_similarity`.
 */
export function indelSimilarity(a: string, b: string): number {
  const total = Array.from(a).length + Array.from(b).length;
  if (total === 0) return 1.0;
  return 1 - indelDistance(a, b) / total;
}

/**
 * Token sort ratio, rapidfuzz-compatible.
 *
 * Matches `rapidfuzz.fuzz.token_sort_ratio`:
 * 1. Lowercase both strings.
 * 2. Strip non-alphanumeric characters (replace with whitespace).
 * 3. Split on whitespace, drop empties, sort tokens, rejoin with single space.
 * 4. Compare via Indel normalized similarity (NOT Levenshtein).
 *
 * Python reference: for ("John Smith", "Smith Johnson") returns ~0.8571.
 */
export function tokenSortRatio(a: string, b: string): number {
  const normalize = (s: string): string =>
    s
      .toLowerCase()
      .replace(/[^a-z0-9\s]/g, " ")
      .trim()
      .split(/\s+/)
      .filter(Boolean)
      .sort()
      .join(" ");
  return indelSimilarity(normalize(a), normalize(b));
}

/**
 * Soundex match: 1.0 if soundex codes equal, else 0.0.
 */
export function soundexMatch(a: string, b: string): number {
  // Empty code (no phonetic content) never matches -- not even another empty
  // code -- so placeholder columns don't mega-cluster. Byte-for-byte with
  // score-core `soundex_match` (id 6) + the Python `_soundex_score_single`.
  const ca = soundex(a);
  return ca !== "" && ca === soundex(b) ? 1.0 : 0.0;
}

// ---------------------------------------------------------------------------
// Bloom filter / PPRL scorers
// ---------------------------------------------------------------------------

/** Convert a hex string to a Uint8Array of bytes. */
function hexToBytes(hex: string): Uint8Array {
  const len = hex.length >>> 1;
  const bytes = new Uint8Array(len);
  for (let i = 0; i < len; i++) {
    bytes[i] = parseInt(hex.slice(i * 2, i * 2 + 2), 16);
  }
  return bytes;
}

/** Count the number of set bits (popcount) in a byte array. */
function popcount(bytes: Uint8Array): number {
  let count = 0;
  for (let i = 0; i < bytes.length; i++) {
    let b = bytes[i]!;
    // Brian Kernighan's algorithm
    while (b !== 0) {
      b &= b - 1;
      count++;
    }
  }
  return count;
}

/** Count set bits in bitwise AND of two byte arrays. */
function popcountAnd(a: Uint8Array, b: Uint8Array): number {
  const len = Math.min(a.length, b.length);
  let count = 0;
  for (let i = 0; i < len; i++) {
    let v = (a[i]! & b[i]!);
    while (v !== 0) {
      v &= v - 1;
      count++;
    }
  }
  return count;
}

/** Count set bits in bitwise OR of two byte arrays. */
function popcountOr(a: Uint8Array, b: Uint8Array): number {
  const maxLen = Math.max(a.length, b.length);
  let count = 0;
  for (let i = 0; i < maxLen; i++) {
    let v = ((a[i] ?? 0) | (b[i] ?? 0));
    while (v !== 0) {
      v &= v - 1;
      count++;
    }
  }
  return count;
}

/**
 * Dice coefficient on two hex-encoded bloom filters.
 * 2 * intersection / (popcount_a + popcount_b)
 */
export function diceCoefficient(a: string, b: string): number {
  const bytesA = hexToBytes(a);
  const bytesB = hexToBytes(b);
  const pcA = popcount(bytesA);
  const pcB = popcount(bytesB);
  const total = pcA + pcB;
  if (total === 0) return 0.0;
  const intersection = popcountAnd(bytesA, bytesB);
  return (2 * intersection) / total;
}

/**
 * Jaccard similarity on two hex-encoded bloom filters.
 * intersection / union of bits
 */
export function jaccardSimilarity(a: string, b: string): number {
  const bytesA = hexToBytes(a);
  const bytesB = hexToBytes(b);
  const intersection = popcountAnd(bytesA, bytesB);
  const union = popcountOr(bytesA, bytesB);
  if (union === 0) return 0.0;
  return intersection / union;
}

/** Left-pad an odd-length hex to even and strip an optional `0x`/`0X` prefix
 * (mirrors score-core `norm_phash_hex`; the prefix strip needs length >= 2). */
function normPhashHex(s: string): string {
  let h = s;
  if (h.length >= 2 && (h.startsWith("0x") || h.startsWith("0X"))) h = h.slice(2);
  if (h.length % 2 !== 0) h = "0" + h;
  return h;
}

/** Strictly decode an even-length ASCII-hex string to bytes, or null on any
 * non-hex char (mirrors score-core `decode_hex`, whose `to_digit(16)?` fails on
 * non-hex; `hexToBytes`/`parseInt` would silently coerce bad hex to 0). */
function decodeHexStrict(hex: string): Uint8Array | null {
  if (hex.length % 2 !== 0 || !/^[0-9a-fA-F]*$/.test(hex)) return null;
  const out = new Uint8Array(hex.length >>> 1);
  for (let i = 0; i < out.length; i++) {
    out[i] = parseInt(hex.slice(i * 2, i * 2 + 2), 16);
  }
  return out;
}

/** Popcount of a single byte (Brian Kernighan). */
function popcountByte(b: number): number {
  let count = 0;
  while (b !== 0) {
    b &= b - 1;
    count++;
  }
  return count;
}

/**
 * Perceptual-hash similarity on two hex pHash strings: `1 - hamming/nbits`, where
 * `nbits` counts over the LONGER hash (the tail of the longer XORs against 0, so
 * every set bit there counts as a difference). Byte-exact with score-core
 * `phash_similarity` (score_one id 11) and Python `_phash_score_single`. A non-hex
 * value on either side -> 0.0.
 */
export function phashSimilarity(a: string, b: string): number {
  const pa = decodeHexStrict(normPhashHex(a));
  const pb = decodeHexStrict(normPhashHex(b));
  if (pa === null || pb === null) return 0.0;
  const nbits = Math.max(pa.length, pb.length) * 8;
  if (nbits === 0) return 0.0;
  const m = Math.min(pa.length, pb.length);
  let dist = 0;
  for (let i = 0; i < m; i++) dist += popcountByte(pa[i]! ^ pb[i]!);
  for (let i = m; i < pa.length; i++) dist += popcountByte(pa[i]!);
  for (let i = m; i < pb.length; i++) dist += popcountByte(pb[i]!);
  return 1.0 - dist / nbits;
}

/** Parse a radial-variance profile: 2 hex chars per bin as a SIGNED byte
 * (`b >= 128 -> b - 256`), `0x`/`0X` prefix tolerated, an odd trailing char
 * dropped. Any non-hex char in the used prefix -> null (mirrors score-core
 * `radial_from_hex`, whose `to_digit(16)?` fails there). */
function radialFromHex(s: string): number[] | null {
  let h = s;
  if (h.length >= 2 && (h.startsWith("0x") || h.startsWith("0X"))) h = h.slice(2);
  const usable = h.length - (h.length % 2);
  if (!/^[0-9a-fA-F]*$/.test(h.slice(0, usable))) return null;
  const out: number[] = [];
  for (let i = 0; i < usable; i += 2) {
    const byte = parseInt(h.slice(i, i + 2), 16);
    out.push(byte >= 128 ? byte - 256 : byte);
  }
  return out;
}

/** Pearson correlation of two equal-length int sequences; 0.0 if either is
 * constant. Mean is `sum/len` (integer sum, one divide) and every reduction
 * accumulates left-to-right, mirroring score-core `pearson` / Python `_pearson`. */
function pearson(a: readonly number[], b: readonly number[]): number {
  const n = a.length;
  let sa = 0;
  let sb = 0;
  for (let i = 0; i < n; i++) {
    sa += a[i]!;
    sb += b[i]!;
  }
  const ma = sa / n;
  const mb = sb / n;
  let da = 0;
  let db = 0;
  for (let i = 0; i < n; i++) {
    const dax = a[i]! - ma;
    da += dax * dax;
    const dbx = b[i]! - mb;
    db += dbx * dbx;
  }
  if (da === 0 || db === 0) return 0.0;
  let num = 0;
  for (let i = 0; i < n; i++) num += (a[i]! - ma) * (b[i]! - mb);
  return num / Math.sqrt(da * db);
}

/**
 * Radial-variance profile similarity (score_one id 13): the max Pearson over
 * every cyclic angular shift of `b`, clamped to [0, 1]. Mismatched/empty profiles
 * or a non-hex value -> 0.0. Mirrors score-core `radial_similarity` /
 * `radial_align` / Python `radial_align_similarity`; the f64 reductions can differ
 * ~1 ULP from the WASM kernel's (possible SIMD) order, so parity is ~4dp not
 * byte-exact.
 */
export function radialSimilarity(a: string, b: string): number {
  const x = radialFromHex(a);
  const y = radialFromHex(b);
  if (x === null || y === null) return 0.0;
  const la = x.length;
  if (la === 0 || y.length !== la) return 0.0;
  let best = -1.0;
  const rotated = new Array<number>(la);
  for (let shift = 0; shift < la; shift++) {
    for (let k = 0; k < la; k++) rotated[k] = y[(shift + k) % la]!;
    const c = pearson(x, rotated);
    if (c > best) best = c;
  }
  return Math.min(1.0, Math.max(0.0, best));
}

/** Parse a concatenated audio fingerprint: 8 hex chars per u32 word, `0x`/`0X`
 * prefix tolerated, trailing chars past the last full word dropped. Any non-hex
 * char in the used prefix -> null (mirrors score-core `audio_fp_from_hex`). */
function audioFpFromHex(s: string): number[] | null {
  let h = s;
  if (h.length >= 2 && (h.startsWith("0x") || h.startsWith("0X"))) h = h.slice(2);
  const usable = h.length - (h.length % 8);
  if (!/^[0-9a-fA-F]*$/.test(h.slice(0, usable))) return null;
  const out: number[] = [];
  for (let i = 0; i < usable; i += 8) {
    out.push(parseInt(h.slice(i, i + 8), 16)); // 8 hex digits = one u32 word
  }
  return out;
}

/** Popcount of a 32-bit word (SWAR; `>>> 0` normalizes the signed XOR result). */
function popcount32(v: number): number {
  v = v >>> 0;
  v = v - ((v >>> 1) & 0x55555555);
  v = (v & 0x33333333) + ((v >>> 2) & 0x33333333);
  return (((v + (v >>> 4)) & 0x0f0f0f0f) * 0x01010101) >>> 24;
}

/** Best (minimum) bit-error-rate over all frame offsets of two audio
 * fingerprints; mirrors score-core `audio_ber_aligned` (need = min(8, la, lb),
 * 32 bits/band, `bits/(overlap*32)` per offset). Empty input -> BER 1.0. */
function audioBerAligned(a: readonly number[], b: readonly number[]): number {
  const la = a.length;
  const lb = b.length;
  if (la === 0 || lb === 0) return 1.0;
  const need = Math.min(8, la, lb);
  const nb = 32.0; // AUDIO_BANDS - 1
  let best = 1.0;
  for (let off = -(lb - 1); off < la; off++) {
    const lo = Math.max(off, 0);
    const hi = Math.min(la, off + lb);
    const overlap = hi - lo;
    if (overlap < need) continue;
    let bits = 0;
    for (let i = lo; i < hi; i++) bits += popcount32(a[i]! ^ b[i - off]!);
    const ber = bits / (overlap * nb);
    if (ber < best) best = ber;
  }
  return best;
}

/**
 * Audio-fingerprint similarity (score_one id 14): `1 - best BER`, the minimum
 * bit-error-rate over every frame offset of two hex audio fingerprints. Byte-exact
 * with score-core `audio_fp_similarity` / Python `_audio_fp_score_single` -- the BER
 * numerator is an integer popcount sum and the rate is a single f64 divide (no
 * reduction-order divergence). A non-hex value on either side -> 0.0.
 */
export function audioFpSimilarity(a: string, b: string): number {
  const x = audioFpFromHex(a);
  const y = audioFpFromHex(b);
  if (x === null || y === null) return 0.0;
  return 1.0 - audioBerAligned(x, y);
}

// ---------------------------------------------------------------------------
// initialism_match (abbreviation matcher)
// ---------------------------------------------------------------------------

/** Remove every `(...)` group — a `(` with the nearest following `)` (and the
 *  text between); a `(` with no later `)` is kept literally. Mirrors score-core
 *  `strip_parentheticals` / Python `re.sub(r"\([^)]*\)", "", s)`. */
function stripParentheticals(s: string): string {
  const chars = Array.from(s);
  let out = "";
  let i = 0;
  while (i < chars.length) {
    if (chars[i] === "(") {
      const rel = chars.slice(i + 1).indexOf(")");
      if (rel !== -1) {
        i += rel + 2; // skip "(...)" inclusive
        continue;
      }
    }
    out += chars[i]!;
    i++;
  }
  return out;
}

/** Python `token.strip().rstrip(".,").lower()` — the per-token legal-form key. */
function normalizeTokenForLegal(token: string): string {
  return token.trim().replace(/[.,]+$/, "").toLowerCase();
}

/** Python `str.isupper()`: at least one cased char and no lowercase cased char. */
function pyIsUpper(s: string): boolean {
  let hasCased = false;
  for (const c of s) {
    const lower = c.toLowerCase();
    const upper = c.toUpperCase();
    if (lower === upper) continue; // uncased
    hasCased = true;
    if (c === lower && c !== upper) return false; // a lowercase cased char
  }
  return hasCased;
}

/** Python `str.isalpha()`: non-empty and every char Unicode-alphabetic. */
function pyIsAlpha(s: string): boolean {
  if (s.length === 0) return false;
  for (const c of s) {
    if (!/\p{Alphabetic}/u.test(c)) return false;
  }
  return true;
}

/**
 * Derive the initialism (acronym) of a business name, dropping legal-form tokens.
 * A single already-acronym token (uppercase, alphabetic, 2..6 chars) passes
 * through; otherwise the first ASCII letter of each surviving token (>= 2).
 * Mirrors score-core `derive_initialism` / Python `core.acronym.derive_initialism`.
 */
export function deriveInitialism(
  text: string,
  legalForms: ReadonlySet<string> = LEGAL_FORMS,
): string {
  const stripped = stripParentheticals(text);
  const cleaned: string[] = [];
  for (const token of stripped.match(/\S+/gu) ?? []) {
    if (!/[A-Za-z]/.test(token)) continue; // punctuation-only / digits-only
    if (legalForms.has(normalizeTokenForLegal(token))) continue;
    cleaned.push(token);
  }
  if (cleaned.length === 1) {
    const tok = cleaned[0]!;
    const n = Array.from(tok).length;
    if (pyIsUpper(tok) && pyIsAlpha(tok) && n >= 2 && n <= 6) return tok.toUpperCase();
    return "";
  }
  if (cleaned.length === 0) return "";
  let initials = "";
  for (const token of cleaned) {
    const m = token.match(/[A-Za-z]/);
    if (m) initials += m[0]!.toUpperCase();
  }
  if (Array.from(initials).length < 2) return "";
  return initials;
}

/**
 * `initialism_match` scorer: 1.0 iff one value's derived initialism equals the
 * other RAW value, or the two derived initialisms match. Byte-exact with
 * score-core `initialism_match` / Python `_initialism_match_single`. The legal-form
 * table is caller-supplied (defaults to the ported `LEGAL_FORMS`), so the WASM
 * kernel -- seeded with the SAME table at `enableWasm()` -- matches byte-for-byte.
 */
export function initialismMatch(
  a: string,
  b: string,
  legalForms: ReadonlySet<string> = LEGAL_FORMS,
): number {
  const ia = deriveInitialism(a, legalForms);
  const ib = deriveInitialism(b, legalForms);
  const matched =
    (ia !== "" && ia === b) ||
    (ib !== "" && a === ib) ||
    (ia !== "" && ib !== "" && ia === ib);
  return matched ? 1.0 : 0.0;
}

/**
 * Padded character q-gram set of a raw string (Python parity:
 * core/scorer.py::_qgram_set). Lowercases, pads with `n-1` `#` sentinels on
 * each side, then returns the FULL set of length-`n` substrings.
 */
function qgramSet(s: string, n: number): Set<string> {
  const padded = "#".repeat(n - 1) + s.toLowerCase() + "#".repeat(n - 1);
  const out = new Set<string>();
  for (let i = 0; i + n <= padded.length; i++) out.add(padded.slice(i, i + n));
  return out;
}

/**
 * Character q-gram Jaccard similarity (Python parity:
 * core/scorer.py::_qgram_score_single). Identical strings (incl. both empty)
 * score 1.0; an empty q-gram union scores 0.0; otherwise `|A∩B| / |A∪B|` over
 * the padded length-`n` substring sets. Pure set math — byte-identical to the
 * Python source, no rapidfuzz involved.
 */
export function qgramScore(a: string, b: string, n = 3): number {
  if (a === b) return 1.0;
  const setA = qgramSet(a, n);
  const setB = qgramSet(b, n);
  let inter = 0;
  for (const g of setA) if (setB.has(g)) inter++;
  const union = setA.size + setB.size - inter;
  if (union === 0) return 0.0;
  return inter / union;
}

// ---------------------------------------------------------------------------
// Ensemble scorer
// ---------------------------------------------------------------------------

/**
 * Ensemble scorer: combines jaro_winkler, token_sort, and soundex_match * 0.8.
 * Takes element-wise max of all three.
 */
export function ensembleScore(a: string, b: string): number {
  const jw = jaroWinkler(a, b);
  const ts = tokenSortRatio(a, b);
  const sx = soundexMatch(a, b) * 0.8;
  return Math.max(jw, ts, sx);
}

// ---------------------------------------------------------------------------
// Public: scoreField
// ---------------------------------------------------------------------------

/**
 * Score two field values using the specified scorer.
 * Returns null if either value is null.
 */
export function scoreField(
  valA: string | null,
  valB: string | null,
  scorer: string,
): number | null {
  if (valA === null || valB === null) return null;

  switch (scorer) {
    case "exact":
      return valA === valB ? 1.0 : 0.0;
    case "jaro_winkler":
      return jaroWinkler(valA, valB);
    case "levenshtein":
      return levenshteinSimilarity(valA, valB);
    case "date":
      return dateSimilarity(valA, valB);
    case "token_sort":
      return tokenSortRatio(valA, valB);
    case "soundex_match":
      return soundexMatch(valA, valB);
    case "dice":
      return diceCoefficient(valA, valB);
    case "jaccard":
      return jaccardSimilarity(valA, valB);
    case "qgram":
      return qgramScore(valA, valB);
    case "phash":
      return phashSimilarity(valA, valB);
    case "radial":
      return radialSimilarity(valA, valB);
    case "audio_fp":
      return audioFpSimilarity(valA, valB);
    case "initialism_match":
      return initialismMatch(valA, valB);
    case "ensemble":
      return ensembleScore(valA, valB);
    case "embedding":
    case "record_embedding":
      // API parity (not golden-value): route through the registered embedder
      // shim and compute cosine similarity. record_embedding embeds the full
      // record string; at the field level both behave identically (embed the
      // field value, cosine-compare). See setSyncEmbedder.
      return embeddingScore(valA, valB);
    case "given_name_aliased_jw":
      // Alias-aware exact bonus: known forms of the same name -> 1.0,
      // else plain Jaro-Winkler. Mirrors Python GivenNameAliasedJW.score_pair.
      return areEquivalent(valA, valB) ? 1.0 : jaroWinkler(valA, valB);
    case "name_freq_weighted_jw": {
      // Jaro-Winkler modulated by census surname IDF in the borderline zone.
      // Mirrors Python NameFreqWeightedJW.score_pair.
      const jw = jaroWinkler(valA, valB);
      if (jw >= 0.95 || jw < 0.7) return jw;
      if (!surnamesAvailable()) return jw;
      // OOV gate: a name absent from the table falls back to plain JW (a typo
      // of a common name shouldn't get credit-by-rarity).
      if (surnameRank(valA) === null || surnameRank(valB) === null) return jw;
      const idfA = surnameIdf(valA);
      const idfB = surnameIdf(valB);
      if (idfA === null || idfB === null) return jw;
      const idf = (idfA + idfB) / 2;
      const weight = 0.6 + 0.4 * idf;
      return jw * weight;
    }
    default:
      throw new Error(`Unknown scorer: ${JSON.stringify(scorer)}`);
  }
}

// ---------------------------------------------------------------------------
// Public: scorePair
// ---------------------------------------------------------------------------

/**
 * Score a pair of rows across all fields using weighted aggregation.
 * Fields that produce null scores are excluded. If all null -> 0.0.
 */
export function scorePair(
  rowA: Row,
  rowB: Row,
  fields: readonly MatchkeyField[],
): number {
  let weightedSum = 0;
  let weightSum = 0;
  for (const f of fields) {
    const valA = applyTransforms(asString(rowA[f.field]), f.transforms);
    const valB = applyTransforms(asString(rowB[f.field]), f.transforms);
    const fieldScore = scoreField(valA, valB, f.scorer);
    if (fieldScore !== null) {
      weightedSum += fieldScore * f.weight;
      weightSum += f.weight;
    }
  }
  return weightSum === 0 ? 0 : weightedSum / weightSum;
}

// ---------------------------------------------------------------------------
// NxN score matrix
// ---------------------------------------------------------------------------

/**
 * Build an NxN score matrix for a list of values using a scorer.
 * Symmetric: matrix[i][j] === matrix[j][i]. Diagonal is 0.
 */
export function scoreMatrix(
  values: (string | null)[],
  scorerName: string,
): number[][] {
  const n = values.length;
  const backend = getScorerBackend();

  // Opt-in WASM fast path: ONE boundary crossing per NxN block, covered
  // scorers only. Nulls are masked to 0 here (the backend never sees them).
  if (backend !== null && WASM_COVERED_SCORERS.has(scorerName)) {
    const SEP = "\x1e"; // record-separator; never appears in scored field data
    const clean = values.map((v) => (v ?? "").replaceAll(SEP, ""));
    const flat = backend.scoreMatrix(clean, scorerName);
    const matrix: number[][] = Array.from({ length: n }, () =>
      new Array<number>(n).fill(0),
    );
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        const masked = values[i] === null || values[j] === null;
        const s = masked ? 0 : flat[i * n + j]!;
        matrix[i]![j] = s;
        matrix[j]![i] = s;
      }
    }
    return matrix;
  }

  // Pure-TS default (unchanged).
  const matrix: number[][] = Array.from({ length: n }, () => new Array<number>(n).fill(0));
  for (let i = 0; i < n; i++) {
    for (let j = i + 1; j < n; j++) {
      const s = scoreField(values[i]!, values[j]!, scorerName) ?? 0;
      matrix[i]![j] = s;
      matrix[j]![i] = s;
    }
  }
  return matrix;
}

// ---------------------------------------------------------------------------
// Exact score matrix (hash-based grouping, O(n))
// ---------------------------------------------------------------------------

function exactScoreMatrix(values: (string | null)[]): number[][] {
  const n = values.length;
  const matrix: number[][] = Array.from({ length: n }, () => new Array<number>(n).fill(0));
  // Group indices by value
  const groups = new Map<string, number[]>();
  for (let i = 0; i < n; i++) {
    const v = values[i];
    if (v != null) {
      const existing = groups.get(v);
      if (existing !== undefined) {
        existing.push(i);
      } else {
        groups.set(v, [i]);
      }
    }
  }
  groups.forEach((indices) => {
    if (indices.length > 1) {
      for (let a = 0; a < indices.length; a++) {
        for (let b = a + 1; b < indices.length; b++) {
          matrix[indices[a]!]![indices[b]!] = 1.0;
          matrix[indices[b]!]![indices[a]!] = 1.0;
        }
      }
    }
  });
  return matrix;
}

/** Soundex score matrix: group by soundex code, 1.0 for same code. Empty codes
 * (no phonetic content) map to null so they match NOTHING -- the empty-guard that
 * mirrors score-core `soundex_match` and keeps placeholders from mega-clustering. */
function soundexScoreMatrix(values: (string | null)[]): number[][] {
  const codes = values.map((v) => {
    if (v === null) return null;
    const c = soundex(v);
    return c === "" ? null : c;
  });
  return exactScoreMatrix(codes);
}

/** Ensemble score matrix: max of jaro_winkler, token_sort, soundex*0.8 */
function ensembleScoreMatrix(values: (string | null)[]): number[][] {
  const n = values.length;
  const clean = values.map((v) => v ?? "");
  const jw: number[][] = Array.from({ length: n }, () => new Array<number>(n).fill(0));
  const ts: number[][] = Array.from({ length: n }, () => new Array<number>(n).fill(0));
  const sx = soundexScoreMatrix(values);
  const result: number[][] = Array.from({ length: n }, () => new Array<number>(n).fill(0));

  for (let i = 0; i < n; i++) {
    for (let j = i + 1; j < n; j++) {
      if (values[i] === null || values[j] === null) continue;
      jw[i]![j] = jaroWinkler(clean[i]!, clean[j]!);
      jw[j]![i] = jw[i]![j]!;
      ts[i]![j] = tokenSortRatio(clean[i]!, clean[j]!);
      ts[j]![i] = ts[i]![j]!;
    }
  }

  for (let i = 0; i < n; i++) {
    for (let j = i + 1; j < n; j++) {
      const val = Math.max(jw[i]![j]!, ts[i]![j]!, sx[i]![j]! * 0.8);
      result[i]![j] = val;
      result[j]![i] = val;
    }
  }
  return result;
}

/**
 * Build an NxN null mask: true where either value is null.
 */
function buildNullMask(values: (string | null)[]): boolean[][] {
  const n = values.length;
  const mask: boolean[][] = Array.from({ length: n }, () => new Array<boolean>(n).fill(false));
  for (let i = 0; i < n; i++) {
    if (values[i] === null) {
      for (let j = 0; j < n; j++) {
        mask[i]![j] = true;
        mask[j]![i] = true;
      }
    }
  }
  return mask;
}

/**
 * Build the appropriate score matrix for a scorer name.
 */
function buildScoreMatrix(values: (string | null)[], scorerName: string): number[][] {
  switch (scorerName) {
    case "exact":
      return exactScoreMatrix(values);
    case "soundex_match":
      return soundexScoreMatrix(values);
    case "ensemble":
      return ensembleScoreMatrix(values);
    default:
      return scoreMatrix(values, scorerName);
  }
}

// ---------------------------------------------------------------------------
// Get transformed values for a field from block rows
// ---------------------------------------------------------------------------

function getTransformedValues(
  rows: readonly Row[],
  field: MatchkeyField,
): (string | null)[] {
  return rows.map((row) => {
    const raw = asString(row[field.field]);
    return applyTransforms(raw, field.transforms);
  });
}

// ---------------------------------------------------------------------------
// Public: findExactMatches
// ---------------------------------------------------------------------------

/**
 * Find exact matches by grouping rows on matchkey columns.
 * Builds a composite key from all matchkey fields (with transforms applied),
 * groups rows sharing the same key, and returns all pairs with score 1.0.
 *
 * Rows must have a `__row_id__` field.
 */
export function findExactMatches(
  rows: readonly Row[],
  mk: MatchkeyConfig,
): ScoredPair[] {
  if (rows.length < 2) return [];

  // Build composite matchkey for each row
  const groups = new Map<string, number[]>();
  for (let i = 0; i < rows.length; i++) {
    const row = rows[i]!;
    const rowId = row["__row_id__"] as number;
    // Build key from all fields
    let keyParts: (string | null)[] = [];
    let hasNull = false;
    for (const f of mk.fields) {
      const raw = asString(row[f.field]);
      const transformed = applyTransforms(raw, f.transforms);
      if (transformed === null) {
        hasNull = true;
        break;
      }
      keyParts.push(transformed);
    }
    // Skip rows with any null field (nulls don't match)
    if (hasNull) continue;

    const key = keyParts.join("\x00"); // null byte separator
    const existing = groups.get(key);
    if (existing !== undefined) {
      existing.push(rowId);
    } else {
      groups.set(key, [rowId]);
    }
  }

  // Extract pairs from groups
  const pairs: ScoredPair[] = [];
  groups.forEach((members) => {
    if (members.length < 2) return;
    for (let i = 0; i < members.length; i++) {
      for (let j = i + 1; j < members.length; j++) {
        pairs.push(makeScoredPair(members[i]!, members[j]!, 1.0));
      }
    }
  });
  return pairs;
}

// ---------------------------------------------------------------------------
// Public: findFuzzyMatches
// ---------------------------------------------------------------------------

/**
 * Find fuzzy matches within a block of rows (NxN scoring).
 *
 * Implements early termination:
 * - Score cheap fields (exact/soundex) first
 * - Check if max possible score can reach threshold
 * - Score expensive fuzzy fields only for promising pairs
 *
 * Rows must have a `__row_id__` field.
 */
export function findFuzzyMatches(
  rows: readonly Row[],
  mk: MatchkeyConfig,
  excludePairs?: ReadonlySet<PairKey>,
  preScoredPairs?: readonly ScoredPair[],
): ScoredPair[] {
  // findFuzzyMatches only runs for weighted/probabilistic matchkeys
  // (exact is handled via findExactMatches). Exact has no threshold.
  const threshold = mk.type === "exact" ? 1.0 : (mk.threshold ?? 0.85);

  // Fast path: pre-scored pairs (from ANN blocking)
  if (preScoredPairs !== undefined) {
    const results: ScoredPair[] = [];
    for (const p of preScoredPairs) {
      if (p.score < threshold) continue;
      const idA = Math.min(p.idA, p.idB);
      const idB = Math.max(p.idA, p.idB);
      const key = pairKey(idA, idB);
      if (excludePairs !== undefined && excludePairs.has(key)) continue;
      results.push(makeScoredPair(idA, idB, p.score));
    }
    return results;
  }

  const n = rows.length;
  if (n < 2) return [];

  const rowIds = rows.map((r) => r["__row_id__"] as number);

  // Separate cheap (exact + soundex) from expensive (fuzzy) fields
  const cheapFields = mk.fields.filter(
    (f) => f.scorer === "exact" || f.scorer === "soundex_match",
  );
  const fuzzyFields = mk.fields.filter(
    (f) => f.scorer !== "exact" && f.scorer !== "soundex_match" && f.scorer !== "record_embedding",
  );

  const totalWeight = mk.fields.reduce((sum, f) => sum + f.weight, 0);
  if (totalWeight === 0) return [];

  // Phase 1: Score cheap fields and build null masks
  // cheapNumerator[i][j] = sum(fieldScore * weight) for cheap fields
  // cheapDenominator[i][j] = sum(weight) for non-null cheap fields
  const cheapNumerator: number[][] = Array.from({ length: n }, () => new Array<number>(n).fill(0));
  const cheapDenominator: number[][] = Array.from({ length: n }, () => new Array<number>(n).fill(0));

  for (const f of cheapFields) {
    const values = getTransformedValues(rows, f);
    const nullMask = buildNullMask(values);
    const scores =
      f.scorer === "exact"
        ? exactScoreMatrix(values)
        : soundexScoreMatrix(values);

    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        if (!nullMask[i]![j]!) {
          cheapNumerator[i]![j]! += scores[i]![j]! * f.weight;
          cheapNumerator[j]![i]! = cheapNumerator[i]![j]!;
          cheapDenominator[i]![j]! += f.weight;
          cheapDenominator[j]![i]! = cheapDenominator[i]![j]!;
        }
      }
    }
  }

  // Phase 2: Early termination check
  const fuzzyTotalWeight = fuzzyFields.reduce((sum, f) => sum + f.weight, 0);

  // Track which pairs are impossible (can't reach threshold)
  const impossible: boolean[][] = Array.from({ length: n }, () => new Array<boolean>(n).fill(false));

  let combined: number[][];

  if (fuzzyFields.length === 0) {
    // No fuzzy fields — just use cheap scores
    combined = Array.from({ length: n }, () => new Array<number>(n).fill(0));
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        combined[i]![j] =
          cheapDenominator[i]![j]! > 0
            ? cheapNumerator[i]![j]! / cheapDenominator[i]![j]!
            : 0;
        combined[j]![i] = combined[i]![j]!;
      }
    }
  } else {
    // Check which pairs can possibly reach threshold
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        const maxNum = cheapNumerator[i]![j]! + fuzzyTotalWeight;
        const maxDen = cheapDenominator[i]![j]! + fuzzyTotalWeight;
        const maxPossible = maxDen > 0 ? maxNum / maxDen : 0;
        if (maxPossible < threshold) {
          impossible[i]![j] = true;
          impossible[j]![i] = true;
        }
      }
    }

    // Phase 3: Score fuzzy fields with intra-field early termination
    const fuzzyNumerator: number[][] = Array.from({ length: n }, () => new Array<number>(n).fill(0));
    const fuzzyDenominator: number[][] = Array.from({ length: n }, () => new Array<number>(n).fill(0));

    for (let fIdx = 0; fIdx < fuzzyFields.length; fIdx++) {
      const f = fuzzyFields[fIdx]!;
      const values = getTransformedValues(rows, f);
      const nullMask = buildNullMask(values);
      const scores = buildScoreMatrix(values, f.scorer);

      for (let i = 0; i < n; i++) {
        for (let j = i + 1; j < n; j++) {
          if (!nullMask[i]![j]!) {
            fuzzyNumerator[i]![j]! += scores[i]![j]! * f.weight;
            fuzzyNumerator[j]![i] = fuzzyNumerator[i]![j]!;
            fuzzyDenominator[i]![j]! += f.weight;
            fuzzyDenominator[j]![i] = fuzzyDenominator[i]![j]!;
          }
        }
      }

      // Intra-field early termination: check if any pair can still reach threshold
      const remainingWeight = fuzzyFields
        .slice(fIdx + 1)
        .reduce((sum, ff) => sum + ff.weight, 0);

      if (remainingWeight > 0) {
        let anyCanReach = false;
        for (let i = 0; i < n && !anyCanReach; i++) {
          for (let j = i + 1; j < n && !anyCanReach; j++) {
            if (impossible[i]![j]!) continue;
            const totalNum =
              cheapNumerator[i]![j]! + fuzzyNumerator[i]![j]! + remainingWeight;
            const totalDen =
              cheapDenominator[i]![j]! + fuzzyDenominator[i]![j]! + remainingWeight;
            const bestPossible = totalDen > 0 ? totalNum / totalDen : 0;
            if (bestPossible >= threshold) {
              anyCanReach = true;
            }
          }
        }
        if (!anyCanReach) break; // No pair can reach threshold — skip remaining fields
      }
    }

    // Combine cheap + fuzzy
    combined = Array.from({ length: n }, () => new Array<number>(n).fill(0));
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        if (impossible[i]![j]!) {
          combined[i]![j] = 0;
        } else {
          const totalNum = cheapNumerator[i]![j]! + fuzzyNumerator[i]![j]!;
          const totalDen = cheapDenominator[i]![j]! + fuzzyDenominator[i]![j]!;
          combined[i]![j] = totalDen > 0 ? totalNum / totalDen : 0;
        }
        combined[j]![i] = combined[i]![j]!;
      }
    }
  }

  // v1.11: negative evidence on weighted matchkeys. Apply per-pair penalty
  // AFTER the positive-score loop, BEFORE the threshold compare, so that
  // pairs whose adjusted score falls below threshold drop out.
  const ne =
    mk.type === "weighted"
      ? (mk as WeightedMatchkey).negativeEvidence
      : undefined;
  const neActive = ne !== undefined && ne.length > 0;

  // Extract upper triangle pairs above threshold
  const results: ScoredPair[] = [];
  for (let i = 0; i < n; i++) {
    for (let j = i + 1; j < n; j++) {
      let score = combined[i]![j]!;
      if (neActive) {
        const penalty = applyNegativeEvidence(mk, rows[i]!, rows[j]!);
        score = Math.max(0, score - penalty);
      }
      if (score < threshold) continue;
      const idA = Math.min(rowIds[i]!, rowIds[j]!);
      const idB = Math.max(rowIds[i]!, rowIds[j]!);
      const key = pairKey(idA, idB);
      if (excludePairs !== undefined && excludePairs.has(key)) continue;
      results.push(makeScoredPair(idA, idB, score));
    }
  }
  return results;
}

// ---------------------------------------------------------------------------
// Public: scoreBlocksSequential
// ---------------------------------------------------------------------------

export interface ScoreBlocksOptions {
  /** Filter to cross-source pairs only. */
  readonly acrossFilesOnly?: boolean;
  /** Row ID -> source name mapping (for acrossFilesOnly). */
  readonly sourceLookup?: ReadonlyMap<number, string>;
  /** Target IDs for match mode — filter to target/ref cross pairs. */
  readonly targetIds?: ReadonlySet<number>;
}

/**
 * Score all blocks sequentially.
 *
 * In JS there is no GIL, so we use sequential scoring as the default.
 * For web workers or similar concurrency, the caller can partition blocks.
 */
export function scoreBlocksSequential(
  blocks: readonly BlockResult[],
  mk: MatchkeyConfig,
  matchedPairs: Set<PairKey>,
  options?: ScoreBlocksOptions,
): ScoredPair[] {
  if (blocks.length === 0) return [];

  const acrossFilesOnly = options?.acrossFilesOnly ?? false;
  const sourceLookup = options?.sourceLookup;
  const targetIds = options?.targetIds;

  const allPairs: ScoredPair[] = [];

  for (const block of blocks) {
    // For cross-file mode, check that block has records from multiple sources
    if (acrossFilesOnly && sourceLookup !== undefined) {
      const sourcesInBlock = new Set<string>();
      for (const row of block.rows) {
        const src = sourceLookup.get(row["__row_id__"] as number);
        if (src !== undefined) sourcesInBlock.add(src);
      }
      if (sourcesInBlock.size < 2) continue;
    }

    // Use a frozen copy of matchedPairs for consistency
    const excludeSnapshot: ReadonlySet<PairKey> = new Set(matchedPairs);

    let pairs = findFuzzyMatches(
      block.rows,
      mk,
      excludeSnapshot,
      block.preScoredPairs,
    );

    // Cross-file filter
    if (acrossFilesOnly && sourceLookup !== undefined) {
      pairs = pairs.filter((p) => {
        const srcA = sourceLookup.get(p.idA);
        const srcB = sourceLookup.get(p.idB);
        return srcA !== srcB;
      });
    }

    // Target/ref cross filter for match mode
    if (targetIds !== undefined) {
      pairs = pairs.filter(
        (p) => targetIds.has(p.idA) !== targetIds.has(p.idB),
      );
    }

    for (const p of pairs) {
      allPairs.push(p);
      matchedPairs.add(pairKey(p.idA, p.idB));
    }
  }

  return allPairs;
}

// ---------------------------------------------------------------------------
// Utility: canonicalize pair key
// ---------------------------------------------------------------------------

// Re-export pairKey from cluster.ts — single canonical source of truth.
export { pairKey };
