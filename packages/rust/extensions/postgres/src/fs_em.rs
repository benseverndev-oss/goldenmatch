//! Native-direct Fellegi-Sunter training from COUNTED comparison vectors (0.19.0).
//!
//! Phase 1c of `docs/superpowers/specs/2026-08-13-fs-em-rust-single-source-design.md`.
//!
//! `goldenmatch_train_em(rows, matchkey, params)` trains through the
//! embedded-CPython bridge, because it samples pairs and bins them into
//! comparison vectors and neither of those is ported. This function starts one
//! step later — from vectors a SQL engine has already counted — and therefore
//! needs **no Python at all**: it calls the pyo3-free
//! `goldenmatch-score-core::em_core` kernel directly, the same shape as
//! `goldenmatch_hnsw_pairs` / `goldenmatch_lsh_pairs` / the goldencheck kernels.
//!
//! That is not a workaround, it is the point. Counting agreement patterns is
//! `SELECT gamma_a, gamma_b, count(*) ... GROUP BY gamma_a, gamma_b` — the one
//! thing a SQL engine is unambiguously the right place for — and the number of
//! distinct vectors is bounded by `prod(levels + 1)`, so what comes back is
//! thousands of rows however many pairs were compared. Postgres can now do both
//! halves of FS training without a Python interpreter in the backend.
//!
//! ## Flat arrays
//!
//! pgrx flattens multidimensional arrays, so every 2-D input arrives flat with a
//! shape argument — the `goldenmatch_hnsw_pairs(flat_vecs, dim)` idiom this
//! crate already uses for the same reason:
//!
//! * `patterns` is row-major `n_patterns x n_fields`; `-1` means unobserved.
//! * `u_probs` is RAGGED — field `j` contributes `n_levels[j]` entries — so it
//!   is flattened in field order and split back by `n_levels`. A caller that
//!   passes a rectangular block for fields with different level counts gets a
//!   length error rather than a silently misaligned model.
//!
//! Fail-soft to a `{"error": ...}` envelope, the convention the DuckDB surface
//! and `goldenmatch_certify_structural` already follow: a training call inside a
//! larger query should not abort the transaction.
use goldenmatch_score_core::em_core::{train_em_from_counts, EmField, EmParams};
use pgrx::prelude::*;

fn err(msg: String) -> String {
    serde_json::json!({ "error": msg }).to_string()
}

/// Split a ragged flat vector into one sub-vector per field.
fn unflatten(flat: &[f64], n_levels: &[usize]) -> Result<Vec<Vec<f64>>, String> {
    let want: usize = n_levels.iter().sum();
    if flat.len() != want {
        return Err(format!(
            "u_probs has {} entries but n_levels sums to {want}; it must be \
             flattened in field order, {} entries for field j",
            flat.len(),
            "n_levels[j]"
        ));
    }
    let mut out = Vec::with_capacity(n_levels.len());
    let mut at = 0usize;
    for &n in n_levels {
        out.push(flat[at..at + n].to_vec());
        at += n;
    }
    Ok(out)
}

/// Train a Fellegi-Sunter model from counted comparison vectors, with no
/// embedded CPython. Returns the model as JSON.
///
/// ```sql
/// SELECT goldenmatch.goldenmatch_train_em_from_counts(
///     ARRAY[2,2],                       -- n_levels, per field
///     ARRAY[1,1, 0,1, 1,0, 0,0],        -- patterns, row-major
///     ARRAY[500,300,150,50]::float8[],  -- counts, one per pattern
///     ARRAY[0.9,0.1, 0.85,0.15]::float8[], -- u, flattened by n_levels
///     ARRAY[false,false]                -- conditioned, per field
/// );
/// ```
#[pg_extern(immutable, parallel_safe)]
#[allow(clippy::too_many_arguments)]
fn goldenmatch_train_em_from_counts(
    n_levels: Vec<i32>,
    patterns: Vec<i32>,
    counts: Vec<f64>,
    u_probs: Vec<f64>,
    conditioned: Vec<bool>,
    max_iterations: default!(i32, 20),
    convergence: default!(f64, 0.001),
) -> String {
    match train_json(
        &n_levels,
        &patterns,
        &counts,
        &u_probs,
        &conditioned,
        max_iterations,
        convergence,
    ) {
        Ok(s) => s,
        Err(e) => err(e),
    }
}

#[allow(clippy::too_many_arguments)]
fn train_json(
    n_levels_i: &[i32],
    patterns_i: &[i32],
    counts: &[f64],
    u_flat: &[f64],
    conditioned: &[bool],
    max_iterations: i32,
    convergence: f64,
) -> Result<String, String> {
    if n_levels_i.is_empty() {
        return Err("n_levels is empty; no fields to train".into());
    }
    let mut n_levels = Vec::with_capacity(n_levels_i.len());
    for (j, &n) in n_levels_i.iter().enumerate() {
        if n < 2 {
            return Err(format!("n_levels[{j}] is {n}; a field needs >= 2 levels"));
        }
        n_levels.push(n as usize);
    }
    let nf = n_levels.len();
    if conditioned.len() != nf {
        return Err(format!(
            "conditioned has {} entries but there are {nf} fields",
            conditioned.len()
        ));
    }
    if !patterns_i.len().is_multiple_of(nf) {
        return Err(format!(
            "patterns has {} entries, not a multiple of the {nf} fields -- it \
             must be row-major n_patterns x n_fields",
            patterns_i.len()
        ));
    }
    let n_patterns = patterns_i.len() / nf;
    if n_patterns == 0 {
        return Err("no patterns; nothing to train on".into());
    }
    if counts.len() != n_patterns {
        return Err(format!(
            "{n_patterns} patterns but {} counts",
            counts.len()
        ));
    }

    let mut rows = Vec::with_capacity(n_patterns);
    for i in 0..n_patterns {
        let mut r = Vec::with_capacity(nf);
        for j in 0..nf {
            let lvl = patterns_i[i * nf + j];
            // Range-checked, not cast: an out-of-range level would otherwise
            // read the wrong weight or panic inside the kernel, and the caller
            // would get a number either way.
            if lvl < -1 || lvl as usize >= n_levels[j] {
                return Err(format!(
                    "pattern {i} field {j} has level {lvl}, outside -1..{}",
                    n_levels[j] - 1
                ));
            }
            r.push(lvl);
        }
        rows.push(r);
    }
    for (i, &c) in counts.iter().enumerate() {
        // NaN rejected explicitly: it fails every comparison, so it slips past a
        // bare `<= 0.0`, poisons every weighted sum, and a NaN convergence delta
        // compares false -- EM would run to the cap and return a model of NaNs.
        if c.is_nan() || c <= 0.0 {
            return Err(format!("pattern {i} has non-positive count {c}"));
        }
    }

    let u = unflatten(u_flat, &n_levels)?;
    let fields: Vec<EmField> = n_levels
        .iter()
        .map(|&n| EmField {
            n_levels: n,
            // Conditioning is expressed through the caller's per-field mask; see
            // the note in native/src/em.rs on why both signals are not set.
            is_blocking: false,
        })
        .collect();
    let params = EmParams {
        max_iterations: max_iterations.max(1) as usize,
        convergence,
    };

    let out = train_em_from_counts(&fields, &rows, counts, &u, conditioned, &params);
    Ok(serde_json::json!({
        "m_probs": out.m_probs,
        "u_probs": out.u_probs,
        "match_weights": out.match_weights,
        "converged": out.converged,
        "iterations": out.iterations,
        "proportion_matched": out.proportion_matched,
    })
    .to_string())
}

// NO `#[pg_test]` module here on purpose. `cargo pgrx test` cannot run for this
// crate (it needs pgrx SQL schema generation, which is broken in this
// workspace), so a `#[pg_test]` would compile and never execute -- untested code
// wearing a test's clothes. The assertions live in the `rust_pgrx` lane's psql
// smoke instead, where they run against a real `CREATE EXTENSION`, and they pin
// the same numbers as `score-core`'s parity fixture so this surface cannot drift
// from the wheel's.
