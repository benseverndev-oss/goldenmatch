//! Fellegi-Sunter training over COUNTED comparison vectors, exposed to Python.
//!
//! Phase 1 of `docs/superpowers/specs/2026-08-13-fs-em-rust-single-source-design.md`.
//! Phase 0 put the counted EM math in `goldenmatch-score-core::em_core`; this is
//! the first caller. Before it, `em_core` had none at all — the copy designated
//! as the source of truth was the only one nothing ran, while
//! `bridge::train_em` embedded CPython to reach the Python trainer.
//!
//! This module is deliberately thin: marshalling only, no arithmetic. Every
//! constant, every accumulation order and every calibration rule lives in
//! `em_core`, because a second place to get `1e-6` or the `-3..+3` ramp wrong is
//! exactly what this phase exists to remove. If you find yourself computing
//! something here, it belongs one crate down.
//!
//! ## Shapes
//!
//! `patterns` is `n_patterns x n_fields` of levels (`-1` = unobserved) and
//! `counts` is one weight per pattern. `conditioned` is per FIELD, not per row:
//! within one blocking pass the conditioning is constant, which is why the
//! counts can come from a `GROUP BY` with no pass column.
//!
//! Levels arrive as `i64` because that is what a Python list of ints marshals to
//! without a lossy narrowing step; they are range-checked into `i32` here rather
//! than cast, so a corrupt vector is an error and not a wrapped level that
//! silently reads the wrong weight.
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

use goldenmatch_score_core::em_core::{
    estimate_u_from_counts as core_estimate_u, train_em_from_counts as core_train, EmField,
    EmParams,
};

/// The numeric subset of the Python `EMResult`, as a plain tuple-friendly struct.
type EmTuple = (
    Vec<Vec<f64>>, // m_probs
    Vec<Vec<f64>>, // u_probs
    Vec<Vec<f64>>, // match_weights
    bool,          // converged
    usize,         // iterations
    f64,           // proportion_matched
);

fn build_fields(n_levels: &[usize]) -> Vec<EmField> {
    n_levels
        .iter()
        .map(|&n| EmField {
            n_levels: n,
            // `is_blocking` stays false: the counted path expresses conditioning
            // through the per-field `conditioned` mask the caller passes, which
            // is the per-PASS signal. Setting both would make it impossible to
            // tell which one produced a fixed field.
            is_blocking: false,
        })
        .collect()
}

fn check_shapes(
    n_levels: &[usize],
    patterns: &[Vec<i64>],
    counts: &[f64],
    conditioned: &[bool],
) -> PyResult<Vec<Vec<i32>>> {
    let nf = n_levels.len();
    if nf == 0 {
        return Err(PyValueError::new_err(
            "n_levels is empty; no fields to train",
        ));
    }
    if conditioned.len() != nf {
        return Err(PyValueError::new_err(format!(
            "conditioned has {} entries but there are {nf} fields",
            conditioned.len()
        )));
    }
    if patterns.len() != counts.len() {
        return Err(PyValueError::new_err(format!(
            "{} patterns but {} counts",
            patterns.len(),
            counts.len()
        )));
    }
    if patterns.is_empty() {
        return Err(PyValueError::new_err("no patterns; nothing to train on"));
    }

    let mut out = Vec::with_capacity(patterns.len());
    for (i, row) in patterns.iter().enumerate() {
        if row.len() != nf {
            return Err(PyValueError::new_err(format!(
                "pattern {i} has {} entries but there are {nf} fields -- the \
                 vectors must be ordered by the matchkey's fields",
                row.len()
            )));
        }
        let mut r = Vec::with_capacity(nf);
        for (j, &lvl) in row.iter().enumerate() {
            // Range-checked, not cast. A level outside its field's range would
            // otherwise index the wrong weight or panic deep in the loop, and
            // the caller would see neither.
            if lvl < -1 || lvl >= n_levels[j] as i64 {
                return Err(PyValueError::new_err(format!(
                    "pattern {i} field {j} has level {lvl}, outside -1..{}",
                    n_levels[j] - 1
                )));
            }
            r.push(lvl as i32);
        }
        out.push(r);
    }
    for (i, &c) in counts.iter().enumerate() {
        // NaN is rejected explicitly rather than via `!(c > 0.0)`: NaN fails
        // every comparison, so it would slip through a bare `c <= 0.0` and then
        // poison every weighted sum downstream into NaN -- which compares false
        // against the convergence threshold, so EM would silently run to the
        // iteration cap and return a model of NaNs.
        if c.is_nan() || c <= 0.0 {
            return Err(PyValueError::new_err(format!(
                "pattern {i} has non-positive count {c}"
            )));
        }
    }
    Ok(out)
}

/// Train FS from counted comparison vectors. Mirrors
/// `goldenmatch.core.probabilistic.train_em_from_counts`'s numeric half.
///
/// Returns `(m_probs, u_probs, match_weights, converged, iterations,
/// proportion_matched)` with each table ordered by field, matching the order the
/// caller passed `n_levels` in.
#[pyfunction]
#[pyo3(signature = (n_levels, patterns, counts, u_probs, conditioned,
                    max_iterations=20, convergence=0.001))]
#[allow(clippy::too_many_arguments)]
pub fn train_em_from_counts_native(
    py: Python<'_>,
    n_levels: Vec<usize>,
    patterns: Vec<Vec<i64>>,
    counts: Vec<f64>,
    u_probs: Vec<Vec<f64>>,
    conditioned: Vec<bool>,
    max_iterations: usize,
    convergence: f64,
) -> PyResult<EmTuple> {
    let levels = check_shapes(&n_levels, &patterns, &counts, &conditioned)?;
    if u_probs.len() != n_levels.len() {
        return Err(PyValueError::new_err(format!(
            "u_probs has {} vectors but there are {} fields",
            u_probs.len(),
            n_levels.len()
        )));
    }
    for (j, u) in u_probs.iter().enumerate() {
        if u.len() != n_levels[j] {
            return Err(PyValueError::new_err(format!(
                "u_probs[{j}] has {} levels but the field has {}",
                u.len(),
                n_levels[j]
            )));
        }
    }

    let fields = build_fields(&n_levels);
    let params = EmParams {
        max_iterations,
        convergence,
    };
    // The loop touches no Python objects, so the GIL is released for it. The
    // work is bounded by the distinct-vector count rather than the pair count,
    // so this is rarely long -- but holding the GIL over a pure-numeric loop is
    // the habit that makes a threaded caller serial for no reason.
    let out =
        py.detach(move || core_train(&fields, &levels, &counts, &u_probs, &conditioned, &params));

    Ok((
        out.m_probs,
        out.u_probs,
        out.match_weights,
        out.converged,
        out.iterations,
        out.proportion_matched,
    ))
}

/// `u` from counted comparison vectors over RANDOM pairs. Mirrors
/// `goldenmatch.core.probabilistic.estimate_u_from_counts`.
#[pyfunction]
pub fn estimate_u_from_counts_native(
    n_levels: Vec<usize>,
    patterns: Vec<Vec<i64>>,
    counts: Vec<f64>,
) -> PyResult<Vec<Vec<f64>>> {
    let conditioned = vec![false; n_levels.len()];
    let levels = check_shapes(&n_levels, &patterns, &counts, &conditioned)?;
    let fields = build_fields(&n_levels);
    Ok(core_estimate_u(&fields, &levels, &counts))
}
