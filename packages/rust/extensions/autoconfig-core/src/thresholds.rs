//! Adaptive decision thresholds (spec 2026-06-22-autoconfig-smarter-faster-s1-s3).
//! Pure, deterministic kernels that replace fixed magic numbers with data-shape-
//! aware ones, shared across every surface (Python / native / wasm / TS).

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
}
