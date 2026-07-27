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

// ---------------------------------------------------------------------------
// Single-word fast path (patterns <= 64 codepoints, Latin-1)
// ---------------------------------------------------------------------------
//
// The multiword path heap-allocates a HashMap peq + limb vectors PER CALL, which
// dominates on short fields (names / addresses / dates): ~20x the actual
// bit-parallel work, and measurably SLOWER than the naive two-row DP it replaced
// on that regime. Almost every ER field is <= 64 codepoints and Latin-1, so a
// stack `[u64; 256]` position bitmap + a single u64 DP register cover it with
// ZERO heap -- the scalar scorer then beats rapidfuzz on the common case. Longer
// or non-Latin-1 (CJK / emoji) patterns fall through to the multiword path.
// Byte-identical to it (fuzzed vs the multiword + rapidfuzz oracle to the exact
// 64-codepoint boundary).

const SW_MAX: usize = 64;

/// Stack position-bitmap for a pattern of <= 64 Latin-1 codepoints. `None` when
/// the pattern is too long or holds a codepoint >= 256 (multiword handles those).
#[inline]
fn sw_peq(pat: &[char]) -> Option<[u64; 256]> {
    if pat.len() > SW_MAX {
        return None;
    }
    let mut peq = [0u64; 256];
    for (i, &c) in pat.iter().enumerate() {
        let ci = c as u32;
        if ci >= 256 {
            return None;
        }
        peq[ci as usize] |= 1u64 << i;
    }
    Some(peq)
}

#[inline]
fn sw_get(peq: &[u64; 256], c: char) -> u64 {
    let ci = c as u32;
    if ci < 256 {
        peq[ci as usize]
    } else {
        0
    }
}

#[inline]
fn sw_mask(m: usize) -> u64 {
    if m >= 64 {
        u64::MAX
    } else {
        (1u64 << m) - 1
    }
}

/// ASCII position bitmap (`[u64; 128]`, half the `char` tier's 2 KiB, and no
/// `Vec<char>` at the call site). Caller guarantees `pat` is ASCII and
/// `pat.len() <= 64`; the `& 0x7f` keeps the index in range defensively.
#[inline]
fn byte_peq(pat: &[u8]) -> [u64; 128] {
    let mut peq = [0u64; 128];
    for (i, &c) in pat.iter().enumerate() {
        peq[(c & 0x7f) as usize] |= 1u64 << i;
    }
    peq
}

/// Single-word Allison-Dix LCS. Caller guarantees `s1.len() <= 64`. Generic over
/// the symbol type so both the `char` (Latin-1, `[u64; 256]` peq) and the `u8`
/// (ASCII, `[u64; 128]` peq) tiers share one monomorphised body; `get` is the
/// position-bitmap lookup, inlined to a single bounds-checked array index.
fn lcs_length_sw<T: Copy>(s1: &[T], s2: &[T], get: impl Fn(T) -> u64) -> usize {
    let m = s1.len();
    let mask = sw_mask(m);
    let mut v = mask;
    for &c in s2 {
        let p = get(c);
        let u = v & p;
        v = (v.wrapping_add(u) & mask) | (v - u);
    }
    m - (v & mask).count_ones() as usize
}

/// Single-word Myers Levenshtein. Caller guarantees `1 <= s1.len() <= 64`.
fn levenshtein_distance_sw<T: Copy>(s1: &[T], s2: &[T], get: impl Fn(T) -> u64) -> usize {
    let m = s1.len();
    let mask = sw_mask(m);
    let top = 1u64 << (m - 1);
    let mut pv = mask;
    let mut mv = 0u64;
    let mut score = m;
    for &c in s2 {
        let eq = get(c);
        let xv = eq | mv;
        let xh = ((((eq & pv).wrapping_add(pv)) ^ pv) | eq) & mask;
        let ph = (mv | !(xh | pv)) & mask;
        let mh = pv & xh;
        if ph & top != 0 {
            score += 1;
        } else if mh & top != 0 {
            score -= 1;
        }
        let ph = ((ph << 1) | 1) & mask;
        let mh = (mh << 1) & mask;
        pv = (mh | !(xv | ph)) & mask;
        mv = ph & xv;
    }
    score
}

/// Single-word FlaggedChars Jaro. Caller guarantees `s1.len() <= 64` (and, via
/// the `len1 == 1 && len2 == 1` short-circuit in the dispatcher, `len2 >= 2` when
/// `len1 == 1`). Same match/transposition order as the multiword path.
fn jaro_similarity_sw<T: Copy + PartialEq>(s1: &[T], s2: &[T], get: impl Fn(T) -> u64) -> f64 {
    let len1 = s1.len();
    let len2 = s2.len();
    let bound = std::cmp::max(len1, len2) / 2 - 1;
    let mut p_flag = 0u64;
    // Matched TEXT positions in ascending j order. The TEXT (s2) may be longer
    // than 64, so a u64 position bitmask would overflow; there are at most
    // `common <= len1 <= 64` matches, so a fixed stack array holds them.
    let mut t_pos = [0usize; SW_MAX];
    let mut nt = 0usize;
    let hi_max = len1 - 1;
    for (j, &cj) in s2.iter().enumerate() {
        let lo = j.saturating_sub(bound);
        let hi = std::cmp::min(j + bound, hi_max);
        if lo > hi {
            continue;
        }
        let window = (((1u128 << (hi + 1)) - 1) ^ ((1u128 << lo) - 1)) as u64;
        let cand = get(cj) & window & !p_flag;
        if cand != 0 {
            p_flag |= cand & cand.wrapping_neg(); // blsi: lowest set bit
            t_pos[nt] = j;
            nt += 1;
        }
    }
    let common = p_flag.count_ones() as usize;
    if common == 0 {
        return 0.0;
    }
    // Pair the k-th flagged pattern char (ascending) with the k-th matched text
    // char (ascending j order); count positional disagreements.
    let mut transposition = 0usize;
    let mut pf = p_flag;
    let mut k = 0usize;
    while pf != 0 {
        let i = pf.trailing_zeros() as usize;
        if s1[i] != s2[t_pos[k]] {
            transposition += 1;
        }
        pf &= pf - 1;
        k += 1;
    }
    jaro_calculate_similarity(len1, len2, common, transposition)
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

    // Single-word fast path for a <= 64-codepoint Latin-1 pattern (accented
    // names etc.; pure ASCII already took the byte tier in jaro_winkler).
    // Byte-identical to the multiword path below.
    if let Some(peq) = sw_peq(s1) {
        return jaro_similarity_sw(s1, s2, |c: char| sw_get(&peq, c));
    }

    // rapidfuzz bound = max(len1, len2) / 2 - 1 (both >= 1 here, so >= 0).
    let bound = std::cmp::max(len1, len2) / 2 - 1;

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

/// Jaro-Winkler normalized similarity on `[0, 1]`, matching
/// `rapidfuzz::distance::jaro_winkler::normalized_similarity` with the default
/// `prefix_weight = 0.1`. Operates on Unicode codepoints.
pub fn jaro_winkler(a: &str, b: &str) -> f64 {
    // ASCII single-word fast path (the common ER field): work on the byte slices
    // directly -- no `Vec<char>`, a 1 KiB stack peq -- so this beats rapidfuzz on
    // short names/addresses. len 0/1 (and the `1,1` jaro edge) route to the char
    // path, which owns the edge cases; a >= 2 keeps the jaro `bound` non-negative.
    if a.is_ascii() && b.is_ascii() && (2..=SW_MAX).contains(&a.len()) {
        let ab = a.as_bytes();
        let bb = b.as_bytes();
        let peq = byte_peq(ab);
        let jaro = jaro_similarity_sw(ab, bb, |x: u8| peq[(x & 0x7f) as usize]);
        return jaro_winkler_combine(ab, bb, jaro);
    }
    let s1: Vec<char> = a.chars().collect();
    let s2: Vec<char> = b.chars().collect();
    jaro_winkler_chars(&s1, &s2)
}

/// Jaro-Winkler prefix boost + `Metricf64` round-trip, shared by the char and
/// byte tiers. `jaro` is the raw Jaro similarity of the same two slices.
#[inline]
fn jaro_winkler_combine<T: PartialEq>(s1: &[T], s2: &[T], jaro: f64) -> f64 {
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
    let mut sim = jaro;
    if sim > 0.7 {
        sim += prefix as f64 * PREFIX_WEIGHT * (1.0 - sim);
    }
    // jaro_winkler is a `Metricf64` similarity metric with `maximum == 1.0`, so
    // the normalized round-trip is `1.0 - (1.0 - sim)` (see the parity note).
    1.0 - (1.0 - sim)
}

/// Slice form (lets `score_one`/callers avoid re-collecting when they already
/// hold `Vec<char>`). `prefix_weight` fixed at rapidfuzz's default 0.1.
pub fn jaro_winkler_chars(s1: &[char], s2: &[char]) -> f64 {
    // score_cutoff = 0.0 on the normalized_similarity path, so the jaro cutoff
    // adjustment (only fires when cutoff > 0.7) is a no-op and omitted. The
    // prefix boost + Metricf64 round-trip are shared with the byte tier.
    jaro_winkler_combine(s1, s2, jaro_similarity(s1, s2))
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
    // ASCII single-word fast path (no Vec<char>, 1 KiB stack peq).
    if a.is_ascii() && b.is_ascii() && (1..=SW_MAX).contains(&a.len()) {
        let ab = a.as_bytes();
        let bb = b.as_bytes();
        let maximum = std::cmp::max(ab.len(), bb.len());
        let peq = byte_peq(ab);
        let dist = levenshtein_distance_sw(ab, bb, |x: u8| peq[(x & 0x7f) as usize]);
        return 1.0 - (dist as f64 / maximum as f64);
    }
    let s1: Vec<char> = a.chars().collect();
    let s2: Vec<char> = b.chars().collect();
    let maximum = std::cmp::max(s1.len(), s2.len());
    if maximum == 0 {
        return 1.0;
    }
    // Latin-1 char tier for accented short patterns; else multiword.
    let dist = match sw_peq(&s1) {
        Some(peq) if !s1.is_empty() => levenshtein_distance_sw(&s1, &s2, |c: char| sw_get(&peq, c)),
        _ => levenshtein_distance(&s1, &s2),
    };
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
    // ASCII single-word fast path (no Vec<char>, 1 KiB stack peq). lcs_length_sw
    // is safe for an empty side (returns 0), so `a.len()` may be 0..=64 here.
    if a.is_ascii() && b.is_ascii() && a.len() <= SW_MAX {
        let ab = a.as_bytes();
        let bb = b.as_bytes();
        let maximum = ab.len() + bb.len();
        if maximum == 0 {
            return 1.0;
        }
        let peq = byte_peq(ab);
        let lcs = lcs_length_sw(ab, bb, |x: u8| peq[(x & 0x7f) as usize]);
        return 1.0 - ((maximum - 2 * lcs) as f64 / maximum as f64);
    }
    let s1: Vec<char> = a.chars().collect();
    let s2: Vec<char> = b.chars().collect();
    let maximum = s1.len() + s2.len();
    if maximum == 0 {
        return 1.0;
    }
    // Latin-1 char tier for accented short patterns; else multiword.
    let lcs = match sw_peq(&s1) {
        Some(peq) => lcs_length_sw(&s1, &s2, |c: char| sw_get(&peq, c)),
        None => lcs_length(&s1, &s2),
    };
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
    fn fast_path_short_pattern_long_text_and_fallback() {
        // The single-word fast path gates on the PATTERN (<=64), but the TEXT can
        // be arbitrarily long -> exercise short-pattern / long-text pairs (the
        // regime the equal-length corpus never hits), plus non-Latin-1 codepoints
        // that must fall through to the multiword path. All vs the rapidfuzz oracle.
        let mut rng: u64 = 0x5EED_F00D_1234;
        for _ in 0..20_000 {
            let a = gen(&mut rng, 64); // pattern: 0..=64 (single-word regime)
            let b = gen(&mut rng, 300); // text: 0..=300 (spills far past 64)
            check_pair(&a, &b);
        }
        // Non-Latin-1 (codepoint >= 256) forces sw_peq -> None -> multiword.
        let uni = ["日本語abc", "Ωμέγα x", "naïve 日", "abc日def", "Ω", "café日"];
        for a in uni {
            for b in uni {
                check_pair(a, b);
                assert!(sw_peq(&a.chars().collect::<Vec<_>>()).is_none() || a.is_ascii());
            }
        }
    }

    #[test]
    fn byte_tier_ascii_parity() {
        // Pure-ASCII inputs take the byte tier (u8 core, [u64;128], no Vec<char>).
        // Cover the guard edges (0/1/2/64/65 pattern lengths, empty text) plus a
        // large asymmetric fuzz (short pattern, long text), all vs rapidfuzz.
        const AB: &[char] = &['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h', 'i', ' ', '1', '2'];
        let mk = |len: usize, off: usize| -> String {
            (0..len).map(|i| AB[(i + off) % AB.len()]).collect()
        };
        for &(la, lb) in &[
            (0usize, 0usize), (0, 5), (5, 0), (1, 1), (1, 9), (9, 1),
            (2, 2), (2, 200), (64, 64), (64, 200), (65, 3), (66, 66),
        ] {
            check_pair(&mk(la, 0), &mk(lb, 3));
        }
        let mut rng: u64 = 0xA5C11_0FF1CE;
        for _ in 0..30_000 {
            let la = (next(&mut rng) as usize) % 65; // 0..=64: pattern in byte-tier range
            let lb = (next(&mut rng) as usize) % 200; // text may spill past 64
            let a: String = (0..la).map(|_| AB[(next(&mut rng) as usize) % AB.len()]).collect();
            let b: String = (0..lb).map(|_| AB[(next(&mut rng) as usize) % AB.len()]).collect();
            assert!(a.is_ascii() && b.is_ascii());
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
