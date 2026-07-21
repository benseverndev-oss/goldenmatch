//! Canonical string scorers backed by the `rapidfuzz` Rust crate — the single
//! source of truth shared (by construction) between the `goldenmatch._native`
//! PyO3 extension and the `datafusion-udf` FFI ScalarUDFs. Both link this crate,
//! so the per-pair scoring is identical across surfaces; parity is structural,
//! not asserted after the fact.
//!
//! This crate is intentionally pyo3-free. The `native` crate keeps thin
//! `#[pyfunction]` shims that delegate here; the FFI UDFs call these `pub fn`s
//! directly. All functions operate on Unicode chars (codepoints), matching
//! rapidfuzz.
use rapidfuzz::distance::{damerau_levenshtein, jaro_winkler, levenshtein};
use rapidfuzz::fuzz;

// Fellegi–Sunter EM training core (pyo3-free numeric heart). PR-C / C1 of the
// FS Rust+Arrow-only epic; the `native` crate will add the Arrow/#[pyfunction]
// shim in C2. No wiring yet — this module is self-contained + unit-tested.
pub mod em_core;

/// `rapidfuzz.fuzz.token_sort_ratio` preprocessing: split on whitespace, sort
/// the tokens, rejoin with a single space. (Then `fuzz::ratio` on the result.)
/// Private: its only callers (`token_sort_ratio` + `score_one`) live in this
/// crate.
fn token_sort_string(s: &str) -> String {
    let mut toks: Vec<&str> = s.split_whitespace().collect();
    toks.sort_unstable();
    toks.join(" ")
}

// ---- Scorer surface (scale matches score_buckets._resolve_score_pair_callable:
//      jaro_winkler/levenshtein on 0-1, token_sort_ratio on 0-100) ----

pub fn jaro_winkler_similarity(a: &str, b: &str) -> f64 {
    // rapidfuzz JaroWinkler default prefix_weight = 0.1.
    jaro_winkler::normalized_similarity(a.chars(), b.chars())
}

pub fn levenshtein_similarity(a: &str, b: &str) -> f64 {
    // rapidfuzz Levenshtein default uniform weights (1, 1, 1).
    levenshtein::normalized_similarity(a.chars(), b.chars())
}

/// token_sort_ratio on the 0-100 scale (score_field divides by 100).
pub fn token_sort_ratio(a: &str, b: &str) -> f64 {
    let sa = token_sort_string(a);
    let sb = token_sort_string(b);
    // rapidfuzz-rs fuzz::ratio returns [0, 1]; Python fuzz.ratio is [0, 100].
    fuzz::ratio(sa.chars(), sb.chars()) * 100.0
}

/// TS/Python `token_sort_ratio` preprocessing for the **WASM TS-parity path**:
/// lowercase, replace every non-`[a-z0-9 + whitespace]` char with a space, then
/// split / sort / join (matching goldenmatch TS `tokenSortRatio`'s
/// `.toLowerCase().replace(/[^a-z0-9\s]/g," ")` normalize), then `fuzz::ratio`
/// (== rapidfuzz `Indel.normalized_similarity`) on `[0, 1]`.
///
/// DISTINCT from `score_one(2)` / `token_sort_string`, which do NOT normalize
/// (the pinned native asymmetry the FFI/native path depends on) — do not merge.
/// Used only by `score-wasm` to give the TS opt-in backend token_sort coverage.
pub fn token_sort_normalized_ratio(a: &str, b: &str) -> f64 {
    fn normalize(s: &str) -> String {
        let cleaned: String = s
            .to_lowercase()
            .chars()
            .map(|c| {
                if c.is_ascii_alphanumeric() || c.is_whitespace() {
                    c
                } else {
                    ' '
                }
            })
            .collect();
        let mut toks: Vec<&str> = cleaned.split_whitespace().collect();
        toks.sort_unstable();
        toks.join(" ")
    }
    fuzz::ratio(normalize(a).chars(), normalize(b).chars())
}

/// Canonicalize an ISO-8601 `YYYY-MM-DD` date to its 8 packed digits
/// (`YYYYMMDD`), or `None` if the string isn't that exact shape. Deliberately
/// strict (no locale parsing, no `YYYY/MM/DD`): the point is to recognize a real
/// ISO date so a typo can be told apart from a different date; anything else
/// falls back to plain edit distance. Month/day RANGES are not validated -- a
/// malformed-but-ISO-shaped value still scores structurally, which is fine for a
/// similarity (and avoids dragging a calendar into the kernel).
fn iso_date_digits(s: &str) -> Option<[u8; 8]> {
    let b = s.as_bytes();
    if b.len() != 10 || b[4] != b'-' || b[7] != b'-' {
        return None;
    }
    let mut out = [0u8; 8];
    let mut oi = 0;
    for (i, &c) in b.iter().enumerate() {
        if i == 4 || i == 7 {
            continue;
        }
        if !c.is_ascii_digit() {
            return None;
        }
        out[oi] = c;
        oi += 1;
    }
    Some(out)
}

/// Date-aware similarity on [0, 1]. `jaro_winkler` on an ISO date scores
/// unrelated birthdays 0.80+ (the fixed `YYYY-MM-DD` shape, shared digit
/// alphabet, and common `19..`/`20..` prefix dominate) -- it cannot separate a
/// typo from a different person (#1858). This parses both sides as ISO dates and
/// uses the Damerau-Levenshtein edit distance over the 8 canonical digits
/// (transposition-aware -- swapped digits are ONE edit), mapped so a single-digit
/// typo stays above a typical 0.85 cutoff while an unrelated date cliffs to 0:
///
///   d == 0 -> 1.00 (same date)     d == 2 -> 0.75 (two edits -- weak)
///   d == 1 -> 0.90 (one typo)      d >= 3 -> 0.00 (unrelated)
///
/// Mirrors Splink's `DamerauLevenshtein <= 2` date comparison. When EITHER side
/// isn't an ISO date, degrades to `levenshtein` on the raw strings -- the
/// like-for-like the issue recommends over `jaro_winkler`, never worse.
pub fn date_similarity(a: &str, b: &str) -> f64 {
    match (iso_date_digits(a), iso_date_digits(b)) {
        (Some(da), Some(db)) => {
            let d = damerau_levenshtein::distance(da.iter().copied(), db.iter().copied());
            match d {
                0 => 1.0,
                1 => 0.90,
                2 => 0.75,
                _ => 0.0,
            }
        }
        // Not both ISO dates: fall back to plain normalized edit distance.
        _ => levenshtein::normalized_similarity(a.chars(), b.chars()),
    }
}

/// Character-trigram (q-gram) Jaccard set for one raw string, mirroring Python
/// `goldenmatch.core.scorer._qgram_set` (n=3): lowercase, pad each side with
/// `n-1` `#` sentinels, and take the set of length-`n` codepoint substrings.
/// Padding means even the empty string yields the all-`#` gram, so the set is
/// never empty for n>=2 (the Python `if not union` branch is unreachable, but
/// `qgram_similarity` guards it anyway).
fn qgram_set(s: &str) -> std::collections::HashSet<[char; 3]> {
    const N: usize = 3;
    // Build the padded codepoint sequence directly into one Vec -- (N-1) `#`
    // sentinels, the lowercased chars, then (N-1) `#` -- with no intermediate
    // padding/`format!` `String` allocations (only `to_lowercase`, which Unicode
    // case mapping requires).
    let lower = s.to_lowercase();
    let mut chars: Vec<char> = Vec::with_capacity(lower.chars().count() + 2 * (N - 1));
    chars.extend(std::iter::repeat_n('#', N - 1));
    chars.extend(lower.chars());
    chars.extend(std::iter::repeat_n('#', N - 1));
    if chars.len() < N {
        return std::collections::HashSet::new();
    }
    // The gram count is known (chars.len() - N + 1), so pre-size the set to avoid
    // rehashing while inserting. Grams are stored as a fixed `[char; N]` (N=3)
    // rather than an allocated `String`, so scoring many pairs doesn't
    // heap-allocate per trigram; set membership semantics are identical
    // (codepoint-wise equality).
    let mut set = std::collections::HashSet::with_capacity(chars.len() - N + 1);
    for i in 0..=(chars.len() - N) {
        set.insert([chars[i], chars[i + 1], chars[i + 2]]);
    }
    set
}

/// Character-trigram (q-gram) Jaccard similarity on two raw strings, the
/// reference for `goldenmatch.core.scorer._qgram_score_single` (n=3):
/// `|A ∩ B| / |A ∪ B|` over the padded q-gram sets. Identical strings (incl.
/// both empty) score 1.0; an empty union scores 0.0.
///
/// Unicode note: lowercasing uses Rust `str::to_lowercase` (Unicode default
/// case mapping), which matches Python `str.lower()` across ASCII and common
/// Latin. A handful of exotic codepoints can differ -- the same ASCII/Latin
/// scoped parity edge documented for the infermap scorers. q-gram is a
/// short-code scorer (SKUs / codes / names), so inputs are ASCII-dominant in
/// practice.
pub fn qgram_similarity(a: &str, b: &str) -> f64 {
    if a == b {
        return 1.0;
    }
    let sa = qgram_set(a);
    let sb = qgram_set(b);
    // One hash-lookup pass for the intersection, then |A ∪ B| = |A| + |B| - |A ∩ B|
    // arithmetically (avoids a second `union()` pass over the sets).
    let inter = sa.intersection(&sb).count();
    let union = sa.len() + sb.len() - inter;
    if union == 0 {
        return 0.0;
    }
    inter as f64 / union as f64
}

/// Scorer dispatch matching `score_buckets._resolve_score_pair_callable`'s
/// fast-path scale, all on [0, 1]. ids: 0=jaro_winkler, 1=levenshtein,
/// 2=token_sort, 3=exact, 4=date, 5=qgram.
///
/// NOTE: id=2 returns the UNSCALED `fuzz::ratio` ([0,1], NOT *100). This is
/// deliberate and must not be reconciled with `token_sort_ratio`'s *100 form:
/// `score_field_matrix` (native) depends on the unscaled value (it divides
/// token-sort by 100 only in the PyO3-exposed path, never here). Changing this
/// is a silent-drift trap.
pub fn score_one(scorer_id: u8, a: &str, b: &str) -> f64 {
    match scorer_id {
        0 => jaro_winkler::normalized_similarity(a.chars(), b.chars()),
        1 => levenshtein::normalized_similarity(a.chars(), b.chars()),
        2 => {
            let sa = token_sort_string(a);
            let sb = token_sort_string(b);
            fuzz::ratio(sa.chars(), sb.chars())
        }
        // id=3 = exact match. Guard arm collapses the if/else into the match
        // (clippy::collapsible-match under CI's stable toolchain); scorer_id==3
        // with a!=b falls through to the catch-all 0.0, same as every other id.
        3 if a == b => 1.0,
        4 => date_similarity(a, b),
        5 => qgram_similarity(a, b),
        _ => 0.0,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn jaro_winkler_identity_and_disjoint() {
        assert_eq!(jaro_winkler_similarity("abc", "abc"), 1.0);
        assert_eq!(jaro_winkler_similarity("abc", "xyz"), 0.0);
    }

    #[test]
    fn levenshtein_identity_and_disjoint() {
        assert_eq!(levenshtein_similarity("abc", "abc"), 1.0);
        let s = levenshtein_similarity("abc", "abx");
        assert!((s - (2.0 / 3.0)).abs() < 1e-9, "got {s}");
    }

    #[test]
    fn token_sort_is_order_invariant_on_0_100_scale() {
        assert_eq!(token_sort_ratio("a b", "b a"), 100.0);
    }

    #[test]
    fn date_similarity_separates_typo_from_unrelated() {
        // The #1858 cases: jaro_winkler scored the unrelated pair 0.80+; date must not.
        assert_eq!(date_similarity("1980-01-01", "1980-01-01"), 1.0); // same
        assert_eq!(date_similarity("1980-01-01", "1980-01-02"), 0.90); // 1-digit typo
        assert_eq!(date_similarity("1980-01-01", "1975-11-30"), 0.0); // unrelated (>=3 edits)
        // A single-digit typo must clear a typical 0.85 cutoff; unrelated must not.
        assert!(date_similarity("1980-01-01", "1980-01-02") >= 0.85);
        assert!(date_similarity("1980-01-01", "1975-01-01") < 0.85); // 2-edit year change
    }

    #[test]
    fn date_similarity_transposition_is_one_edit() {
        // Swapped adjacent digits = ONE Damerau edit (not two) -> stays a typo.
        assert_eq!(date_similarity("1980-12-01", "1980-21-01"), 0.90);
    }

    #[test]
    fn date_similarity_non_iso_falls_back_to_levenshtein() {
        // Not both ISO -> plain normalized edit distance, never jaro_winkler.
        assert_eq!(date_similarity("abc", "abc"), 1.0);
        let s = date_similarity("1980-01-01", "Jan 1 1980");
        assert!((s - levenshtein_similarity("1980-01-01", "Jan 1 1980")).abs() < 1e-12);
    }

    #[test]
    fn qgram_identity_and_jaccard() {
        // identical (incl. empty) -> 1.0
        assert_eq!(qgram_similarity("abc", "abc"), 1.0);
        assert_eq!(qgram_similarity("", ""), 1.0);
        // case-insensitive: same q-gram sets after lowercasing
        assert_eq!(qgram_similarity("ABC", "abc"), 1.0);
        // disjoint short strings share only the all-`#` padding gram is false here:
        // "ab" -> {##a,#ab,ab#} lower; "xy" -> {##x,#xy,xy#}; no overlap -> 0.0
        assert_eq!(qgram_similarity("ab", "xy"), 0.0);
        // one empty, one not: union non-empty, intersection empty -> 0.0
        assert_eq!(qgram_similarity("", "x"), 0.0);
        // partial overlap is strictly between 0 and 1
        let s = qgram_similarity("abcd", "abce");
        assert!(s > 0.0 && s < 1.0, "got {s}");
    }

    #[test]
    fn qgram_matches_hand_computed_jaccard() {
        // "abc" -> {##a,#ab,abc,bc#,c##}; "abd" -> {##a,#ab,abd,bd#,d##}
        // intersection {##a,#ab} = 2, union = 8 -> 0.25
        let s = qgram_similarity("abc", "abd");
        assert!((s - 0.25).abs() < 1e-12, "got {s}");
    }

    #[test]
    fn score_one_id5_is_qgram() {
        assert_eq!(score_one(5, "abc", "abc"), 1.0);
        assert_eq!(score_one(5, "abc", "abd"), qgram_similarity("abc", "abd"));
    }

    #[test]
    fn score_one_id4_is_date() {
        assert_eq!(score_one(4, "1980-01-01", "1980-01-01"), 1.0);
        assert_eq!(score_one(4, "1980-01-01", "1975-11-30"), 0.0);
    }

    #[test]
    fn score_one_dispatches_by_id() {
        // id=3 is exact match; score_one returns [0,1] (NOT the *100 token_sort_ratio scale)
        assert_eq!(score_one(3, "abc", "abc"), 1.0);
        assert_eq!(score_one(3, "abc", "abd"), 0.0);
    }

    #[test]
    fn score_one_id2_is_unscaled_not_100_scale() {
        // score_one(id=2) returns fuzz::ratio on [0,1], NOT token_sort_ratio's
        // *100 form. This asymmetry is load-bearing (the PyO3 score_field_matrix
        // path divides by 100, never here). Pinned so a silent unification breaks.
        assert_eq!(score_one(2, "a b", "b a"), 1.0);
        assert_eq!(token_sort_ratio("a b", "b a"), 100.0);
    }
}
