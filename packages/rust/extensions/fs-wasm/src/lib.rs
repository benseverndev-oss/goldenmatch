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
// `i`/`j` index row_ids AND are the positional args to score_fs_pair's field
// accessor, so the span walk is genuinely index-based (not an iterator map).
#[allow(clippy::too_many_arguments, clippy::needless_range_loop)]
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
        // Reference-data name scorers (census / alias tables) are a later TS
        // increment — the TS caller would inject them via wasm-bindgen, like the
        // native pyo3 side. None here degrades those fields to plain JW.
        surname_freq: None,
        name_aliases: None,
        tf_tables: &[],
        // Embedding scorers marshal precomputed vectors from the host — a later
        // TS increment (the native pyo3 side lands first). None here degrades an
        // id-7 field to "fully disagree", but the wasm entry never emits id 7.
        emb_vectors: &[],
        emb_dims: &[],
        // The wasm/TS surface keeps the legacy emit-at-neutral behavior for now
        // (its parity fixtures are byte-locked); the net-zero filter is opt-in on
        // the Python engine first. See the fs-net-zero-evidence-filter spec.
        require_positive_evidence: false,
        missing_disagree: false,
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

// ── Fellegi-Sunter training from COUNTED comparison vectors ──────────
//
// Phase 1b of `docs/superpowers/specs/2026-08-13-fs-em-rust-single-source-design.md`.
// TypeScript maintains its own `trainEM` (a hand-port of the Python trainer),
// which is the third copy of a loop that should have one. This exposes the
// shared kernel to TS.
//
// It exposes the COUNTED shape only, not the row-shaped `train_em_core`, and
// that is deliberate: TS's `trainEM` supports negative-evidence dimensions and
// `em_core` explicitly does not ("NOT yet ported: negative-evidence dims..."),
// so routing `trainEM` through the kernel today would silently drop NE and
// return a model the config did not ask for. The counted path has no such gap --
// it refuses NE everywhere, on every surface.
//
// So this ships the kernel + parity as an ALTERNATIVE surface first, exactly how
// fs-wasm's own scoring kernel shipped before the TS scorer was rerouted to it.
// The row-shaped duplicate stays until `em_core` covers NE.

/// `(m_probs, u_probs, match_weights, converged, iterations, proportion_matched)`
/// as JSON. Split from the `#[wasm_bindgen]` shim so `cargo test` exercises it
/// on a NON-wasm target -- the same discipline `score_block_pairs_fs_impl`
/// follows, and the reason this crate's tests run in the ordinary `rust` lane.
///
/// `patterns_flat` is row-major `n_patterns x n_fields` (`-1` = unobserved),
/// `u_flat` is RAGGED and split by `n_levels`, and `conditioned` is `0`/`1` per
/// FIELD (wasm-bindgen has no `Vec<bool>`; the crate already uses `u8` masks for
/// `field_nulls`).
#[allow(clippy::too_many_arguments)]
pub fn train_em_from_counts_impl(
    n_levels: &[u32],
    patterns_flat: &[i32],
    counts: &[f64],
    u_flat: &[f64],
    conditioned: &[u8],
    max_iterations: u32,
    convergence: f64,
) -> Result<String, String> {
    use goldenmatch_score_core::em_core::{train_em_from_counts, EmField, EmParams};

    if n_levels.is_empty() {
        return Err("n_levels is empty; no fields to train".into());
    }
    let nf = n_levels.len();
    let lv: Vec<usize> = n_levels.iter().map(|&n| n as usize).collect();
    for (j, &n) in lv.iter().enumerate() {
        if n < 2 {
            return Err(format!("n_levels[{j}] is {n}; a field needs >= 2 levels"));
        }
    }
    if conditioned.len() != nf {
        return Err(format!(
            "conditioned has {} entries but there are {nf} fields",
            conditioned.len()
        ));
    }
    if !patterns_flat.len().is_multiple_of(nf) {
        return Err(format!(
            "patterns has {} entries, not a multiple of the {nf} fields",
            patterns_flat.len()
        ));
    }
    let n_patterns = patterns_flat.len() / nf;
    if n_patterns == 0 {
        return Err("no patterns; nothing to train on".into());
    }
    if counts.len() != n_patterns {
        return Err(format!("{n_patterns} patterns but {} counts", counts.len()));
    }
    let want: usize = lv.iter().sum();
    if u_flat.len() != want {
        return Err(format!(
            "u_probs has {} entries but n_levels sums to {want}",
            u_flat.len()
        ));
    }

    let mut rows = Vec::with_capacity(n_patterns);
    for i in 0..n_patterns {
        let mut r = Vec::with_capacity(nf);
        for j in 0..nf {
            let x = patterns_flat[i * nf + j];
            // Range-checked, not cast: an out-of-range level reads the wrong
            // weight rather than failing, and the caller sees a number either way.
            if x < -1 || x as usize >= lv[j] {
                return Err(format!(
                    "pattern {i} field {j} has level {x}, outside -1..{}",
                    lv[j] - 1
                ));
            }
            r.push(x);
        }
        rows.push(r);
    }
    for (i, &c) in counts.iter().enumerate() {
        // NaN fails every comparison, so it slips past a bare `<= 0.0`, poisons
        // every weighted sum, and a NaN convergence delta compares false -- EM
        // would run to the cap and return a model of NaNs.
        if c.is_nan() || c <= 0.0 {
            return Err(format!("pattern {i} has non-positive count {c}"));
        }
    }

    let mut u = Vec::with_capacity(nf);
    let mut at = 0usize;
    for &n in &lv {
        u.push(u_flat[at..at + n].to_vec());
        at += n;
    }
    let fields: Vec<EmField> = lv
        .iter()
        .map(|&n| EmField {
            n_levels: n,
            is_blocking: false,
        })
        .collect();
    let cond: Vec<bool> = conditioned.iter().map(|&b| b != 0).collect();
    let params = EmParams {
        max_iterations: (max_iterations.max(1)) as usize,
        convergence,
    };

    let out = train_em_from_counts(&fields, &rows, counts, &u, &cond, &params);
    Ok(em_output_to_json(&out))
}

/// Hand-rolled JSON, because this crate carries no serde and an edge bundle is
/// not the place to add one for six fields.
fn em_output_to_json(out: &goldenmatch_score_core::em_core::EmOutput) -> String {
    fn table(t: &[Vec<f64>]) -> String {
        let rows: Vec<String> = t
            .iter()
            .map(|r| {
                let xs: Vec<String> = r.iter().map(|v| fmt_f64(*v)).collect();
                format!("[{}]", xs.join(","))
            })
            .collect();
        format!("[{}]", rows.join(","))
    }
    format!(
        "{{\"m_probs\":{},\"u_probs\":{},\"match_weights\":{},\"converged\":{},\"iterations\":{},\"proportion_matched\":{}}}",
        table(&out.m_probs),
        table(&out.u_probs),
        table(&out.match_weights),
        out.converged,
        out.iterations,
        fmt_f64(out.proportion_matched),
    )
}

/// `{:?}` on f64 round-trips exactly and never emits `1e0`-style exponents that
/// `JSON.parse` would still accept but a reader would not expect. Non-finite
/// values cannot occur (every input is validated above), but they would be
/// invalid JSON, so they are pinned to `null` rather than emitting `NaN`.
fn fmt_f64(v: f64) -> String {
    if v.is_finite() {
        format!("{v:?}")
    } else {
        "null".to_string()
    }
}

#[cfg(target_arch = "wasm32")]
mod wasm {
    use super::*;
    use wasm_bindgen::prelude::*;

    /// FS block scoring. `field_values_flat` / `field_nulls` are column-major
    /// (`field 0` rows, then `field 1` …). Returns a JSON array of `[a, b, score]`
    /// triples.
    ///
    /// The trailing arguments carry the capabilities `score_block_pairs_fs_impl`
    /// (and `fs_core::score_fs_pair`) already support but the zero-config shape
    /// does not exercise. They are ADDITIVE — passing the empty/None defaults
    /// (empty `level_thresholds_lens`, `n_ne = 0`, empty `ne_*`) reproduces the
    /// prior byte-identical zero-config output:
    ///
    /// * Custom level-banding (`fs_core` `field_thresholds`) arrives ragged as a
    ///   flat `f64` buffer + a per-field length vector (the same flat+lens idiom
    ///   as `match_weights`), with a `-1` length sentinel meaning "no custom
    ///   thresholds for this field" (→ `None`). An EMPTY `level_thresholds_lens`
    ///   means every field is `None` (the old hardcoded behavior).
    /// * Negative evidence mirrors the native pyo3 `score_block_pairs_fs`
    ///   shape: `ne_values_flat`/`ne_nulls` are column-major over `n_ne` NE
    ///   fields × `n_rows` (reshaped exactly like the regular field values), plus
    ///   parallel `ne_scorer_ids`/`ne_thresholds`/`ne_weights` (length `n_ne`).
    ///   `n_ne = 0` = no negative evidence.
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
        // --- ADDITIVE: custom level-banding (ragged flat+lens, -1 = None) ---
        level_thresholds_flat: Vec<f64>,
        level_thresholds_lens: Vec<i32>,
        // --- ADDITIVE: negative evidence (column-major over n_ne fields) ---
        ne_values_flat: Vec<String>,
        ne_nulls: Vec<u8>,
        n_ne: usize,
        ne_scorer_ids: Vec<u8>,
        ne_thresholds: Vec<f64>,
        ne_weights: Vec<f64>,
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
        // Custom level-banding: empty lens => all None (zero-config default),
        // else per-field ragged decode with -1 = None (mirrors the native pyo3
        // `level_thresholds: Option<Vec<Option<Vec<f64>>>>` shape).
        let field_thresholds: Vec<Option<Vec<f64>>> = if level_thresholds_lens.is_empty() {
            vec![None; n_fields]
        } else {
            let mut out: Vec<Option<Vec<f64>>> = Vec::with_capacity(level_thresholds_lens.len());
            let mut ti = 0usize;
            for &len in &level_thresholds_lens {
                if len < 0 {
                    out.push(None);
                } else {
                    let l = len as usize;
                    out.push(Some(level_thresholds_flat[ti..ti + l].to_vec()));
                    ti += l;
                }
            }
            out
        };
        // Negative evidence: reshape column-major values exactly like the
        // regular field values (n_ne fields × n_rows). n_ne == 0 => empty NE.
        let ne_values = reshape_columns(ne_values_flat, &ne_nulls, n_ne, n_rows);
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
            &ne_values,
            &ne_scorer_ids,
            &ne_thresholds,
            &ne_weights,
            &exclude,
        );
        pairs_to_json(&pairs)
    }

    /// Fellegi-Sunter training from counted comparison vectors. Returns the
    /// model as JSON, or a `{"error": ...}` envelope -- fail-soft, matching the
    /// convention every other GoldenMatch surface uses for this kernel.
    #[allow(clippy::too_many_arguments)]
    #[wasm_bindgen]
    pub fn train_em_from_counts(
        n_levels: Vec<u32>,
        patterns_flat: Vec<i32>,
        counts: Vec<f64>,
        u_flat: Vec<f64>,
        conditioned: Vec<u8>,
        max_iterations: u32,
        convergence: f64,
    ) -> String {
        match train_em_from_counts_impl(
            &n_levels,
            &patterns_flat,
            &counts,
            &u_flat,
            &conditioned,
            max_iterations,
            convergence,
        ) {
            Ok(s) => s,
            Err(e) => format!("{{\"error\":{:?}}}", e),
        }
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

#[cfg(test)]
mod em_tests {
    use super::*;

    /// Pinned to `score-core/tests/fixtures/em_counts_parity.json`, case
    /// `two_level_learnable_only`. Shared anchors are the point: the wasm
    /// surface and the wheel must not be able to drift apart, and they only
    /// cannot if both are checked against the same numbers.
    #[test]
    fn counted_training_matches_the_shared_anchors() {
        let json = train_em_from_counts_impl(
            &[2, 2],
            &[1, 1, 0, 1, 1, 0, 0, 0],
            &[500.0, 300.0, 150.0, 50.0],
            &[0.9, 0.1, 0.85, 0.15],
            &[0, 0],
            20,
            0.001,
        )
        .expect("training failed");
        // Weights from the fixture: field 0 agree = 2.706681, field 1
        // disagree = -2.111677. Substring-free parse would need serde; this
        // crate carries none, so the check is on the emitted text.
        assert!(json.contains("\"match_weights\""), "{json}");
        let w = json
            .split("\"match_weights\":")
            .nth(1)
            .unwrap()
            .split("],\"converged\"")
            .next()
            .unwrap();
        assert!(
            w.starts_with("[[-1.374233"),
            "field 0 disagree drifted: {w}"
        );
        assert!(w.contains("2.70668102"), "field 0 agree drifted: {w}");
        // Field 1 too: asserting only field 0 would pass with the second
        // field's weights dropped or duplicated from the first.
        assert!(w.contains("-2.11167708"), "field 1 disagree drifted: {w}");
        assert!(w.contains("2.42102808"), "field 1 agree drifted: {w}");
    }

    /// A conditioned field takes the bounded ramp and neutral u (#1835) -- the
    /// calibration rule that fails silently, since every wrong variant still
    /// returns a valid probability vector.
    #[test]
    fn a_conditioned_field_takes_the_bounded_ramp() {
        let json = train_em_from_counts_impl(
            &[2, 2],
            &[1, 1, 0, 1],
            &[500.0, 300.0],
            &[0.9, 0.1, 0.999, 0.001],
            &[0, 1],
            20,
            0.001,
        )
        .unwrap();
        assert!(json.contains("[-3.0,3.0]"), "no bounded ramp in {json}");
        assert!(
            json.contains("[0.5,0.5]"),
            "u was not neutralised in {json}"
        );
    }

    #[test]
    fn an_out_of_range_level_is_refused() {
        let e = train_em_from_counts_impl(
            &[2, 2],
            &[1, 7],
            &[10.0],
            &[0.9, 0.1, 0.9, 0.1],
            &[0, 0],
            20,
            0.001,
        )
        .unwrap_err();
        assert!(e.contains("outside"), "{e}");
    }

    #[test]
    fn a_non_positive_count_is_refused() {
        let e = train_em_from_counts_impl(
            &[2, 2],
            &[1, 1],
            &[0.0],
            &[0.9, 0.1, 0.9, 0.1],
            &[0, 0],
            20,
            0.001,
        )
        .unwrap_err();
        assert!(e.contains("non-positive"), "{e}");
    }

    /// Ragged u is split by n_levels; a mis-sized block is refused rather than
    /// silently misaligning every field after the first.
    #[test]
    fn a_mis_sized_u_is_refused() {
        let e = train_em_from_counts_impl(
            &[2, 3],
            &[1, 2],
            &[10.0],
            &[0.9, 0.1, 0.8, 0.2],
            &[0, 0],
            20,
            0.001,
        )
        .unwrap_err();
        assert!(e.contains("u_probs"), "{e}");
    }
}
