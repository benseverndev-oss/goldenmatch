//! wasm-bindgen wrapper over `goldenmatch-fs-core`. The TS analogue of the
//! `native` pyo3 crate: it wraps the SAME `fs_core::score_fs_pair`, so
//! Fellegi-Sunter block scoring is byte-identical across Python-native and
//! TS-WASM by construction (the cross-surface source of truth the 2026-07-17
//! fs-core design establishes).
//!
//! Split mirrors `score-wasm`: [`score_block_pairs_fs_impl`] is the pure,
//! host-testable scoring loop (linked via the `rlib` crate type, so `cargo test`
//! exercises it WITHOUT a wasm target), and the `#[wasm_bindgen]` shim below is a
//! thin JS<->WASM marshaling layer that crosses the boundary ONCE per block
//! (flat column-major arrays in, one JSON string out) per the perf-audit lesson.
//!
//! Scope note: like the native kernel, this scores against an ALREADY-trained
//! EMResult and ALREADY-transformed field values — EM training and transforms
//! stay host-side (TS `trainEM` / `buildComparisonVector`'s transform step),
//! exactly as they stay Python-side. NE / custom `level_thresholds` / a running
//! exclude set are supported by `_impl`; the initial `#[wasm_bindgen]` entry
//! covers the zero-config FS shape (no NE, no custom banding, no cross-batch
//! exclude — what `auto_configure_probabilistic_df` emits) and grows from there.

use std::collections::HashSet;

use goldenmatch_fs_core::{score_fs_pair, FsPairParams};

/// Score every within-block pair and return the ones at/above `threshold` as
/// `(a, b, score)` with `a < b`. Byte-identical to the native Vec entry point
/// `score_block_pairs_fs` (same `fs_core::score_fs_pair`), minus rayon — WASM is
/// single-threaded, so spans are walked sequentially in the same order, which
/// yields the same `(min, max)` pair sequence.
///
/// `field_values[field][row]` / `ne_values[ne][row]` are the already-transformed
/// values (`None` = null). `field_thresholds[field]` is the optional custom
/// level-threshold list for that field.
#[allow(clippy::too_many_arguments)]
pub fn score_block_pairs_fs_impl(
    row_ids: &[i64],
    block_sizes: &[usize],
    field_values: &[Vec<Option<String>>],
    scorer_ids: &[u8],
    levels: &[u8],
    partial_thresholds: &[f64],
    field_thresholds: &[Option<Vec<f64>>],
    match_weights: &[Vec<f64>],
    calibrated: bool,
    prior_w: f64,
    min_weight: f64,
    weight_range: f64,
    threshold: f64,
    ne_values: &[Vec<Option<String>>],
    ne_scorer_ids: &[u8],
    ne_thresholds: &[f64],
    ne_weights: &[f64],
    exclude: &HashSet<(i64, i64)>,
) -> Vec<(i64, i64, f64)> {
    // Same per-matchkey setup as native/src/score.rs: field weight extremes, then
    // the NE-aware base endpoints score_fs_pair adds observed fields back onto.
    let field_mins: Vec<f64> = match_weights
        .iter()
        .map(|w| w.iter().copied().fold(f64::INFINITY, f64::min))
        .collect();
    let field_maxs: Vec<f64> = match_weights
        .iter()
        .map(|w| w.iter().copied().fold(f64::NEG_INFINITY, f64::max))
        .collect();
    let regular_min: f64 = field_mins.iter().sum();
    let regular_max: f64 = field_maxs.iter().sum();
    let field_thresholds_slices: Vec<Option<&[f64]>> =
        field_thresholds.iter().map(|o| o.as_deref()).collect();

    let params = FsPairParams {
        scorer_ids,
        levels,
        partial_thresholds,
        field_thresholds: &field_thresholds_slices,
        match_weights,
        field_mins: &field_mins,
        field_maxs: &field_maxs,
        base_min: min_weight - regular_min,
        base_max: min_weight + weight_range - regular_max,
        ne_scorer_ids,
        ne_thresholds,
        ne_weights,
        calibrated,
        prior_w,
    };

    let mut out: Vec<(i64, i64, f64)> = Vec::new();
    let mut offset = 0usize;
    for &size in block_sizes {
        if size >= 2 {
            let end = offset + size;
            for i in offset..end - 1 {
                let ri = row_ids[i];
                for j in (i + 1)..end {
                    let rj = row_ids[j];
                    let pair_key = if ri < rj { (ri, rj) } else { (rj, ri) };
                    if exclude.contains(&pair_key) {
                        continue;
                    }
                    let normalized = score_fs_pair(
                        i,
                        j,
                        &params,
                        |f, row| field_values[f][row].as_deref(),
                        |k, row| ne_values[k][row].as_deref(),
                    );
                    if normalized >= threshold {
                        out.push((pair_key.0, pair_key.1, normalized));
                    }
                }
            }
        }
        offset += size;
    }
    out
}

/// Reshape a column-major flat value buffer (`field 0` all rows, then `field 1`
/// …) + a parallel null-flag buffer into `[field][row]` `Option<String>`.
// Consumed by the wasm shim (and the roundtrip test); the plain non-wasm lib
// compile can't see the `cfg(target_arch="wasm32")` use, hence the allow.
#[allow(dead_code)]
fn reshape_columns(
    flat: Vec<String>,
    nulls: &[u8],
    n_fields: usize,
    n_rows: usize,
) -> Vec<Vec<Option<String>>> {
    let mut cols: Vec<Vec<Option<String>>> = Vec::with_capacity(n_fields);
    let mut it = flat.into_iter();
    for f in 0..n_fields {
        let mut col: Vec<Option<String>> = Vec::with_capacity(n_rows);
        for r in 0..n_rows {
            let v = it.next().unwrap_or_default();
            col.push(if nulls.get(f * n_rows + r).copied().unwrap_or(0) == 1 {
                None
            } else {
                Some(v)
            });
        }
        cols.push(col);
    }
    cols
}

/// Serialize `(a, b, score)` triples as a compact JSON array `[[a,b,s],…]`.
/// i64 ids stay exact in JSON (no f64 round-trip), matching the goldengraph-wasm
/// `*_json` boundary idiom used elsewhere in the repo.
#[allow(dead_code)]
fn pairs_to_json(pairs: &[(i64, i64, f64)]) -> String {
    let mut s = String::from("[");
    for (idx, (a, b, sc)) in pairs.iter().enumerate() {
        if idx > 0 {
            s.push(',');
        }
        s.push_str(&format!("[{a},{b},{sc}]"));
    }
    s.push(']');
    s
}

#[cfg(target_arch = "wasm32")]
mod wasm {
    use super::*;
    use wasm_bindgen::prelude::*;

    /// Zero-config FS block scoring (no NE, no custom banding, no cross-batch
    /// exclude — the `auto_configure_probabilistic_df` shape). `field_values_flat`
    /// / `field_nulls` are column-major (`field 0` rows, then `field 1` …).
    /// Returns a JSON array of `[a, b, score]` triples.
    #[allow(clippy::too_many_arguments)]
    #[wasm_bindgen]
    pub fn score_block_pairs_fs(
        row_ids: Vec<i64>,
        block_sizes: Vec<usize>,
        field_values_flat: Vec<String>,
        field_nulls: Vec<u8>,
        n_fields: usize,
        scorer_ids: Vec<u8>,
        levels: Vec<u8>,
        partial_thresholds: Vec<f64>,
        match_weights_flat: Vec<f64>,
        match_weights_lens: Vec<usize>,
        calibrated: bool,
        prior_w: f64,
        min_weight: f64,
        weight_range: f64,
        threshold: f64,
    ) -> String {
        let n_rows = row_ids.len();
        let field_values = reshape_columns(field_values_flat, &field_nulls, n_fields, n_rows);
        // Ragged per-field weight rows arrive flat + a lengths vector.
        let mut match_weights: Vec<Vec<f64>> = Vec::with_capacity(match_weights_lens.len());
        let mut wi = 0usize;
        for &len in &match_weights_lens {
            match_weights.push(match_weights_flat[wi..wi + len].to_vec());
            wi += len;
        }
        let field_thresholds: Vec<Option<Vec<f64>>> = vec![None; n_fields];
        let empty_ne: Vec<Vec<Option<String>>> = Vec::new();
        let exclude: HashSet<(i64, i64)> = HashSet::new();
        let pairs = score_block_pairs_fs_impl(
            &row_ids,
            &block_sizes,
            &field_values,
            &scorer_ids,
            &levels,
            &partial_thresholds,
            &field_thresholds,
            &match_weights,
            calibrated,
            prior_w,
            min_weight,
            weight_range,
            threshold,
            &empty_ne,
            &[],
            &[],
            &[],
            &exclude,
        );
        pairs_to_json(&pairs)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // Two exact-scorer fields, 2 levels, weights [disagree=-2, agree=+3]; one
    // block of 3 rows. Byte-identical to what the native Vec entry would emit.
    fn params() -> (Vec<u8>, Vec<u8>, Vec<f64>, Vec<Vec<f64>>) {
        let scorer_ids = vec![3u8, 3];
        let levels = vec![2u8, 2];
        let partials = vec![0.9_f64, 0.9];
        let mw = vec![vec![-2.0_f64, 3.0], vec![-2.0, 3.0]];
        (scorer_ids, levels, partials, mw)
    }

    #[test]
    fn one_block_emits_expected_pairs() {
        let (scorer_ids, levels, partials, mw) = params();
        // rows: 0=(alice,smith) 1=(alice,jones) 2=(alice,smith) -> ids 10,20,30.
        let fields = vec![
            vec![
                Some("alice".into()),
                Some("alice".into()),
                Some("alice".into()),
            ],
            vec![
                Some("smith".into()),
                Some("jones".into()),
                Some("smith".into()),
            ],
        ];
        let regular_min = -4.0; // -2 + -2
        let regular_max = 6.0; // 3 + 3
        let pairs = score_block_pairs_fs_impl(
            &[10, 20, 30],
            &[3],
            &fields,
            &scorer_ids,
            &levels,
            &partials,
            &[None, None],
            &mw,
            false,
            0.0,
            regular_min,
            regular_max - regular_min,
            0.99, // only full agreement (score 1.0) clears this
            &[],
            &[],
            &[],
            &[],
            &HashSet::new(),
        );
        // Only (10,30) agree on both fields -> score 1.0 >= 0.99. (10,20)/(20,30)
        // agree on one field -> mid score, below 0.99.
        assert_eq!(pairs.len(), 1);
        assert_eq!((pairs[0].0, pairs[0].1), (10, 30));
        assert!((pairs[0].2 - 1.0).abs() < 1e-12);
    }

    #[test]
    fn exclude_suppresses_a_pair() {
        let (scorer_ids, levels, partials, mw) = params();
        let fields = vec![
            vec![Some("alice".into()), Some("alice".into())],
            vec![Some("smith".into()), Some("smith".into())],
        ];
        let mut ex = HashSet::new();
        ex.insert((10i64, 20i64));
        let pairs = score_block_pairs_fs_impl(
            &[10, 20],
            &[2],
            &fields,
            &scorer_ids,
            &levels,
            &partials,
            &[None, None],
            &mw,
            false,
            0.0,
            -4.0,
            10.0,
            0.5,
            &[],
            &[],
            &[],
            &[],
            &ex,
        );
        assert!(pairs.is_empty(), "excluded pair must not be emitted");
    }

    #[test]
    fn reshape_and_json_roundtrip() {
        let cols = reshape_columns(
            vec!["a".into(), "b".into(), "c".into(), "d".into()],
            &[0, 1, 0, 0],
            2,
            2,
        );
        assert_eq!(cols[0], vec![Some("a".to_string()), None]); // field 0, row 1 null
        assert_eq!(cols[1], vec![Some("c".to_string()), Some("d".to_string())]);
        assert_eq!(
            pairs_to_json(&[(10, 30, 1.0), (1, 2, 0.5)]),
            "[[10,30,1],[1,2,0.5]]"
        );
    }
}
