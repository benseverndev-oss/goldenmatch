//! Vendored string-similarity primitives — GoldenMatch's OWN implementations of
//! the four metrics the scorer surface needs, replacing the `rapidfuzz` crate on
//! the shipped path so the "one authoritative semantic owner per capability"
//! invariant holds for the scoring math itself (not a black box).
//!
//! Slice 1 of the rapidfuzz-ownership epic. These are faithful, byte-identical
//! ports of rapidfuzz-rs 0.5.0's scalar `normalized_similarity` / `distance`:
//!
//!   * [`jaro_winkler`]  == `jaro_winkler::normalized_similarity` (prefix_weight 0.1)
//!   * [`levenshtein_normalized_similarity`] == `levenshtein::normalized_similarity`
//!   * [`indel_ratio`]   == `fuzz::ratio` (Indel normalized_similarity, [0,1])
//!   * [`damerau_levenshtein_distance`] == `damerau_levenshtein::distance` (true DL)
//!
//! rapidfuzz's bit-parallel machinery (Myers/Hyyrö, SIMD batch) is an
//! *optimization*: the integer distances / LCS / Jaro match+transposition counts
//! it produces are exact and identical to a correct naive computation, so
//! byte-identical output only requires (a) the same final float arithmetic and
//! (b) rapidfuzz's Jaro match order (iterate the text string, greedily bind the
//! lowest unflagged pattern position in the window). Both are replicated below.
//!
//! Byte-identical output is PROVEN, not assumed: the `parity` test module (built
//! only with the `rapidfuzz` dev-dependency) asserts `f64::to_bits` equality
//! against the crate over a large randomized + adversarial corpus.

use std::collections::HashMap;
use std::hash::Hash;

// ---------------------------------------------------------------------------
// Jaro / Jaro-Winkler
// ---------------------------------------------------------------------------

/// rapidfuzz `jaro::calculate_similarity`: `transposition/=2` then
/// `(m/p_len + m/t_len + (m-t)/m) / 3` — exact operation order preserved.
#[inline]
fn jaro_calculate_similarity(p_len: usize, t_len: usize, common: usize, mut transposition: usize) -> f64 {
    transposition /= 2;
    let mut sim: f64 = 0.0;
    sim += common as f64 / p_len as f64;
    sim += common as f64 / t_len as f64;
    sim += (common as f64 - transposition as f64) / common as f64;
    sim / 3.0
}

/// Jaro similarity on `[0, 1]`, matching `rapidfuzz::distance::jaro` at
/// `score_cutoff = 0.0`. `s1`/`s2` are codepoint slices.
fn jaro_similarity(s1: &[char], s2: &[char]) -> f64 {
    let len1 = s1.len();
    let len2 = s2.len();

    if len1 == 0 && len2 == 0 {
        return 1.0;
    }
    // length_filter: either side empty -> 0.0 (never passes at any cutoff).
    if len1 == 0 || len2 == 0 {
        return 0.0;
    }
    if len1 == 1 && len2 == 1 {
        return if s1[0] == s2[0] { 1.0 } else { 0.0 };
    }

    // rapidfuzz bound = max(len1, len2) / 2 - 1 (both >= 1 here, so >= 0).
    let bound = std::cmp::max(len1, len2) / 2 - 1;

    let mut s1_flag = vec![false; len1];
    let mut s2_flag = vec![false; len2];
    let mut common = 0usize;

    // Iterate the TEXT (s2) positions in order; bind each to the LOWEST
    // unflagged PATTERN (s1) position within the window [j-bound, j+bound]
    // (rapidfuzz's `blsi` lowest-set-bit over the bounded pattern-match mask).
    for j in 0..len2 {
        let lo = j.saturating_sub(bound);
        let hi = std::cmp::min(j + bound, len1 - 1);
        let mut i = lo;
        while i <= hi {
            if !s1_flag[i] && s1[i] == s2[j] {
                s1_flag[i] = true;
                s2_flag[j] = true;
                common += 1;
                break;
            }
            i += 1;
        }
    }

    // common_char_filter: no common chars -> 0.0.
    if common == 0 {
        return 0.0;
    }

    // Transpositions: pair the k-th flagged pattern char with the k-th flagged
    // text char; count positional disagreements.
    let mut transposition = 0usize;
    let mut k = 0usize;
    for (i, &f1) in s1_flag.iter().enumerate() {
        if f1 {
            while !s2_flag[k] {
                k += 1;
            }
            if s1[i] != s2[k] {
                transposition += 1;
            }
            k += 1;
        }
    }

    jaro_calculate_similarity(len1, len2, common, transposition)
}

/// Jaro-Winkler normalized similarity on `[0, 1]`, matching
/// `rapidfuzz::distance::jaro_winkler::normalized_similarity` with the default
/// `prefix_weight = 0.1`. Operates on Unicode codepoints.
pub fn jaro_winkler(a: &str, b: &str) -> f64 {
    let s1: Vec<char> = a.chars().collect();
    let s2: Vec<char> = b.chars().collect();
    jaro_winkler_chars(&s1, &s2)
}

/// Slice form (lets `score_one`/callers avoid re-collecting when they already
/// hold `Vec<char>`). `prefix_weight` fixed at rapidfuzz's default 0.1.
pub fn jaro_winkler_chars(s1: &[char], s2: &[char]) -> f64 {
    const PREFIX_WEIGHT: f64 = 0.1;
    // Common prefix, capped at 4 (rapidfuzz `.take(4).take_while(eq)`).
    let mut prefix = 0usize;
    for (c1, c2) in s1.iter().zip(s2.iter()).take(4) {
        if c1 == c2 {
            prefix += 1;
        } else {
            break;
        }
    }
    // score_cutoff = 0.0 on the normalized_similarity path, so the jaro cutoff
    // adjustment (only fires when cutoff > 0.7) is a no-op and omitted.
    let mut sim = jaro_similarity(s1, s2);
    if sim > 0.7 {
        sim += prefix as f64 * PREFIX_WEIGHT * (1.0 - sim);
    }
    // jaro_winkler is a `Metricf64` SIMILARITY metric with `maximum == 1.0`, so
    // rapidfuzz's `normalized_similarity` returns it as `1.0 - normalized_distance`
    // = `1.0 - (1.0 - sim)` (division by the 1.0 maximum is exact). That double
    // round-trip shifts the last ULP vs the raw `sim`; replicating it is required
    // for byte-identical output (proven by the parity test). The integer metrics
    // (levenshtein/indel) have no such round-trip — their `1.0 - dist/maximum` is
    // already the final form.
    1.0 - (1.0 - sim)
}

// ---------------------------------------------------------------------------
// Levenshtein
// ---------------------------------------------------------------------------

/// Uniform-weight Levenshtein edit distance (Wagner-Fischer, two-row). The
/// integer result is identical to rapidfuzz's bit-parallel kernel.
pub fn levenshtein_distance<T: PartialEq>(a: &[T], b: &[T]) -> usize {
    if a.is_empty() {
        return b.len();
    }
    if b.is_empty() {
        return a.len();
    }
    let mut prev: Vec<usize> = (0..=b.len()).collect();
    let mut cur = vec![0usize; b.len() + 1];
    for (i, ca) in a.iter().enumerate() {
        cur[0] = i + 1;
        for (j, cb) in b.iter().enumerate() {
            let cost = usize::from(ca != cb);
            cur[j + 1] = (prev[j] + cost)
                .min(prev[j + 1] + 1)
                .min(cur[j] + 1);
        }
        std::mem::swap(&mut prev, &mut cur);
    }
    prev[b.len()]
}

/// `rapidfuzz::distance::levenshtein::normalized_similarity`:
/// `1.0 - dist / max(len1, len2)` (`maximum == 0 -> 0` distance -> 1.0 sim).
pub fn levenshtein_normalized_similarity(a: &str, b: &str) -> f64 {
    let s1: Vec<char> = a.chars().collect();
    let s2: Vec<char> = b.chars().collect();
    let maximum = std::cmp::max(s1.len(), s2.len());
    if maximum == 0 {
        return 1.0;
    }
    let dist = levenshtein_distance(&s1, &s2);
    1.0 - (dist as f64 / maximum as f64)
}

// ---------------------------------------------------------------------------
// Indel / fuzz::ratio  (LCS-based, substitution-free edit distance)
// ---------------------------------------------------------------------------

/// Longest common subsequence length (Hunt-Szymanski-free DP, two-row). Integer
/// result identical to rapidfuzz's LCS kernel.
fn lcs_length<T: PartialEq>(a: &[T], b: &[T]) -> usize {
    if a.is_empty() || b.is_empty() {
        return 0;
    }
    let mut prev = vec![0usize; b.len() + 1];
    let mut cur = vec![0usize; b.len() + 1];
    for ca in a {
        for (j, cb) in b.iter().enumerate() {
            cur[j + 1] = if ca == cb {
                prev[j] + 1
            } else {
                cur[j].max(prev[j + 1])
            };
        }
        std::mem::swap(&mut prev, &mut cur);
    }
    prev[b.len()]
}

/// `rapidfuzz::fuzz::ratio` == Indel `normalized_similarity` on `[0, 1]`:
/// indel distance `= len1 + len2 - 2*lcs`, maximum `= len1 + len2`,
/// `sim = 1.0 - dist / maximum` (`maximum == 0 -> 1.0`).
pub fn indel_ratio(a: &str, b: &str) -> f64 {
    let s1: Vec<char> = a.chars().collect();
    let s2: Vec<char> = b.chars().collect();
    let maximum = s1.len() + s2.len();
    if maximum == 0 {
        return 1.0;
    }
    let lcs = lcs_length(&s1, &s2);
    let dist = maximum - 2 * lcs;
    1.0 - (dist as f64 / maximum as f64)
}

// ---------------------------------------------------------------------------
// Damerau-Levenshtein (true / unrestricted, Lowrance-Wagner)
// ---------------------------------------------------------------------------

/// True Damerau-Levenshtein edit distance (adjacent transposition = 1 edit,
/// unrestricted), matching `rapidfuzz::distance::damerau_levenshtein::distance`.
/// Integer result; used by the date comparator over 8 packed digits.
pub fn damerau_levenshtein_distance<T: Eq + Hash + Copy>(a: &[T], b: &[T]) -> usize {
    let la = a.len();
    let lb = b.len();
    if la == 0 {
        return lb;
    }
    if lb == 0 {
        return la;
    }
    let inf = la + lb;
    // (la+2) x (lb+2) matrix, offset by 1 so row/col 0 hold the INF sentinels.
    let w = lb + 2;
    let mut d = vec![0usize; (la + 2) * w];
    let at = |i: usize, j: usize| i * w + j;
    d[at(0, 0)] = inf;
    for i in 0..=la {
        d[at(i + 1, 0)] = inf;
        d[at(i + 1, 1)] = i;
    }
    for j in 0..=lb {
        d[at(0, j + 1)] = inf;
        d[at(1, j + 1)] = j;
    }
    let mut last: HashMap<T, usize> = HashMap::new();
    for i in 1..=la {
        let mut db = 0usize; // last column in row i where a[i-1] matched b
        for j in 1..=lb {
            let k = last.get(&b[j - 1]).copied().unwrap_or(0); // last row with b[j-1]
            let l = db;
            let cost = if a[i - 1] == b[j - 1] {
                db = j;
                0
            } else {
                1
            };
            let sub = d[at(i, j)] + cost;
            let ins = d[at(i + 1, j)] + 1;
            let del = d[at(i, j + 1)] + 1;
            let trans = d[at(k, l)] + (i - k - 1) + 1 + (j - l - 1);
            d[at(i + 1, j + 1)] = sub.min(ins).min(del).min(trans);
        }
        last.insert(a[i - 1], i);
    }
    d[at(la + 1, lb + 1)]
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn basic_identities() {
        assert_eq!(jaro_winkler("", ""), 1.0);
        assert_eq!(jaro_winkler("abc", ""), 0.0);
        assert_eq!(jaro_winkler("abc", "abc"), 1.0);
        assert_eq!(levenshtein_normalized_similarity("", ""), 1.0);
        assert_eq!(levenshtein_normalized_similarity("abc", "abc"), 1.0);
        assert_eq!(indel_ratio("", ""), 1.0);
        assert_eq!(indel_ratio("abc", "abc"), 1.0);
        assert_eq!(damerau_levenshtein_distance(b"abcd", b"acbd"), 1); // one transposition
        assert_eq!(damerau_levenshtein_distance(b"ca", b"abc"), 2);
    }

    // ---- Byte-identical parity vs the rapidfuzz crate (the proof gate) ----
    // rapidfuzz is a dev-dependency ONLY; the shipped path uses the vendored
    // fns above. This module proves they are bit-for-bit identical so the swap
    // is a pure sovereignty change with zero output drift.
    use rapidfuzz::distance::{damerau_levenshtein as rf_dl, levenshtein as rf_lev};
    use rapidfuzz::{distance::jaro_winkler as rf_jw, fuzz as rf_fuzz};

    // Deterministic SplitMix64 corpus generator (no rand dep, stable).
    fn next(state: &mut u64) -> u64 {
        *state = state.wrapping_add(0x9E37_79B9_7F4A_7C15);
        let mut z = *state;
        z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
        z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
        z ^ (z >> 31)
    }

    // Small alphabet forces frequent matches/transpositions/repeats — the exact
    // regime where Jaro transposition order and DL transposition edges bite.
    const ALPHABET: &[char] = &['a', 'b', 'c', 'd', 'e', 'é', '1', '2', ' '];

    fn gen(rng: &mut u64, max_len: usize) -> String {
        let len = (next(rng) as usize) % (max_len + 1);
        (0..len)
            .map(|_| ALPHABET[(next(rng) as usize) % ALPHABET.len()])
            .collect()
    }

    fn assert_bits(name: &str, a: &str, b: &str, mine: f64, theirs: f64) {
        assert_eq!(
            mine.to_bits(),
            theirs.to_bits(),
            "{name} drift on ({a:?},{b:?}): mine={mine} theirs={theirs}"
        );
    }

    #[test]
    fn parity_vs_rapidfuzz_crate() {
        let mut rng: u64 = 0xF00D_CAFE_1234_5678;
        // Directed edge cases first.
        let edge = [
            ("", ""), ("a", ""), ("", "a"), ("a", "a"), ("a", "b"),
            ("ab", "ba"), ("abc", "acb"), ("martha", "marhta"),
            ("dwayne", "duane"), ("dixon", "dicksonx"), ("aabbcc", "abcabc"),
            ("2026-07-26", "2026-07-25"), ("caaba", "aabac"),
        ];
        for (a, b) in edge {
            check_pair(a, b);
        }
        // 40k randomized pairs across length regimes (0..1..2..long).
        for _ in 0..40_000 {
            let ml = [1usize, 2, 3, 6, 12, 24][(next(&mut rng) as usize) % 6];
            let a = gen(&mut rng, ml);
            let b = gen(&mut rng, ml);
            check_pair(&a, &b);
        }
    }

    fn check_pair(a: &str, b: &str) {
        // Jaro-Winkler
        let mine = jaro_winkler(a, b);
        let theirs = rf_jw::normalized_similarity(a.chars(), b.chars());
        assert_bits("jaro_winkler", a, b, mine, theirs);

        // Levenshtein normalized similarity
        let mine = levenshtein_normalized_similarity(a, b);
        let theirs = rf_lev::normalized_similarity(a.chars(), b.chars());
        assert_bits("levenshtein", a, b, mine, theirs);

        // fuzz::ratio (Indel normalized similarity)
        let mine = indel_ratio(a, b);
        let theirs = rf_fuzz::ratio(a.chars(), b.chars());
        assert_bits("indel_ratio", a, b, mine, theirs);

        // Damerau-Levenshtein distance over the codepoints
        let ca: Vec<char> = a.chars().collect();
        let cb: Vec<char> = b.chars().collect();
        let mine = damerau_levenshtein_distance(&ca, &cb);
        let theirs = rf_dl::distance(a.chars(), b.chars());
        assert_eq!(mine, theirs, "damerau_levenshtein drift on ({a:?},{b:?})");
    }
}
