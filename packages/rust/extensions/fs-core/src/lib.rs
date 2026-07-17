//! Canonical Fellegi-Sunter block-scoring math, pyo3-free.
//!
//! This crate is the single source of truth for the FS scoring *math* shared
//! across surfaces (the `native` Python extension today; an `fs-wasm` binding
//! next). Per-string similarity lives in `goldenmatch-score-core`; reference
//! data (census frequencies / name aliases) is injected by the host and never
//! bundled here. See `docs/superpowers/specs/2026-07-17-fs-core-cross-surface-extraction-design.md`.
//!
//! Increment 1 extracted the two pure leaf functions (`fs_normalize` +
//! `fs_level_from_sim`). Increment 2 adds [`score_fs_pair`] — the per-pair FS
//! scoring math (field loop → level → weight → negative evidence → normalize).
//! Both `score_block_pairs_fs` entry points in `native` (Vec + zero-copy Arrow)
//! now call it through a field accessor, so span iteration, rayon, GIL release,
//! and Arrow/Vec marshaling stay in `native` while the scoring *math* is single-
//! sourced here. Parity holds by construction (same computation, relocated).

use goldenmatch_score_core::score_one;

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

/// Per-matchkey constants for [`score_fs_pair`], borrowed once per scoring call
/// (built in `native` from the marshaled kwargs, reused across every pair).
///
/// `base_min` / `base_max` are the NE-aware weight-range endpoints MINUS the sum
/// of the regular fields' min/max weights, so [`score_fs_pair`] can add back only
/// the fields actually OBSERVED for a pair (nulls contribute nothing) — the exact
/// per-pair min-max the numpy reference computes.
pub struct FsPairParams<'a> {
    pub scorer_ids: &'a [u8],
    pub levels: &'a [u8],
    pub partial_thresholds: &'a [f64],
    pub field_thresholds: &'a [Option<&'a [f64]>],
    pub match_weights: &'a [Vec<f64>],
    pub field_mins: &'a [f64],
    pub field_maxs: &'a [f64],
    pub base_min: f64,
    pub base_max: f64,
    pub ne_scorer_ids: &'a [u8],
    pub ne_thresholds: &'a [f64],
    pub ne_weights: &'a [f64],
    pub calibrated: bool,
    pub prior_w: f64,
}

/// Score one within-block pair `(i, j)` and return its normalized `[0, 1]` score.
///
/// `get_field(field, row)` / `get_ne(ne, row)` yield the already-transform-applied
/// value (or `None` for a null), abstracting over `native`'s Vec-of-`String` and
/// zero-copy Arrow columns so both entry points share this one implementation.
/// The returned references are only read transiently (handed to `score_one`), so
/// their lifetime `'d` just has to outlive the call.
///
/// Mirrors `core/probabilistic.py` exactly: each observed field maps to a
/// comparison level whose EM match weight is summed; a negative-evidence field
/// fires (contributing its weight) iff BOTH values are present, non-empty, and
/// similarity is STRICTLY below the NE threshold; the summed weight is normalized
/// via [`fs_normalize`] over the pair's observed min-max range — except a pair
/// with no regular evidence and zero weight is a neutral `0.5` on the linear path.
#[inline]
#[allow(clippy::too_many_arguments)]
pub fn score_fs_pair<'d, F, G>(
    i: usize,
    j: usize,
    p: &FsPairParams<'_>,
    get_field: F,
    get_ne: G,
) -> f64
where
    F: Fn(usize, usize) -> Option<&'d str>,
    G: Fn(usize, usize) -> Option<&'d str>,
{
    let mut total_weight = 0.0_f64;
    let mut pair_min = p.base_min;
    let mut pair_max = p.base_max;
    let mut has_regular_evidence = false;
    for f in 0..p.scorer_ids.len() {
        if let (Some(a), Some(b)) = (get_field(f, i), get_field(f, j)) {
            has_regular_evidence = true;
            let sim = score_one(p.scorer_ids[f], a, b);
            let level = fs_level_from_sim(
                sim,
                p.levels[f],
                p.partial_thresholds[f],
                p.field_thresholds[f],
            );
            total_weight += p.match_weights[f][level];
            pair_min += p.field_mins[f];
            pair_max += p.field_maxs[f];
        }
    }
    // Negative evidence: exact `_ne_fired` semantics — fires iff both values are
    // present AND non-empty AND similarity is STRICTLY below the threshold.
    for k in 0..p.ne_scorer_ids.len() {
        if let (Some(a), Some(b)) = (get_ne(k, i), get_ne(k, j)) {
            if !a.is_empty()
                && !b.is_empty()
                && score_one(p.ne_scorer_ids[k], a, b) < p.ne_thresholds[k]
            {
                total_weight += p.ne_weights[k];
            }
        }
    }
    if !p.calibrated && !has_regular_evidence && total_weight == 0.0 {
        0.5
    } else {
        fs_normalize(
            total_weight,
            p.calibrated,
            p.prior_w,
            pair_min,
            pair_max - pair_min,
        )
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
    fn pair_agreement_beats_disagreement() {
        // Two exact-scorer fields (id 3), 2 levels, weights [disagree=-2, agree=+3].
        let mw = vec![vec![-2.0_f64, 3.0], vec![-2.0, 3.0]];
        let field_mins = [-2.0_f64, -2.0];
        let field_maxs = [3.0_f64, 3.0];
        let regular_min: f64 = field_mins.iter().sum();
        let regular_max: f64 = field_maxs.iter().sum();
        // No NE; min_weight/weight_range = regular range (base_* strip then re-add).
        let (min_weight, weight_range) = (regular_min, regular_max - regular_min);
        let p = FsPairParams {
            scorer_ids: &[3, 3],
            levels: &[2, 2],
            partial_thresholds: &[0.9, 0.9],
            field_thresholds: &[None, None],
            match_weights: &mw,
            field_mins: &field_mins,
            field_maxs: &field_maxs,
            base_min: min_weight - regular_min,
            base_max: min_weight + weight_range - regular_max,
            ne_scorer_ids: &[],
            ne_thresholds: &[],
            ne_weights: &[],
            calibrated: false,
            prior_w: 0.0,
        };
        let rows = [["alice", "smith"], ["alice", "jones"], ["bob", "brown"]];
        let get = |f: usize, r: usize| Some(rows[r][f]);
        let noop_ne = |_: usize, _: usize| None;
        // Row 0 vs 1: agree on field 0, disagree on field 1 -> mid score.
        let s01 = score_fs_pair(0, 1, &p, get, noop_ne);
        // Row 0 vs 2: disagree on both -> bottom of range (0.0).
        let s02 = score_fs_pair(0, 2, &p, get, noop_ne);
        // Row 0 vs 0: agree on both -> top of range (1.0).
        let s00 = score_fs_pair(0, 0, &p, get, noop_ne);
        assert!((s00 - 1.0).abs() < 1e-12, "full agreement = 1.0, got {s00}");
        assert!(
            (s02 - 0.0).abs() < 1e-12,
            "full disagreement = 0.0, got {s02}"
        );
        assert!(
            s02 < s01 && s01 < s00,
            "partial between: {s02} < {s01} < {s00}"
        );
    }

    #[test]
    fn pair_null_field_uses_observed_range_only() {
        // One field null on one side -> that field contributes no evidence and is
        // excluded from the pair's min-max range (pair_min/pair_max unchanged).
        let mw = vec![vec![-2.0_f64, 3.0], vec![-2.0, 3.0]];
        let field_mins = [-2.0_f64, -2.0];
        let field_maxs = [3.0_f64, 3.0];
        let regular_min: f64 = field_mins.iter().sum();
        let regular_max: f64 = field_maxs.iter().sum();
        let (min_weight, weight_range) = (regular_min, regular_max - regular_min);
        let p = FsPairParams {
            scorer_ids: &[3, 3],
            levels: &[2, 2],
            partial_thresholds: &[0.9, 0.9],
            field_thresholds: &[None, None],
            match_weights: &mw,
            field_mins: &field_mins,
            field_maxs: &field_maxs,
            base_min: min_weight - regular_min,
            base_max: min_weight + weight_range - regular_max,
            ne_scorer_ids: &[],
            ne_thresholds: &[],
            ne_weights: &[],
            calibrated: false,
            prior_w: 0.0,
        };
        // field 1 is null for row 1 -> only field 0 observed; agree -> 1.0 of the
        // single-field observed range.
        let get = |f: usize, r: usize| match (f, r) {
            (0, _) => Some("alice"),
            (1, 0) => Some("smith"),
            (1, 1) => None,
            _ => None,
        };
        let noop_ne = |_: usize, _: usize| None;
        let s = score_fs_pair(0, 1, &p, get, noop_ne);
        assert!(
            (s - 1.0).abs() < 1e-12,
            "observed-only agreement = 1.0, got {s}"
        );
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
