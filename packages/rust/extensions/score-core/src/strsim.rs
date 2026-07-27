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
// Multiword bit-parallel primitives (little-endian u64 limbs)
// ---------------------------------------------------------------------------
//
// The LCS (Allison-Dix), Levenshtein (Myers 1999) and Jaro (rapidfuzz
// FlaggedChars) kernels below all pack the PATTERN's positions into one bit per
// position and advance a DP row/column with a handful of word ops per text
// char. rapidfuzz uses the identical machinery; the INTEGER counts (LCS length,
// edit distance, Jaro match/transposition) are exact, so byte-identical output
// is preserved (proven vs the rapidfuzz crate + a naive-oracle fuzz across the
// 64/128/192-bit limb boundaries in the test module). A pattern longer than 64
// codepoints spans multiple limbs, so every op carries/borrows across limbs.

#[inline]
fn nwords(m: usize) -> usize {
    m.div_ceil(64)
}

/// Mask of the valid bits in the TOP limb of an `m`-bit vector.
#[inline]
fn top_mask(m: usize) -> u64 {
    let r = m % 64;
    if r == 0 {
        u64::MAX
    } else {
        (1u64 << r) - 1
    }
}

/// `out = a + b` (equal length); the carry out of the top limb is discarded
/// (callers mask the top limb to the pattern width where it matters).
fn bv_add(a: &[u64], b: &[u64], out: &mut [u64]) {
    let mut carry: u128 = 0;
    for i in 0..a.len() {
        let s = a[i] as u128 + b[i] as u128 + carry;
        out[i] = s as u64;
        carry = s >> 64;
    }
}

/// `out = a - b`, assuming `a >= b` (the callers only ever subtract a subset),
/// so the final borrow is always zero.
fn bv_sub(a: &[u64], b: &[u64], out: &mut [u64]) {
    let mut borrow: i128 = 0;
    for i in 0..a.len() {
        let d = a[i] as i128 - b[i] as i128 - borrow;
        if d < 0 {
            out[i] = (d + (1i128 << 64)) as u64;
            borrow = 1;
        } else {
            out[i] = d as u64;
            borrow = 0;
        }
    }
}

fn bv_popcount(a: &[u64]) -> usize {
    a.iter().map(|w| w.count_ones() as usize).sum()
}

/// In-place left shift by one across limbs; sets bit 0 if `carry_in`, then masks
/// the top limb to `tmask`.
fn bv_shl1(v: &mut [u64], tmask: u64, carry_in: bool) {
    let mut carry: u64 = u64::from(carry_in);
    for w in v.iter_mut() {
        let next = *w >> 63;
        *w = (*w << 1) | carry;
        carry = next;
    }
    let last = v.len() - 1;
    v[last] &= tmask;
}

fn bv_clear_below(v: &mut [u64], lo: usize) {
    let w = lo / 64;
    for x in v.iter_mut().take(w) {
        *x = 0;
    }
    if w < v.len() {
        let r = lo % 64;
        if r > 0 {
            v[w] &= !((1u64 << r) - 1);
        }
    }
}

fn bv_clear_above(v: &mut [u64], hi: usize) {
    let w = hi / 64;
    let r = hi % 64;
    let keep = if r == 63 { u64::MAX } else { (1u64 << (r + 1)) - 1 };
    if w < v.len() {
        v[w] &= keep;
    }
    for x in v.iter_mut().skip(w + 1) {
        *x = 0;
    }
}

fn bv_lowest_set(v: &[u64]) -> Option<usize> {
    for (i, &w) in v.iter().enumerate() {
        if w != 0 {
            return Some(i * 64 + w.trailing_zeros() as usize);
        }
    }
    None
}

/// `Peq[c]` = bitmask (one bit per position) of where `c` occurs in `pattern`.
fn peq_map<T: Eq + Hash>(pattern: &[T]) -> HashMap<&T, Vec<u64>> {
    let nw = nwords(pattern.len()).max(1);
    let mut m: HashMap<&T, Vec<u64>> = HashMap::new();
    for (i, c) in pattern.iter().enumerate() {
        let e = m.entry(c).or_insert_with(|| vec![0u64; nw]);
        e[i / 64] |= 1u64 << (i % 64);
    }
    m
}

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

    // ASCII single-block fast path (the ~universal case for names). When the
    // PATTERN fits one 64-bit block AND every char in BOTH strings is ASCII, the
    // per-comparison `HashMap<&char, Vec<u64>>` peq + the `vec![0u64; nw]`
    // scratch allocations are pure overhead: profiling (flamegraph) put peq_map's
    // hashing + allocation at ~2x the actual jaro math. Use a stack `[u64; 128]`
    // array peq indexed by byte and single-u64 bitvectors -- zero hashing, zero
    // heap allocation. Byte-identical to the generic path (same algorithm, same
    // bit ops, same order); parity is asserted in the tests below.
    if len1 <= 64
        && s1.iter().all(|c| (*c as u32) < 128)
        && s2.iter().all(|c| (*c as u32) < 128)
    {
        return jaro_similarity_ascii(s1, s2, bound);
    }

    // Bit-parallel FlaggedChars: bind each TEXT (s2) position to the LOWEST
    // unflagged PATTERN (s1) position within [j-bound, j+bound] via a masked
    // pattern-match bitvector + lowest-set-bit (rapidfuzz's `blsi` order),
    // instead of the O(window) inner scan. Same match/transposition counts ->
    // byte-identical similarity.
    let nw = nwords(len1);
    let peq = peq_map(s1);
    let empty = vec![0u64; nw];
    let mut p_flag = vec![0u64; nw]; // matched pattern positions
    let mut t_flag_pos: Vec<usize> = Vec::new(); // matched text positions, ascending
    let mut cand = vec![0u64; nw];
    let hi_max = len1 - 1;
    for (j, &cj) in s2.iter().enumerate() {
        let lo = j.saturating_sub(bound);
        let hi = std::cmp::min(j + bound, hi_max);
        if lo > hi {
            continue;
        }
        let pm = peq.get(&cj).unwrap_or(&empty);
        for i in 0..nw {
            cand[i] = pm[i] & !p_flag[i];
        }
        bv_clear_below(&mut cand, lo);
        bv_clear_above(&mut cand, hi);
        if let Some(bit) = bv_lowest_set(&cand) {
            p_flag[bit / 64] |= 1u64 << (bit % 64);
            t_flag_pos.push(j);
        }
    }

    // common_char_filter: no common chars -> 0.0.
    let common = bv_popcount(&p_flag);
    if common == 0 {
        return 0.0;
    }

    // Transpositions: pair the k-th flagged pattern char with the k-th flagged
    // text char (both ascending); count positional disagreements.
    let mut transposition = 0usize;
    let mut tmp = p_flag.clone();
    let mut k = 0usize;
    while let Some(bit) = bv_lowest_set(&tmp) {
        if s1[bit] != s2[t_flag_pos[k]] {
            transposition += 1;
        }
        tmp[bit / 64] &= !(1u64 << (bit % 64));
        k += 1;
    }

    jaro_calculate_similarity(len1, len2, common, transposition)
}

/// Allocation-free, hash-free Jaro for the ASCII single-block case
/// (`s1.len() <= 64`, every char in both strings `< 128`). Byte-identical to
/// `jaro_similarity`'s generic path -- the ONLY differences are representation:
/// a stack `[u64; 128]` peq indexed by byte instead of `HashMap<&char, Vec<u64>>`,
/// and single-`u64` bitvectors instead of `Vec<u64>` (nw == 1). Callers guarantee
/// the preconditions, so `c as usize` never indexes out of `[0, 128)`.
#[inline]
fn jaro_similarity_ascii(s1: &[char], s2: &[char], bound: usize) -> f64 {
    let len1 = s1.len();
    let len2 = s2.len();

    // Peq[c] = bitmask of positions where byte c occurs in s1 (one 64-bit block).
    let mut peq = [0u64; 128];
    for (i, &c) in s1.iter().enumerate() {
        peq[c as usize] |= 1u64 << i;
    }

    let mut p_flag: u64 = 0; // matched pattern positions
    let mut t_flag_pos = [0u32; 64]; // matched text positions, ascending
    let mut t_cnt = 0usize;
    let hi_max = len1 - 1;
    for (j, &cj) in s2.iter().enumerate() {
        let lo = j.saturating_sub(bound);
        let hi = std::cmp::min(j + bound, hi_max);
        if lo > hi {
            continue;
        }
        let mut cand = peq[cj as usize] & !p_flag;
        if lo > 0 {
            cand &= !((1u64 << lo) - 1); // clear bits below lo
        }
        if hi < 63 {
            cand &= (1u64 << (hi + 1)) - 1; // clear bits above hi
        }
        if cand != 0 {
            let bit = cand.trailing_zeros(); // lowest set bit == rapidfuzz blsi order
            p_flag |= 1u64 << bit;
            t_flag_pos[t_cnt] = j as u32;
            t_cnt += 1;
        }
    }

    let common = p_flag.count_ones() as usize;
    if common == 0 {
        return 0.0;
    }

    // Transpositions: pair the k-th flagged pattern char with the k-th flagged
    // text char (both ascending); count positional disagreements.
    let mut transposition = 0usize;
    let mut tmp = p_flag;
    let mut k = 0usize;
    while tmp != 0 {
        let bit = tmp.trailing_zeros() as usize;
        if s1[bit] != s2[t_flag_pos[k] as usize] {
            transposition += 1;
        }
        tmp &= tmp - 1; // clear lowest set bit
        k += 1;
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

/// Uniform-weight Levenshtein edit distance, bit-parallel (Myers 1999). The
/// PATTERN `a` is packed one bit per position; each text char advances the
/// vertical-delta vectors `pv`/`mv` with a fixed set of word ops. Integer result
/// identical to the naive Wagner-Fischer DP and to rapidfuzz (proven in tests).
pub fn levenshtein_distance<T: Eq + Hash>(a: &[T], b: &[T]) -> usize {
    if a.is_empty() {
        return b.len();
    }
    if b.is_empty() {
        return a.len();
    }
    let m = a.len();
    let nw = nwords(m);
    let tmask = top_mask(m);
    let peq = peq_map(a);
    let empty = vec![0u64; nw];
    let top_word = (m - 1) / 64;
    let top_bit = 1u64 << ((m - 1) % 64);

    let mut pv = vec![u64::MAX; nw];
    pv[nw - 1] &= tmask;
    let mut mv = vec![0u64; nw];
    let mut score = m;

    let mut xv = vec![0u64; nw];
    let mut xh = vec![0u64; nw];
    let mut ph = vec![0u64; nw];
    let mut mh = vec![0u64; nw];
    let mut t = vec![0u64; nw];
    let mut t2 = vec![0u64; nw];

    for cb in b {
        let eq = peq.get(cb).unwrap_or(&empty);
        for i in 0..nw {
            xv[i] = eq[i] | mv[i];
            t[i] = eq[i] & pv[i];
        }
        // xh = (((eq & pv) + pv) ^ pv) | eq
        bv_add(&t, &pv, &mut t2);
        for i in 0..nw {
            let m_all = if i == nw - 1 { tmask } else { u64::MAX };
            xh[i] = ((t2[i] ^ pv[i]) | eq[i]) & m_all;
            ph[i] = (mv[i] | !(xh[i] | pv[i])) & m_all;
            mh[i] = pv[i] & xh[i];
        }
        if ph[top_word] & top_bit != 0 {
            score += 1;
        } else if mh[top_word] & top_bit != 0 {
            score -= 1;
        }
        bv_shl1(&mut ph, tmask, true);
        bv_shl1(&mut mh, tmask, false);
        for i in 0..nw {
            let m_all = if i == nw - 1 { tmask } else { u64::MAX };
            pv[i] = (mh[i] | !(xv[i] | ph[i])) & m_all;
            mv[i] = ph[i] & xv[i];
        }
    }
    score
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

/// Longest common subsequence length, bit-parallel (Allison-Dix). The PATTERN
/// `a` is packed one bit per position; each text char advances the LCS row with
/// `V = (V + (V & Peq[c])) | (V - (V & Peq[c]))`, and the LCS length is the count
/// of ZERO bits left in `V`. Integer result identical to the naive DP and to
/// rapidfuzz's LCS kernel (proven in tests, incl. the multiword boundaries).
fn lcs_length<T: Eq + Hash>(a: &[T], b: &[T]) -> usize {
    if a.is_empty() || b.is_empty() {
        return 0;
    }
    let m = a.len();
    let nw = nwords(m);
    let tmask = top_mask(m);
    let peq = peq_map(a);
    let empty = vec![0u64; nw];

    let mut v = vec![u64::MAX; nw];
    v[nw - 1] &= tmask;
    let mut u = vec![0u64; nw];
    let mut add = vec![0u64; nw];
    let mut sub = vec![0u64; nw];
    for cb in b {
        let p = peq.get(cb).unwrap_or(&empty);
        for i in 0..nw {
            u[i] = v[i] & p[i];
        }
        bv_add(&v, &u, &mut add);
        add[nw - 1] &= tmask;
        bv_sub(&v, &u, &mut sub);
        for i in 0..nw {
            v[i] = add[i] | sub[i];
        }
    }
    v[nw - 1] &= tmask;
    m - bv_popcount(&v)
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
        // 20k pairs in the MULTIWORD regime (patterns > 64 codepoints), so the
        // bit-parallel kernels' carry/borrow across u64 limbs is proven vs
        // rapidfuzz, not just the single-limb path.
        for _ in 0..20_000 {
            let ml = [40usize, 64, 65, 100, 128, 130, 200][(next(&mut rng) as usize) % 7];
            let a = gen(&mut rng, ml);
            let b = gen(&mut rng, ml);
            check_pair(&a, &b);
        }
    }

    #[test]
    fn ascii_fast_path_parity_vs_rapidfuzz() {
        // Hammer the ASCII single-block fast path (all chars < 128, len1 <= 64)
        // directly vs rapidfuzz. The generic HashMap path is proven vs rapidfuzz
        // above, so fast==rapidfuzz + generic==rapidfuzz => fast==generic. An
        // ASCII-only alphabet (no 'é') guarantees every pair takes the fast path.
        const ASCII: &[char] = &['a', 'b', 'c', 'd', 'e', 'f', '1', '2', ' ', '\'', '-'];
        let mut rng: u64 = 0xDEAD_BEEF_0BAD_F00D;
        let geni = |rng: &mut u64, max: usize| -> String {
            let len = (next(rng) as usize) % (max + 1);
            (0..len).map(|_| ASCII[(next(rng) as usize) % ASCII.len()]).collect()
        };
        for _ in 0..60_000 {
            let ml = [1usize, 2, 3, 6, 12, 24, 40, 64][(next(&mut rng) as usize) % 8];
            let a = geni(&mut rng, ml);
            let b = geni(&mut rng, ml);
            check_pair(&a, &b);
        }
    }

    #[test]
    fn parity_vs_rapidfuzz_at_limb_boundaries() {
        // Exact pattern lengths straddling the 64-bit limb edges, where the
        // multiword shift/add/borrow is most likely to be off by a bit.
        for &n in &[63usize, 64, 65, 127, 128, 129, 191, 192, 193, 256] {
            let a: String = (0..n).map(|i| ALPHABET[i % ALPHABET.len()]).collect();
            let mut cb: Vec<char> = a.chars().collect();
            // Perturb a few interior/edge positions to exercise mismatches.
            for &p in &[0usize, 1, n / 2, n - 1] {
                cb[p] = ALPHABET[(p + 3) % ALPHABET.len()];
            }
            let b: String = cb.into_iter().collect();
            check_pair(&a, &b);
            check_pair(&b, &a); // asymmetric pattern/text roles
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
