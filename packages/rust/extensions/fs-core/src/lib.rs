//! Canonical Fellegi-Sunter block-scoring math, pyo3-free.
//!
//! This crate is the single source of truth for the FS scoring *math* shared
//! across surfaces (the `native` Python extension today; an `fs-wasm` binding
//! next). Per-string similarity lives in `goldenmatch-score-core`; reference
//! data (census frequencies / name aliases) is injected by the host and never
//! bundled here. See `docs/superpowers/specs/2026-07-17-fs-core-cross-surface-extraction-design.md`.
//!
//! Increment 1 extracts the two pure leaf functions verbatim from
//! `native/src/score.rs` (`fs_normalize` + `fs_level_from_sim`). They are
//! byte-for-byte the same logic, relocated — `score.rs` now re-exports them, so
//! every call site (block scoring + the fused path) is unchanged and parity
//! holds by construction. The FS scoring loop itself moves in a later increment.

/// Normalize a summed Fellegi-Sunter match weight to a `[0, 1]` score.
///
/// `calibrated` = posterior probability `1 / (1 + 2^-(prior_w + w))` (with the
/// log-odds clamped to `[-60, 60]`); otherwise a linear min-max over the
/// observed weight range (`0.5` when the range is degenerate). This is the exact
/// contract the Python `score_probabilistic_vectorized` linear/posterior modes
/// use, so the native path is score-identical to the reference.
#[inline]
pub fn fs_normalize(
    total_weight: f64,
    calibrated: bool,
    prior_w: f64,
    min_weight: f64,
    weight_range: f64,
) -> f64 {
    if calibrated {
        let logodds = (prior_w + total_weight).clamp(-60.0, 60.0);
        1.0 / (1.0 + 2.0_f64.powf(-logodds))
    } else if weight_range > 0.0 {
        ((total_weight - min_weight) / weight_range).clamp(0.0, 1.0)
    } else {
        0.5
    }
}

/// Map a per-field similarity to a comparison level, matching
/// `core/probabilistic._levels_from_similarity`:
///   - custom `level_thresholds`: level = count of thresholds `t` with `sim >= t`
///     (inclusive, order-independent — `len(thresholds) + 1` levels total)
///   - 2 levels: `1` if `sim >= partial_threshold` else `0`
///   - 3 levels: `2` if `sim >= 0.95`, elif `sim >= partial_threshold` -> `1`, else `0`
///   - N levels: count of `k in 1..N` with `sim >= k/N` (even spacing)
#[inline]
pub fn fs_level_from_sim(
    sim: f64,
    n_levels: u8,
    partial_threshold: f64,
    level_thresholds: Option<&[f64]>,
) -> usize {
    if let Some(ts) = level_thresholds {
        return ts.iter().filter(|&&t| sim >= t).count();
    }
    match n_levels {
        2 => usize::from(sim >= partial_threshold),
        3 => {
            if sim >= 0.95 {
                2
            } else if sim >= partial_threshold {
                1
            } else {
                0
            }
        }
        n => {
            let n = n as usize;
            let mut lvl = 0usize;
            for k in 1..n {
                if sim >= (k as f64) / (n as f64) {
                    lvl += 1;
                }
            }
            lvl
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normalize_linear_min_max() {
        // Midpoint of the observed range -> 0.5.
        assert!((fs_normalize(1.0, false, 0.0, 0.0, 2.0) - 0.5).abs() < 1e-12);
        // Clamped into [0, 1].
        assert_eq!(fs_normalize(5.0, false, 0.0, 0.0, 2.0), 1.0);
        assert_eq!(fs_normalize(-5.0, false, 0.0, 0.0, 2.0), 0.0);
        // Degenerate range -> 0.5.
        assert_eq!(fs_normalize(3.0, false, 0.0, 0.0, 0.0), 0.5);
    }

    #[test]
    fn normalize_posterior_is_probability() {
        // prior_w + w = 0 -> 0.5; large positive -> ~1; large negative -> ~0.
        assert!((fs_normalize(0.0, true, 0.0, 0.0, 0.0) - 0.5).abs() < 1e-12);
        assert!(fs_normalize(100.0, true, 0.0, 0.0, 0.0) > 0.999);
        assert!(fs_normalize(-100.0, true, 0.0, 0.0, 0.0) < 0.001);
    }

    #[test]
    fn level_custom_thresholds_count_inclusive() {
        let ts = [0.8, 0.9, 0.95];
        for (sim, want) in [(1.0, 3usize), (0.93, 2), (0.85, 1), (0.5, 0)] {
            assert_eq!(fs_level_from_sim(sim, 4, 0.8, Some(&ts)), want);
        }
    }

    #[test]
    fn level_none_keeps_legacy_banding() {
        // 2-level.
        assert_eq!(fs_level_from_sim(0.9, 2, 0.8, None), 1);
        assert_eq!(fs_level_from_sim(0.7, 2, 0.8, None), 0);
        // 3-level.
        assert_eq!(fs_level_from_sim(1.0, 3, 0.8, None), 2);
        assert_eq!(fs_level_from_sim(0.9, 3, 0.8, None), 1);
        assert_eq!(fs_level_from_sim(0.5, 3, 0.8, None), 0);
        // N-level (even spacing).
        assert_eq!(fs_level_from_sim(0.85, 5, 0.8, None), 4);
        assert_eq!(fs_level_from_sim(0.4, 5, 0.8, None), 2);
    }
}
