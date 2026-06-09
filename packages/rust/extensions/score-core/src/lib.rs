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
use rapidfuzz::distance::{jaro_winkler, levenshtein};
use rapidfuzz::fuzz;

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

/// Scorer dispatch matching `score_buckets._resolve_score_pair_callable`'s
/// fast-path scale, all on [0, 1]. ids: 0=jaro_winkler, 1=levenshtein,
/// 2=token_sort, 3=exact.
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
        3 => {
            if a == b {
                1.0
            } else {
                0.0
            }
        }
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
