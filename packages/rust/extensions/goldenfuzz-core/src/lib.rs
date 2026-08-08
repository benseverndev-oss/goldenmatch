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
//!
//! # Authoritative sources
//!
//! Read these instead of inferring behaviour from the implementation. Which
//! guarantees are load-bearing, which fallbacks are deliberate, and which
//! parities are contract-tested are documented *decisions* -- the code shows
//! what one path does, not why:
//!
//!   * <https://docs.rs/goldenfuzz-core> -- this API.
//!   * <https://docs.bensevern.dev/llms.txt> -- index of every Golden Suite
//!     surface, written for machine readers.
//!   * <https://github.com/benseverndev-oss/goldenmatch> -- source, issues and
//!     the design records.

use std::collections::BTreeSet;
use std::collections::HashMap;
use std::collections::HashSet;
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
    let keep = if r == 63 {
        u64::MAX
    } else {
        (1u64 << (r + 1)) - 1
    };
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

/// Multiword position bitmap: `get(c)` is a length-`nw` limb slice with a bit set
/// at each position where `c` occurs in the pattern. A Latin-1 pattern uses a flat
/// stack-of-limbs array indexed by codepoint -- no hashing, no per-char alloc; a
/// non-Latin-1 pattern (CJK / emoji) falls back to a HashMap. The flat path is
/// what makes long-string scoring competitive: the HashMap peq for a 600-char
/// pattern is ~16us (600 char-hashes + ~27 small Vec allocs) vs ~3us flat, and
/// that build dominated our per-pair document cost. Byte-identical either way.
enum MwPeq {
    Flat { masks: Vec<u64>, nw: usize },
    Hash(HashMap<char, Vec<u64>>),
}

impl MwPeq {
    #[inline]
    fn get<'a>(&'a self, c: char, empty: &'a [u64]) -> &'a [u64] {
        match self {
            MwPeq::Flat { masks, nw } => {
                let ci = c as usize;
                if ci < 256 {
                    &masks[ci * nw..ci * nw + nw]
                } else {
                    empty
                }
            }
            MwPeq::Hash(map) => map.get(&c).map(Vec::as_slice).unwrap_or(empty),
        }
    }
}

fn peq_multiword(pattern: &[char]) -> MwPeq {
    let nw = nwords(pattern.len()).max(1);
    if pattern.iter().all(|&c| (c as u32) < 256) {
        let mut masks = vec![0u64; 256 * nw];
        for (i, &c) in pattern.iter().enumerate() {
            masks[(c as usize) * nw + i / 64] |= 1u64 << (i % 64);
        }
        MwPeq::Flat { masks, nw }
    } else {
        let mut map: HashMap<char, Vec<u64>> = HashMap::new();
        for (i, &c) in pattern.iter().enumerate() {
            map.entry(c).or_insert_with(|| vec![0u64; nw])[i / 64] |= 1u64 << (i % 64);
        }
        MwPeq::Hash(map)
    }
}

// ---------------------------------------------------------------------------
// Jaro / Jaro-Winkler
// ---------------------------------------------------------------------------

/// rapidfuzz `jaro::calculate_similarity`: `transposition/=2` then
/// `(m/p_len + m/t_len + (m-t)/m) / 3` — exact operation order preserved.
#[inline]
fn jaro_calculate_similarity(
    p_len: usize,
    t_len: usize,
    common: usize,
    mut transposition: usize,
) -> f64 {
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

    jaro_similarity_mw(s1, s2, &peq_multiword(s1))
}

/// Multiword Jaro body over a PREPARED peq, so the one-vs-many batch API can
/// build the query's peq ONCE and reuse it across choices. Caller guarantees
/// `len1 >= 1`, `len2 >= 1`, and not (`len1 == 1 && len2 == 1`) — the edge cases
/// `jaro_similarity` handles before dispatching here. Byte-identical to the
/// inline path it replaced.
fn jaro_similarity_mw(s1: &[char], s2: &[char], peq: &MwPeq) -> f64 {
    let len1 = s1.len();
    let len2 = s2.len();
    // rapidfuzz bound = max(len1, len2) / 2 - 1 (both >= 1 here, so >= 0).
    let bound = std::cmp::max(len1, len2) / 2 - 1;

    // Bit-parallel FlaggedChars: bind each TEXT (s2) position to the LOWEST
    // unflagged PATTERN (s1) position within [j-bound, j+bound].
    let nw = nwords(len1);
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
        let pm = peq.get(cj, &empty);
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
pub fn levenshtein_distance(a: &[char], b: &[char]) -> usize {
    if a.is_empty() {
        return b.len();
    }
    if b.is_empty() {
        return a.len();
    }
    levenshtein_distance_mw(a, b, &peq_multiword(a))
}

/// Multiword Myers body over a PREPARED peq (one-vs-many amortisation). Caller
/// guarantees `a` and `b` non-empty.
fn levenshtein_distance_mw(a: &[char], b: &[char], peq: &MwPeq) -> usize {
    let m = a.len();
    let nw = nwords(m);
    let tmask = top_mask(m);
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

    for &cb in b {
        let eq = peq.get(cb, &empty);
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
fn lcs_length(a: &[char], b: &[char]) -> usize {
    if a.is_empty() || b.is_empty() {
        return 0;
    }
    lcs_length_mw(a, b, &peq_multiword(a))
}

/// Multiword Allison-Dix body over a PREPARED peq (one-vs-many amortisation).
/// Caller guarantees `a` and `b` non-empty.
fn lcs_length_mw(a: &[char], b: &[char], peq: &MwPeq) -> usize {
    let m = a.len();
    let nw = nwords(m);
    let tmask = top_mask(m);
    let empty = vec![0u64; nw];

    let mut v = vec![u64::MAX; nw];
    v[nw - 1] &= tmask;
    let mut u = vec![0u64; nw];
    let mut add = vec![0u64; nw];
    let mut sub = vec![0u64; nw];
    for &cb in b {
        let p = peq.get(cb, &empty);
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
// One-vs-many: BatchComparator / extract / cdist (amortised query peq)
// ---------------------------------------------------------------------------
//
// The dominant per-pair cost is building the query's position bitmap (~16us for
// a 600-char HashMap peq, ~3us flat). In a one-vs-many workload (a query vs many
// choices -- fuzzy dedup, record linkage, autocomplete) that build is pure
// repeated work. `BatchComparator` builds it ONCE and reuses it across every
// choice, exactly like rapidfuzz's `process.cdist` / `BatchComparator`. Results
// are byte-identical to the per-pair scorers (proven in tests).

/// Which normalized-similarity scorer the batch API uses (all on `[0, 1]`).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Scorer {
    /// `jaro_winkler` normalized similarity.
    JaroWinkler,
    /// `levenshtein` normalized similarity.
    Levenshtein,
    /// `indel` normalized similarity (== `fuzz::ratio`).
    Indel,
}

/// How a prepared query scores its choices.
///
/// For a `<= 64`-codepoint query the peq build is cheap (~80ns) and the per-pair
/// path already has a zero-alloc byte tier that beats rapidfuzz, so we just reuse
/// it (no `Vec<char>` collect per choice). Amortising only pays off for the
/// EXPENSIVE multiword peq (a long / non-Latin-1 query, ~16us HashMap / ~3us
/// flat), which we build once and reuse.
enum Prepared {
    PerPair(String),
    Mw { q: Vec<char>, peq: MwPeq },
}

/// A query prepared for one-vs-many scoring (rapidfuzz's `BatchComparator` /
/// `cdist`). Byte-identical to the per-pair scorers.
pub struct BatchComparator {
    prep: Prepared,
}

impl BatchComparator {
    pub fn new(query: &str) -> Self {
        let qc: Vec<char> = query.chars().collect();
        let prep = if qc.len() <= SW_MAX {
            Prepared::PerPair(query.to_string())
        } else {
            let peq = peq_multiword(&qc);
            Prepared::Mw { q: qc, peq }
        };
        BatchComparator { prep }
    }

    pub fn jaro_winkler(&self, choice: &str) -> f64 {
        match &self.prep {
            Prepared::PerPair(qs) => jaro_winkler(qs, choice),
            Prepared::Mw { q, peq } => {
                let c: Vec<char> = choice.chars().collect();
                // q.len() > 64, so only the empty-choice edge can fire here.
                let raw = if c.is_empty() {
                    0.0
                } else {
                    jaro_similarity_mw(q, &c, peq)
                };
                jaro_winkler_combine(q, &c, raw)
            }
        }
    }

    pub fn levenshtein(&self, choice: &str) -> f64 {
        match &self.prep {
            Prepared::PerPair(qs) => levenshtein_normalized_similarity(qs, choice),
            Prepared::Mw { q, peq } => {
                let c: Vec<char> = choice.chars().collect();
                let maximum = std::cmp::max(q.len(), c.len());
                let dist = if c.is_empty() {
                    q.len()
                } else {
                    levenshtein_distance_mw(q, &c, peq)
                };
                1.0 - (dist as f64 / maximum as f64)
            }
        }
    }

    pub fn indel(&self, choice: &str) -> f64 {
        match &self.prep {
            Prepared::PerPair(qs) => indel_ratio(qs, choice),
            Prepared::Mw { q, peq } => {
                let c: Vec<char> = choice.chars().collect();
                let maximum = q.len() + c.len();
                let lcs = if c.is_empty() {
                    0
                } else {
                    lcs_length_mw(q, &c, peq)
                };
                1.0 - ((maximum - 2 * lcs) as f64 / maximum as f64)
            }
        }
    }

    #[inline]
    pub fn score(&self, choice: &str, scorer: Scorer) -> f64 {
        match scorer {
            Scorer::JaroWinkler => self.jaro_winkler(choice),
            Scorer::Levenshtein => self.levenshtein(choice),
            Scorer::Indel => self.indel(choice),
        }
    }
}

/// One-vs-many top-k. Scores `query` against each choice with `scorer` (the
/// query peq is built once), keeps scores `>= score_cutoff`, and returns up to
/// `limit` `(choice_index, score)` pairs, highest score first (ties broken by
/// lower index). `limit == 0` means no limit.
pub fn extract(
    query: &str,
    choices: &[&str],
    scorer: Scorer,
    score_cutoff: f64,
    limit: usize,
) -> Vec<(usize, f64)> {
    let bc = BatchComparator::new(query);
    let mut scored: Vec<(usize, f64)> = choices
        .iter()
        .enumerate()
        .map(|(i, &c)| (i, bc.score(c, scorer)))
        .filter(|&(_, s)| s >= score_cutoff)
        .collect();
    scored.sort_by(|a, b| {
        b.1.partial_cmp(&a.1)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then(a.0.cmp(&b.0))
    });
    if limit != 0 && scored.len() > limit {
        scored.truncate(limit);
    }
    scored
}

/// Full one-vs-many score matrix: `out[i][j]` = `scorer(queries[i], choices[j])`.
/// Each query's peq is built once and reused across all choices.
pub fn cdist(queries: &[&str], choices: &[&str], scorer: Scorer) -> Vec<Vec<f64>> {
    queries
        .iter()
        .map(|&q| {
            let bc = BatchComparator::new(q);
            choices.iter().map(|&c| bc.score(c, scorer)).collect()
        })
        .collect()
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

// ---------------------------------------------------------------------------
// fuzz::* composite scorers  (rapidfuzz `fuzz` module, byte-identical)
// ---------------------------------------------------------------------------
//
// Faithful ports of rapidfuzz's PURE-PYTHON reference (`rapidfuzz/fuzz_py.py`
// + `distance/Indel_py.py`), all returning a float in [0, 100]. The rapidfuzz
// Rust crate 0.5.0 only exposes `fuzz::ratio`, so these are proven byte-for-byte
// against the Python `rapidfuzz.fuzz.*` oracle (the `emit_fuzz_corpus` test dumps
// a large deterministic corpus that `scripts/check_fuzz_parity.py` cross-checks).
//
// The internal `*_cut` variants thread rapidfuzz's `score_cutoff` EXACTLY as the
// reference does (WRatio bumps `score_cutoff = max(sc, end_ratio) / SCALE` before
// each sub-call, which can early-return 0 and change the final `max`). The public
// entry points pass `score_cutoff = 0.0`, which is behaviorally identical to the
// reference's `None` (a threshold of 0 never zeroes a non-negative score).

/// rapidfuzz's tokenizer whitespace test, matching the SHIPPED `rapidfuzz.fuzz`
/// (its C++ `common::is_space<CharT>`). This is WIDTH-DEPENDENT and does NOT
/// match Python `str.split()`: rapidfuzz picks the smallest integer element type
/// holding a string's max codepoint (uint8 for all-Latin1, uint16/uint32
/// otherwise), and `is_space` for the 8-bit type EXCLUDES `0x85` (NEL) and `0xA0`
/// (NBSP) even though they fit in a byte. So an all-Latin1 string does NOT split
/// on NEL/NBSP, while any string containing a codepoint > 0xFF does (matching the
/// pure-Python `str.split()` there). This is DECISIVE per-string (a narrow s1
/// keeps its NBSP regardless of a wide s2 — verified against the C++ default).
/// `0x1C..=0x1F` (info separators) split in both widths. All other cases verified
/// char-by-char over the Python whitespace set + `0x180E`/`0x200B`/`0xFEFF`.
#[inline]
fn is_rf_whitespace(c: char, wide: bool) -> bool {
    let cp = c as u32;
    if matches!(cp, 0x09..=0x0D | 0x1C..=0x1F | 0x20) {
        return true;
    }
    // NEL/NBSP + the Unicode space separators: only for a "wide" (any codepoint
    // > 0xFF) string. (The 0x1680.. separators are themselves > 0xFF, so they can
    // only appear in a wide string anyway; the load-bearing gate is NEL/NBSP.)
    wide && matches!(
        cp,
        0x85 | 0xA0 | 0x1680 | 0x2000..=0x200A | 0x2028 | 0x2029 | 0x202F | 0x205F | 0x3000
    )
}

/// rapidfuzz's `_split_sequence` on a string (its C++ tokenizer): split on runs
/// of whitespace (see `is_rf_whitespace`), drop empty tokens. Preserves order
/// and duplicates (the `token_sort` / list path). The whitespace set is chosen
/// per string from its own max codepoint (uint8 vs wider).
fn split_tokens(s: &str) -> Vec<String> {
    let wide = s.chars().any(|c| c as u32 > 0xFF);
    let mut out: Vec<String> = Vec::new();
    let mut cur = String::new();
    for c in s.chars() {
        if is_rf_whitespace(c, wide) {
            if !cur.is_empty() {
                out.push(std::mem::take(&mut cur));
            }
        } else {
            cur.push(c);
        }
    }
    if !cur.is_empty() {
        out.push(cur);
    }
    out
}

/// `_join_splitted_sequence`: single-space join (`" ".join(tokens)`); empty -> "".
#[inline]
fn join_tokens(tokens: &[String]) -> String {
    tokens.join(" ")
}

/// Codepoint length of `" ".join(tokens)` without building the string
/// (`len(_join_splitted_sequence(intersect))`, order-independent).
#[inline]
fn joined_len_refs(v: &[&String]) -> usize {
    if v.is_empty() {
        0
    } else {
        v.iter().map(|s| s.chars().count()).sum::<usize>() + v.len() - 1
    }
}

/// Indel normalized similarity on `[0, 1]` for codepoint slices (== `fuzz.ratio`
/// / 100). `maximum == 0 -> 1.0` (both empty). Byte-identical to `indel_ratio`.
#[inline]
fn indel_sim01_chars(a: &[char], b: &[char]) -> f64 {
    let maximum = a.len() + b.len();
    if maximum == 0 {
        return 1.0;
    }
    let lcs = lcs_length(a, b);
    let dist = maximum - 2 * lcs;
    1.0 - (dist as f64 / maximum as f64)
}

/// Indel edit distance `len1 + len2 - 2*lcs` for codepoint slices.
#[inline]
fn indel_distance_chars(a: &[char], b: &[char]) -> usize {
    let lcs = lcs_length(a, b);
    a.len() + b.len() - 2 * lcs
}

/// `_norm_distance(dist, lensum, score_cutoff)`: `100 - 100*dist/lensum`
/// (`lensum == 0 -> 100`), zeroed below `score_cutoff`. Operation order matches
/// the reference (`100 - 100 * dist / lensum`, left-associative).
#[inline]
fn norm_distance(dist: usize, lensum: usize, score_cutoff: f64) -> f64 {
    let score = if lensum != 0 {
        100.0 - 100.0 * (dist as f64) / (lensum as f64)
    } else {
        100.0
    };
    if score >= score_cutoff {
        score
    } else {
        0.0
    }
}

/// `ratio` on codepoint slices with a `score_cutoff` in `[0, 100]`. The reference
/// divides the cutoff by 100 and gates `norm_sim >= score_cutoff/100` before the
/// `* 100` scale-up.
#[inline]
fn ratio_cut(a: &[char], b: &[char], score_cutoff: f64) -> f64 {
    let sim01 = indel_sim01_chars(a, b);
    if sim01 >= score_cutoff / 100.0 {
        sim01 * 100.0
    } else {
        0.0
    }
}

/// `_partial_ratio_impl` (short-needle path): slides `s1` (the shorter string)
/// across `s2` and returns the best `fuzz.ratio` in `[0, 100]`. Assumes
/// `s1.len() <= s2.len()`. Replicates the three window loops, the
/// `s2[...] in set(s1)` guards, the running `score_cutoff`, and the exact-match
/// early return EXACTLY.
fn partial_ratio_impl(s1: &[char], s2: &[char], mut cutoff_frac: f64) -> f64 {
    let len1 = s1.len();
    let len2 = s2.len();
    // Empty needle: every window guard fails (set(s1) is empty) -> 0.0. (Also
    // sidesteps the reference's Python-negative-index `s2[i+len1-1]` when len1==0.)
    if len1 == 0 {
        return 0.0;
    }
    let s1_set: HashSet<char> = s1.iter().copied().collect();
    let mut best = 0.0_f64; // best sim01 seen (fraction)

    // Returns true on an exact-match early exit (score forced to 100).
    macro_rules! consider {
        ($window:expr) => {{
            let sim = indel_sim01_chars(s1, $window);
            let ls = if sim >= cutoff_frac { sim } else { 0.0 };
            if ls > best {
                best = ls;
                cutoff_frac = ls;
                if ls == 1.0 {
                    return 100.0;
                }
            }
        }};
    }

    // loop 1: windows s2[0..i], i in 1..len1  (guard: s2[i-1] in set(s1))
    for i in 1..len1 {
        if !s1_set.contains(&s2[i - 1]) {
            continue;
        }
        consider!(&s2[0..i]);
    }
    // loop 2: windows s2[i..i+len1], i in 0..len2-len1  (guard: s2[i+len1-1])
    for i in 0..(len2 - len1) {
        if !s1_set.contains(&s2[i + len1 - 1]) {
            continue;
        }
        consider!(&s2[i..i + len1]);
    }
    // loop 3: windows s2[i..len2], i in len2-len1..len2  (guard: s2[i])
    for i in (len2 - len1)..len2 {
        if !s1_set.contains(&s2[i]) {
            continue;
        }
        consider!(&s2[i..len2]);
    }
    best * 100.0
}

/// `partial_ratio` / `partial_ratio_alignment` score on codepoint slices, in
/// `[0, 100]`. Runs `_partial_ratio_impl` on the shorter-in-longer, adds the
/// `len1 == len2` swapped re-run, and applies the outer `score < score_cutoff ->
/// None (0)` gate.
fn partial_ratio_cut(s1: &[char], s2: &[char], score_cutoff: f64) -> f64 {
    // Both empty -> ScoreAlignment(100.0), returned before the cutoff gate.
    if s1.is_empty() && s2.is_empty() {
        return 100.0;
    }
    let len1 = s1.len();
    let len2 = s2.len();
    let (shorter, longer) = if len1 <= len2 { (s1, s2) } else { (s2, s1) };

    let mut res_score = partial_ratio_impl(shorter, longer, score_cutoff / 100.0);
    if res_score != 100.0 && len1 == len2 {
        let sc2 = f64::max(score_cutoff, res_score);
        let res2 = partial_ratio_impl(longer, shorter, sc2 / 100.0);
        if res2 > res_score {
            res_score = res2;
        }
    }
    if res_score < score_cutoff {
        return 0.0;
    }
    res_score
}

/// `token_sort_ratio`: sort the token LIST (dups kept), join, `ratio`.
fn token_sort_ratio_cut(s1: &str, s2: &str, score_cutoff: f64) -> f64 {
    let mut t1 = split_tokens(s1);
    t1.sort();
    let mut t2 = split_tokens(s2);
    t2.sort();
    let j1: Vec<char> = join_tokens(&t1).chars().collect();
    let j2: Vec<char> = join_tokens(&t2).chars().collect();
    ratio_cut(&j1, &j2, score_cutoff)
}

/// `token_set_ratio`: the intersect/diff formula, EXACTLY as `fuzz_py.py`.
fn token_set_ratio_cut(s1: &str, s2: &str, score_cutoff: f64) -> f64 {
    let a: BTreeSet<String> = split_tokens(s1).into_iter().collect();
    let b: BTreeSet<String> = split_tokens(s2).into_iter().collect();
    if a.is_empty() || b.is_empty() {
        return 0.0;
    }
    let inter: Vec<&String> = a.intersection(&b).collect();
    let diff_ab: Vec<String> = a.difference(&b).cloned().collect(); // BTreeSet -> sorted
    let diff_ba: Vec<String> = b.difference(&a).cloned().collect();
    // one sentence is part of the other one
    if !inter.is_empty() && (diff_ab.is_empty() || diff_ba.is_empty()) {
        return 100.0;
    }

    let diff_ab_joined = join_tokens(&diff_ab);
    let diff_ba_joined = join_tokens(&diff_ba);
    let ab_len = diff_ab_joined.chars().count();
    let ba_len = diff_ba_joined.chars().count();
    let sect_len = joined_len_refs(&inter);
    let sect_flag = usize::from(sect_len != 0);

    let sect_ab_len = sect_len + sect_flag + ab_len;
    let sect_ba_len = sect_len + sect_flag + ba_len;
    let lensum = sect_ab_len + sect_ba_len;

    let mut result = 0.0_f64;
    let cutoff_distance = (lensum as f64 * (1.0 - score_cutoff / 100.0)).ceil();
    let dab: Vec<char> = diff_ab_joined.chars().collect();
    let dba: Vec<char> = diff_ba_joined.chars().collect();
    let dist = indel_distance_chars(&dab, &dba);
    if (dist as f64) <= cutoff_distance {
        result = norm_distance(dist, lensum, score_cutoff);
    }

    // exit early since the other ratios are 0
    if sect_len == 0 {
        return result;
    }

    let sect_ab_dist = sect_flag + ab_len;
    let sect_ab_ratio = norm_distance(sect_ab_dist, sect_len + sect_ab_len, score_cutoff);
    let sect_ba_dist = sect_flag + ba_len;
    let sect_ba_ratio = norm_distance(sect_ba_dist, sect_len + sect_ba_len, score_cutoff);

    result.max(sect_ab_ratio).max(sect_ba_ratio)
}

/// `token_ratio` = `max(token_set_ratio, token_sort_ratio)`.
fn token_ratio_cut(s1: &str, s2: &str, score_cutoff: f64) -> f64 {
    f64::max(
        token_set_ratio_cut(s1, s2, score_cutoff),
        token_sort_ratio_cut(s1, s2, score_cutoff),
    )
}

/// `partial_token_sort_ratio`: sort the token LIST, join, `partial_ratio`.
fn partial_token_sort_ratio_cut(s1: &str, s2: &str, score_cutoff: f64) -> f64 {
    let mut t1 = split_tokens(s1);
    t1.sort();
    let mut t2 = split_tokens(s2);
    t2.sort();
    let j1: Vec<char> = join_tokens(&t1).chars().collect();
    let j2: Vec<char> = join_tokens(&t2).chars().collect();
    partial_ratio_cut(&j1, &j2, score_cutoff)
}

/// `partial_token_set_ratio`: common-word early-100, else `partial_ratio` of the
/// sorted-unique diffs.
fn partial_token_set_ratio_cut(s1: &str, s2: &str, score_cutoff: f64) -> f64 {
    let a: BTreeSet<String> = split_tokens(s1).into_iter().collect();
    let b: BTreeSet<String> = split_tokens(s2).into_iter().collect();
    if a.is_empty() || b.is_empty() {
        return 0.0;
    }
    if a.intersection(&b).next().is_some() {
        return 100.0;
    }
    let diff_ab: Vec<String> = a.difference(&b).cloned().collect();
    let diff_ba: Vec<String> = b.difference(&a).cloned().collect();
    let j1: Vec<char> = join_tokens(&diff_ab).chars().collect();
    let j2: Vec<char> = join_tokens(&diff_ba).chars().collect();
    partial_ratio_cut(&j1, &j2, score_cutoff)
}

/// `partial_token_ratio` = max of the sorted-list partial ratio and (unless the
/// diffs equal the whole token lists) the sorted-diff partial ratio, threading
/// `score_cutoff = max(cutoff, result)` between the two.
fn partial_token_ratio_cut(s1: &str, s2: &str, score_cutoff: f64) -> f64 {
    let list_a = split_tokens(s1);
    let list_b = split_tokens(s2);
    let a: BTreeSet<String> = list_a.iter().cloned().collect();
    let b: BTreeSet<String> = list_b.iter().cloned().collect();
    if a.intersection(&b).next().is_some() {
        return 100.0;
    }
    let diff_ab: Vec<String> = a.difference(&b).cloned().collect();
    let diff_ba: Vec<String> = b.difference(&a).cloned().collect();

    let mut sorted_a = list_a.clone();
    sorted_a.sort();
    let mut sorted_b = list_b.clone();
    sorted_b.sort();
    let ja: Vec<char> = join_tokens(&sorted_a).chars().collect();
    let jb: Vec<char> = join_tokens(&sorted_b).chars().collect();
    let result = partial_ratio_cut(&ja, &jb, score_cutoff);

    // do not calculate the same partial_ratio twice
    if list_a.len() == diff_ab.len() && list_b.len() == diff_ba.len() {
        return result;
    }
    let cutoff2 = f64::max(score_cutoff, result);
    let jda: Vec<char> = join_tokens(&diff_ab).chars().collect();
    let jdb: Vec<char> = join_tokens(&diff_ba).chars().collect();
    result.max(partial_ratio_cut(&jda, &jdb, cutoff2))
}

// ---- Public fuzz entry points (score_cutoff = 0, == reference `None`) --------

/// `fuzz.ratio` in `[0, 100]` (Indel normalized similarity * 100).
pub fn ratio(a: &str, b: &str) -> f64 {
    indel_ratio(a, b) * 100.0
}

/// `fuzz.partial_ratio` in `[0, 100]`.
pub fn partial_ratio(a: &str, b: &str) -> f64 {
    let s1: Vec<char> = a.chars().collect();
    let s2: Vec<char> = b.chars().collect();
    partial_ratio_cut(&s1, &s2, 0.0)
}

/// `fuzz.token_sort_ratio` in `[0, 100]`.
pub fn token_sort_ratio(a: &str, b: &str) -> f64 {
    token_sort_ratio_cut(a, b, 0.0)
}

/// `fuzz.token_set_ratio` in `[0, 100]`.
pub fn token_set_ratio(a: &str, b: &str) -> f64 {
    token_set_ratio_cut(a, b, 0.0)
}

/// `fuzz.token_ratio` = `max(token_set_ratio, token_sort_ratio)`.
pub fn token_ratio(a: &str, b: &str) -> f64 {
    token_ratio_cut(a, b, 0.0)
}

/// `fuzz.partial_token_sort_ratio` in `[0, 100]`.
pub fn partial_token_sort_ratio(a: &str, b: &str) -> f64 {
    partial_token_sort_ratio_cut(a, b, 0.0)
}

/// `fuzz.partial_token_set_ratio` in `[0, 100]`.
pub fn partial_token_set_ratio(a: &str, b: &str) -> f64 {
    partial_token_set_ratio_cut(a, b, 0.0)
}

/// `fuzz.partial_token_ratio` in `[0, 100]`.
pub fn partial_token_ratio(a: &str, b: &str) -> f64 {
    partial_token_ratio_cut(a, b, 0.0)
}

/// `fuzz.WRatio` — the composite weighted ratio in `[0, 100]`. Empty input -> 0.
pub fn w_ratio(a: &str, b: &str) -> f64 {
    // in FuzzyWuzzy this returns 0 -- keep parity (rapidfuzz issue #110).
    if a.is_empty() || b.is_empty() {
        return 0.0;
    }
    const UNBASE_SCALE: f64 = 0.95;
    let s1: Vec<char> = a.chars().collect();
    let s2: Vec<char> = b.chars().collect();
    let len1 = s1.len();
    let len2 = s2.len();
    let len_ratio = if len1 > len2 {
        len1 as f64 / len2 as f64
    } else {
        len2 as f64 / len1 as f64
    };

    let mut score_cutoff = 0.0_f64;
    let end_ratio = ratio_cut(&s1, &s2, score_cutoff);
    if len_ratio < 1.5 {
        score_cutoff = f64::max(score_cutoff, end_ratio) / UNBASE_SCALE;
        return f64::max(
            end_ratio,
            token_ratio_cut(a, b, score_cutoff) * UNBASE_SCALE,
        );
    }

    let partial_scale = if len_ratio <= 8.0 { 0.9 } else { 0.6 };
    score_cutoff = f64::max(score_cutoff, end_ratio) / partial_scale;
    let end_ratio = f64::max(
        end_ratio,
        partial_ratio_cut(&s1, &s2, score_cutoff) * partial_scale,
    );

    score_cutoff = f64::max(score_cutoff, end_ratio) / UNBASE_SCALE;
    f64::max(
        end_ratio,
        partial_token_ratio_cut(a, b, score_cutoff) * UNBASE_SCALE * partial_scale,
    )
}

/// `fuzz.QRatio` = `ratio`, except two empty (or one-empty) strings return 0.
pub fn q_ratio(a: &str, b: &str) -> f64 {
    if a.is_empty() || b.is_empty() {
        return 0.0;
    }
    indel_ratio(a, b) * 100.0
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
            ("", ""),
            ("a", ""),
            ("", "a"),
            ("a", "a"),
            ("a", "b"),
            ("ab", "ba"),
            ("abc", "acb"),
            ("martha", "marhta"),
            ("dwayne", "duane"),
            ("dixon", "dicksonx"),
            ("aabbcc", "abcabc"),
            ("2026-07-26", "2026-07-25"),
            ("caaba", "aabac"),
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
        let uni = [
            "日本語abc",
            "Ωμέγα x",
            "naïve 日",
            "abc日def",
            "Ω",
            "café日",
        ];
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
            (0usize, 0usize),
            (0, 5),
            (5, 0),
            (1, 1),
            (1, 9),
            (9, 1),
            (2, 2),
            (2, 200),
            (64, 64),
            (64, 200),
            (65, 3),
            (66, 66),
        ] {
            check_pair(&mk(la, 0), &mk(lb, 3));
        }
        let mut rng: u64 = 0xA5C110FF1CE;
        for _ in 0..30_000 {
            let la = (next(&mut rng) as usize) % 65; // 0..=64: pattern in byte-tier range
            let lb = (next(&mut rng) as usize) % 200; // text may spill past 64
            let a: String = (0..la)
                .map(|_| AB[(next(&mut rng) as usize) % AB.len()])
                .collect();
            let b: String = (0..lb)
                .map(|_| AB[(next(&mut rng) as usize) % AB.len()])
                .collect();
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

    #[test]
    fn batch_matches_per_pair() {
        // BatchComparator / extract / cdist just amortise the query peq, so every
        // result must be bit-for-bit identical to the per-pair scorers.
        let mut rng: u64 = 0xBA7C40FF1CE;
        for _ in 0..20_000 {
            let ml = [1usize, 3, 13, 40, 64, 100, 200][(next(&mut rng) as usize) % 7];
            let q = gen(&mut rng, ml);
            let c = gen(&mut rng, ml);
            let bc = BatchComparator::new(&q);
            assert_eq!(
                bc.jaro_winkler(&c).to_bits(),
                jaro_winkler(&q, &c).to_bits()
            );
            assert_eq!(
                bc.levenshtein(&c).to_bits(),
                levenshtein_normalized_similarity(&q, &c).to_bits()
            );
            assert_eq!(bc.indel(&c).to_bits(), indel_ratio(&q, &c).to_bits());
        }
        // extract: cutoff + top-k + descending order + exact match ranks first.
        let choices = [
            "johnathan smith",
            "jonathan smith",
            "jon smith",
            "jane doe",
            "j smith",
        ];
        let got = extract("jonathan smith", &choices, Scorer::JaroWinkler, 0.5, 3);
        assert!(got.len() <= 3 && !got.is_empty());
        assert_eq!(got[0].0, 1); // the exact match
        for w in got.windows(2) {
            assert!(w[0].1 >= w[1].1);
        }
        for &(_, s) in &got {
            assert!(s >= 0.5);
        }
        // cdist == per-pair over the full matrix.
        let qs = ["abc", "café", ""];
        let mat = cdist(&qs, &choices, Scorer::Indel);
        for (i, &q) in qs.iter().enumerate() {
            for (j, &c) in choices.iter().enumerate() {
                assert_eq!(mat[i][j].to_bits(), indel_ratio(q, c).to_bits());
            }
        }
    }

    // ---- fuzz::* composite scorers: directed + emitted-corpus parity ----------
    //
    // The rapidfuzz Rust crate 0.5.0 exposes only `fuzz::ratio`, so the composite
    // family (WRatio / token_* / partial_*) is proven against the Python
    // `rapidfuzz.fuzz.*` oracle: `emit_fuzz_corpus` writes a large deterministic
    // corpus + our results to `target/goldenfuzz_fuzz_corpus.jsonl`, which
    // `scripts/check_fuzz_parity.py` cross-checks (equality within 1e-9).

    #[test]
    fn ratio_matches_crate_fuzz_scaled() {
        // `fuzz::ratio` IS in the crate -> a cheap in-Rust oracle for the *100 scale.
        let mut rng: u64 = 0x1234_ABCD_9876;
        for _ in 0..5_000 {
            let ml = [1usize, 3, 8, 20][(next(&mut rng) as usize) % 4];
            let a = gen(&mut rng, ml);
            let b = gen(&mut rng, ml);
            let mine = ratio(&a, &b);
            // The rapidfuzz CRATE's `fuzz::ratio` is [0,1]; Python's is *100.
            let theirs = rf_fuzz::ratio(a.chars(), b.chars()) * 100.0;
            assert_bits("ratio*100", &a, &b, mine, theirs);
            // QRatio == ratio for non-empty, 0 for empty.
            let q = q_ratio(&a, &b);
            if a.is_empty() || b.is_empty() {
                assert_eq!(q, 0.0);
            } else {
                assert_eq!(q.to_bits(), mine.to_bits());
            }
        }
    }

    #[test]
    fn fuzz_directed_identities() {
        // Exact-match / subset identities are float-exact (100.0) in the reference.
        assert_eq!(
            token_sort_ratio("fuzzy wuzzy was a bear", "wuzzy fuzzy was a bear"),
            100.0
        );
        assert_eq!(
            token_set_ratio("fuzzy was a bear", "fuzzy fuzzy was a bear"),
            100.0
        );
        assert_eq!(
            token_set_ratio("fuzzy was a bear but not a dog", "fuzzy was a bear"),
            100.0
        );
        assert_eq!(token_ratio("a b c", "c b a"), 100.0);
        // ratio identities
        assert_eq!(ratio("", ""), 100.0);
        assert_eq!(q_ratio("", ""), 0.0);
        assert_eq!(q_ratio("abc", ""), 0.0);
        assert_eq!(ratio("abc", "abc"), 100.0);
        assert_eq!(w_ratio("", "abc"), 0.0);
        assert_eq!(w_ratio("abc", "abc"), 100.0);
        assert_eq!(partial_ratio("", ""), 100.0);
        assert_eq!(partial_ratio("abc", "abc"), 100.0);
        // rapidfuzz splits 0x1c..0x1f (info separators) in both widths.
        assert_eq!(split_tokens("a\u{1c}b\tc  d"), vec!["a", "b", "c", "d"]);
        assert_eq!(token_sort_ratio("b a", "a\u{1c}b"), 100.0);
        // WIDTH-DEPENDENT NBSP: an all-Latin1 string does NOT split on NBSP
        // (matches C++ `is_space<uint8>`), so "a\u{a0}b" stays one token; a
        // string with a codepoint > 0xFF DOES split on NBSP.
        assert_eq!(split_tokens("a\u{a0}b"), vec!["a\u{a0}b"]);
        assert_eq!(split_tokens("\u{4e00}\u{a0}b"), vec!["\u{4e00}", "b"]);
        assert_eq!(split_tokens("a\u{85}b"), vec!["a\u{85}b"]);
    }

    // Deterministic corpus spanning every required category. Returns >5000 pairs.
    fn fuzz_corpus() -> Vec<(String, String)> {
        const TOKS: &[&str] = &[
            "john",
            "jon",
            "jonathan",
            "smith",
            "smyth",
            "acme",
            "corp",
            "corporation",
            "inc",
            "ltd",
            "the",
            "of",
            "and",
            "a",
            "bear",
            "fuzzy",
            "wuzzy",
            "was",
            "dog",
            "cat",
            "not",
            "but",
            "café",
            "naïve",
            "日本",
            "東京",
            "Ωμέγα",
            "müller",
            "strasse",
            "o'brien",
            "mary",
            "anne",
            "van",
            "der",
            "berg",
            "123",
            "2026",
            "x",
            "ab",
            "abc",
            "abcd",
            "z",
        ];
        const WS: &[&str] = &[" ", "  ", "\t", " \u{1c} ", "\n", "   ", " \u{a0}"];
        let mut rng: u64 = 0x00C0_FFEE_1234_5678;
        let mut out: Vec<(String, String)> = Vec::new();
        let tok = |rng: &mut u64| TOKS[(next(rng) as usize) % TOKS.len()];
        let ws = |rng: &mut u64| WS[(next(rng) as usize) % WS.len()];
        let phrase = |rng: &mut u64, k: usize| -> Vec<String> {
            (0..k).map(|_| tok(rng).to_string()).collect()
        };
        let join = |rng: &mut u64, ts: &[String]| -> String {
            let mut s = String::new();
            for (i, t) in ts.iter().enumerate() {
                if i > 0 {
                    s.push_str(ws(rng));
                }
                s.push_str(t);
            }
            s
        };

        // Directed edge cases.
        for &(a, b) in &[
            ("", ""),
            ("a", ""),
            ("", "a"),
            ("a", "a"),
            ("abc", "abc"),
            ("this is a test", "this is a test!"),
            ("fuzzy wuzzy was a bear", "wuzzy fuzzy was a bear"),
            ("fuzzy was a bear", "fuzzy fuzzy was a bear"),
            (
                "fuzzy was a bear but not a dog",
                "fuzzy was a bear but not a cat",
            ),
            ("fuzzy was a bear but not a dog", "fuzzy was a bear"),
            ("a certain string", "cetain"),
            ("jonathan smith", "jon smith"),
            ("dixon", "dicksonx"),
            ("   ", "       "),
            ("a\u{1c}b c", "c b a"),
            ("café", "cafe"),
            ("Ωμέγα 日本", "日本 Ωμέγα"),
            ("mary anne smith", "smith mary anne"),
        ] {
            out.push((a.to_string(), b.to_string()));
        }

        for _ in 0..6500 {
            let cat = (next(&mut rng) as usize) % 12;
            let (s1, s2) = match cat {
                0 => {
                    // single tokens
                    (tok(&mut rng).to_string(), tok(&mut rng).to_string())
                }
                1 => {
                    // multi-token phrases (2..6 tokens each)
                    let k1 = 2 + (next(&mut rng) as usize) % 5;
                    let k2 = 2 + (next(&mut rng) as usize) % 5;
                    let p1 = phrase(&mut rng, k1);
                    let p2 = phrase(&mut rng, k2);
                    (join(&mut rng, &p1), join(&mut rng, &p2))
                }
                2 => {
                    // identical
                    let k = 1 + (next(&mut rng) as usize) % 5;
                    let p = phrase(&mut rng, k);
                    let s = join(&mut rng, &p);
                    (s.clone(), s)
                }
                3 => {
                    // shuffled tokens (same set, reordered) -> token_sort/set 100
                    let k = 2 + (next(&mut rng) as usize) % 5;
                    let mut p = phrase(&mut rng, k);
                    let s1 = join(&mut rng, &p);
                    // rotate
                    p.rotate_left(1 + (next(&mut rng) as usize) % k);
                    (s1, join(&mut rng, &p))
                }
                4 => {
                    // subset: s2 is a subset of s1's tokens
                    let k = 3 + (next(&mut rng) as usize) % 4;
                    let p = phrase(&mut rng, k);
                    let take = 1 + (next(&mut rng) as usize) % k;
                    let sub: Vec<String> = p.iter().take(take).cloned().collect();
                    (join(&mut rng, &p), join(&mut rng, &sub))
                }
                5 => {
                    // length ratio > 8: token repeated many times
                    let t = tok(&mut rng).to_string();
                    let n = 10 + (next(&mut rng) as usize) % 8;
                    let big: Vec<String> = std::iter::repeat_n(t.clone(), n).collect();
                    (t, join(&mut rng, &big))
                }
                6 => {
                    // length ratio 1.5..8
                    let k = 1 + (next(&mut rng) as usize) % 2;
                    let p = phrase(&mut rng, k);
                    let s1 = join(&mut rng, &p);
                    let reps = 2 + (next(&mut rng) as usize) % 3;
                    let mut big = Vec::new();
                    for _ in 0..reps {
                        big.extend(p.iter().cloned());
                    }
                    (s1, join(&mut rng, &big))
                }
                7 => {
                    // length ratio < 1.5 (tiny edit)
                    let k = 1 + (next(&mut rng) as usize) % 4;
                    let p = phrase(&mut rng, k);
                    let s1 = join(&mut rng, &p);
                    let mut s2 = s1.clone();
                    if (next(&mut rng) & 1) == 0 {
                        s2.push('x');
                    } else if !s2.is_empty() {
                        s2.pop();
                    }
                    (s1, s2)
                }
                8 => {
                    // repeated tokens within a phrase
                    let t = tok(&mut rng).to_string();
                    let u = tok(&mut rng).to_string();
                    let p1 = vec![t.clone(), t.clone(), u.clone()];
                    let p2 = vec![t.clone(), u.clone(), u.clone()];
                    (join(&mut rng, &p1), join(&mut rng, &p2))
                }
                9 => {
                    // unicode-heavy
                    let uni = ["café", "naïve", "日本", "東京", "Ωμέγα", "müller"];
                    let a = uni[(next(&mut rng) as usize) % uni.len()];
                    let b = uni[(next(&mut rng) as usize) % uni.len()];
                    let w1 = ws(&mut rng);
                    let t1 = tok(&mut rng);
                    let t2 = tok(&mut rng);
                    let w2 = ws(&mut rng);
                    (format!("{a}{w1}{t1}"), format!("{t2}{w2}{b}"))
                }
                10 => {
                    // one empty
                    let p = phrase(&mut rng, 3);
                    let s = join(&mut rng, &p);
                    if (next(&mut rng) & 1) == 0 {
                        (String::new(), s)
                    } else {
                        (s, String::new())
                    }
                }
                _ => {
                    // char-level edits on a single token
                    let t = tok(&mut rng);
                    let mut cs: Vec<char> = t.chars().collect();
                    if !cs.is_empty() {
                        let i = (next(&mut rng) as usize) % cs.len();
                        cs[i] = TOKS[(next(&mut rng) as usize) % 8].chars().next().unwrap();
                    }
                    (t.to_string(), cs.into_iter().collect())
                }
            };
            out.push((s1, s2));
        }
        out
    }

    fn json_escape(s: &str) -> String {
        let mut out = String::from("\"");
        for c in s.chars() {
            match c {
                '"' => out.push_str("\\\""),
                '\\' => out.push_str("\\\\"),
                '\n' => out.push_str("\\n"),
                '\r' => out.push_str("\\r"),
                '\t' => out.push_str("\\t"),
                c if (c as u32) < 0x20 => out.push_str(&format!("\\u{:04x}", c as u32)),
                c => out.push(c),
            }
        }
        out.push('"');
        out
    }

    #[test]
    fn emit_fuzz_corpus() {
        // Dump the corpus + our fuzz.* results as JSONL for the Python oracle
        // cross-check (scripts/check_fuzz_parity.py). Full-precision floats via
        // Display (shortest round-trippable) so Python recovers exact bits.
        let corpus = fuzz_corpus();
        let mut buf = String::new();
        for (a, b) in &corpus {
            let scores = [
                ratio(a, b),
                partial_ratio(a, b),
                token_sort_ratio(a, b),
                token_set_ratio(a, b),
                token_ratio(a, b),
                partial_token_sort_ratio(a, b),
                partial_token_set_ratio(a, b),
                partial_token_ratio(a, b),
                w_ratio(a, b),
                q_ratio(a, b),
            ];
            // sanity: all in [0, 100]
            for s in scores {
                assert!(
                    (0.0..=100.0).contains(&s),
                    "out of range {s} for ({a:?},{b:?})"
                );
            }
            buf.push_str(&format!(
                "{{\"s1\":{},\"s2\":{},\"ratio\":{},\"partial_ratio\":{},\"token_sort_ratio\":{},\"token_set_ratio\":{},\"token_ratio\":{},\"partial_token_sort_ratio\":{},\"partial_token_set_ratio\":{},\"partial_token_ratio\":{},\"WRatio\":{},\"QRatio\":{}}}\n",
                json_escape(a), json_escape(b),
                scores[0], scores[1], scores[2], scores[3], scores[4],
                scores[5], scores[6], scores[7], scores[8], scores[9],
            ));
        }
        let dir = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("target");
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("goldenfuzz_fuzz_corpus.jsonl");
        std::fs::write(&path, buf).unwrap();
        assert!(corpus.len() >= 5000, "corpus too small: {}", corpus.len());
        eprintln!("emitted {} pairs to {}", corpus.len(), path.display());
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
