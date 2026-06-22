//! Adaptive decision thresholds (spec 2026-06-22-autoconfig-smarter-faster-s1-s3).
//! Pure, deterministic kernels that replace fixed magic numbers with data-shape-
//! aware ones, shared across every surface (Python / native / wasm / TS).

/// S3: per-type exact-matchkey cardinality floor (closes the standing TODO at
/// `autoconfig.py:875`, issue #715).
///
/// An exact matchkey asserts identity equivalence, so the backing column must be
/// plausibly unique. The blanket 0.5 floor is crude for one type in particular:
/// **phones are legitimately shared** across household/business lines, so a
/// moderately-shared phone is still a useful candidate-generation signal (the
/// scorer weights it; the floor only guards against mega-clusters from very low
/// cardinality). Phones get a more permissive `0.30`.
///
/// **email stays at the default 0.50** — a shared email (e.g. a 0.5-cardinality
/// household/account email) is a genuine identity signal this codebase keeps as
/// an exact matchkey; the existing matchkey-guard tests pin email's floor to 0.50
/// (0.5 included, 0.4999 excluded). The spec's initial email=0.70 demoted those
/// legitimate shared emails and was corrected here.
///
/// Anything outside the tuned set (incl. unknown / drifted vocab) gets the
/// historical default 0.5. Matches on the `col_type` string (the serde name of
/// the core `ColType`) so it is robust to vocabulary drift. zip/geo are skipped
/// entirely upstream (a separate guard) and never reach this floor.
pub fn exact_matchkey_floor(col_type: &str) -> f64 {
    match col_type {
        "phone" => 0.30,
        // email / name / string / and every other type keep the 0.5 default.
        _ => 0.50,
    }
}

/// S2b: adaptive sparse-match floor.
///
/// The sparse-match indicator flags a sample as "sparse" when its exact-matchkey
/// collision count is below a floor. A fixed floor of 50 is row-count- and
/// matchkey-independent: for a dataset that can only ever produce a few hundred
/// candidate pairs, demanding 50 sample collisions over-triggers sparse-match
/// expansion. This scales the floor down for small-yield datasets while keeping
/// it at 50 for anything expected to produce >= 5000 pairs.
///
/// `estimated_pairs` is the (S1-corrected) candidate pair count.
/// Returns `min(50, estimated_pairs / 100)` (integer floor division).
pub fn sparse_match_floor(estimated_pairs: u64) -> u64 {
    (estimated_pairs / 100).min(50)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn floor_caps_at_50_for_high_yield() {
        assert_eq!(sparse_match_floor(5_000), 50);
        assert_eq!(sparse_match_floor(1_000_000), 50);
        assert_eq!(sparse_match_floor(5_001), 50);
    }

    #[test]
    fn floor_scales_down_for_low_yield() {
        assert_eq!(sparse_match_floor(0), 0);
        assert_eq!(sparse_match_floor(100), 1);
        assert_eq!(sparse_match_floor(1_000), 10);
        assert_eq!(sparse_match_floor(4_900), 49);
    }

    #[test]
    fn floor_boundary_exactly_5000() {
        // 5000/100 = 50 == cap; still 50
        assert_eq!(sparse_match_floor(5_000), 50);
        // 4999/100 = 49 (integer floor), below the cap
        assert_eq!(sparse_match_floor(4_999), 49);
    }

    #[test]
    fn exact_matchkey_floor_per_type() {
        assert_eq!(exact_matchkey_floor("phone"), 0.30); // permissive: shared phones
        assert_eq!(exact_matchkey_floor("email"), 0.50); // default: shared emails kept
        assert_eq!(exact_matchkey_floor("name"), 0.50);
        assert_eq!(exact_matchkey_floor("string"), 0.50);
    }

    #[test]
    fn exact_matchkey_floor_unknown_defaults_to_half() {
        assert_eq!(exact_matchkey_floor("identifier"), 0.50);
        assert_eq!(exact_matchkey_floor("multi_name"), 0.50);
        assert_eq!(exact_matchkey_floor("totally_unknown_type"), 0.50);
        assert_eq!(exact_matchkey_floor(""), 0.50);
    }
}
