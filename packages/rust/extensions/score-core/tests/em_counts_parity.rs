//! The Rust FS EM core reproduces the Python reference, case for case.
//!
//! Phase 0 of `docs/superpowers/specs/2026-08-13-fs-em-rust-single-source-design.md`.
//! The Fellegi-Sunter EM loop exists three times in this repo (Python, TypeScript,
//! and here) and this crate's copy is the one designated as the source of truth.
//! Until it is wired (Phase 1) it is also the one nothing calls, so without a gate
//! it drifts from the behaviour it is supposed to define and nobody finds out.
//!
//! The anchors come from `scripts/gen_fs_em_parity_fixture.py`, which is
//! COMMITTED. The older anchors inlined in `em_core.rs` came from an uncommitted
//! scratch script: when one of those goes red there is no way to tell a Python
//! behaviour change from a bad port, because the numbers cannot be re-derived.
//! Here, re-running the emitter and reading `git diff` is the whole diagnosis.
//!
//! Parity is decision-level, not bitwise -- libm's `ln`/`log2`/`exp` differ from
//! CPython's in the low mantissa bits. Tolerances travel IN the fixture so the
//! two sides cannot disagree about what "parity" means.

use goldenmatch_score_core::em_core::{
    estimate_u_from_counts, train_em_from_counts, EmField, EmParams,
};
use serde_json::Value;

fn fixture() -> Value {
    let raw = include_str!("fixtures/em_counts_parity.json");
    serde_json::from_str(raw).expect("the parity fixture is not valid JSON")
}

fn floats(v: &Value) -> Vec<f64> {
    v.as_array()
        .expect("expected an array of numbers")
        .iter()
        .map(|x| x.as_f64().expect("expected a number"))
        .collect()
}

fn table(v: &Value) -> Vec<Vec<f64>> {
    v.as_array()
        .expect("expected an array of arrays")
        .iter()
        .map(floats)
        .collect()
}

/// Compare two nested tables, reporting the case, field and level on failure.
///
/// The message carries `why` -- the fixture's own statement of what the case is
/// for -- because a bare numeric mismatch three months from now says nothing
/// about which property broke.
fn assert_table(got: &[Vec<f64>], want: &[Vec<f64>], tol: f64, case: &str, what: &str, why: &str) {
    assert_eq!(got.len(), want.len(), "{case}/{what}: field count");
    for (j, (g, w)) in got.iter().zip(want).enumerate() {
        assert_eq!(g.len(), w.len(), "{case}/{what}: field {j} level count");
        for (k, (a, b)) in g.iter().zip(w).enumerate() {
            assert!(
                (a - b).abs() <= tol,
                "{case}/{what}: field {j} level {k}: rust {a} vs python {b} \
                 (delta {}, tol {tol})\n  this case exists because: {why}",
                (a - b).abs()
            );
        }
    }
}

#[test]
fn the_rust_core_reproduces_the_python_reference() {
    let fx = fixture();
    let tol_p = fx["_tolerances"]["probabilities"].as_f64().unwrap();
    let tol_w = fx["_tolerances"]["match_weights"].as_f64().unwrap();

    let cases = fx["cases"].as_array().expect("cases");
    assert!(!cases.is_empty(), "the fixture carries no cases");

    for case in cases {
        let name = case["name"].as_str().unwrap();
        let why = case["why"].as_str().unwrap();

        let fields: Vec<EmField> = case["fields"]
            .as_array()
            .unwrap()
            .iter()
            .zip(case["conditioned"].as_array().unwrap())
            .map(|(f, _)| EmField {
                n_levels: f["n_levels"].as_u64().unwrap() as usize,
                // `is_blocking` stays false: the fixture expresses conditioning
                // through `conditioned`, which is the per-PASS signal the
                // counted path carries. Setting both would make it impossible
                // to tell which one the port is honouring.
                is_blocking: false,
            })
            .collect();
        let conditioned: Vec<bool> = case["conditioned"]
            .as_array()
            .unwrap()
            .iter()
            .map(|b| b.as_bool().unwrap())
            .collect();

        let patterns: Vec<Vec<i32>> = case["patterns"]
            .as_array()
            .unwrap()
            .iter()
            .map(|row| {
                row.as_array()
                    .unwrap()
                    .iter()
                    .map(|x| x.as_i64().unwrap() as i32)
                    .collect()
            })
            .collect();
        let counts = floats(&case["counts"]);
        let u_in = table(&case["u_probs_in"]);

        let out = train_em_from_counts(
            &fields,
            &patterns,
            &counts,
            &u_in,
            &conditioned,
            &EmParams::default(),
        );

        let expect = &case["expect"];
        assert_table(
            &out.m_probs,
            &table(&expect["m_probs"]),
            tol_p,
            name,
            "m_probs",
            why,
        );
        assert_table(
            &out.u_probs,
            &table(&expect["u_probs"]),
            tol_p,
            name,
            "u_probs",
            why,
        );
        assert_table(
            &out.match_weights,
            &table(&expect["match_weights"]),
            tol_w,
            name,
            "match_weights",
            why,
        );
        let want_p = expect["proportion_matched"].as_f64().unwrap();
        assert!(
            (out.proportion_matched - want_p).abs() <= tol_p,
            "{name}/proportion_matched: rust {} vs python {want_p}",
            out.proportion_matched
        );
        assert_eq!(
            out.converged,
            expect["converged"].as_bool().unwrap(),
            "{name}: converged flag"
        );
        assert_eq!(
            out.iterations,
            expect["iterations"].as_u64().unwrap() as usize,
            "{name}: iteration count -- the loops diverged even if the numbers \
             happened to land close"
        );

        // u-from-counts has no other gate: the trainer is handed `u` rather than
        // estimating it, so a broken estimator would never show up above.
        let u_est = estimate_u_from_counts(&fields, &patterns, &counts);
        assert_table(
            &u_est,
            &table(&case["expect_u_from_counts"]),
            tol_p,
            name,
            "estimate_u_from_counts",
            why,
        );
    }
}

#[test]
fn a_conditioned_field_takes_the_bounded_ramp_not_an_estimate() {
    // Stated separately from the fixture sweep because it is the #1835/#1836
    // failure and it is worth being able to read the rule off a test rather
    // than off a JSON blob. A near-unique blocking key whose `u` is learned
    // collapses toward the smoothing floor, which explodes log2(m/u) past 20
    // bits and lets one field dominate every other (measured F1 0.83 -> 0.57).
    let fields = vec![
        EmField {
            n_levels: 2,
            is_blocking: false,
        },
        EmField {
            n_levels: 2,
            is_blocking: false,
        },
    ];
    let patterns = vec![vec![1, 1], vec![0, 1]];
    let counts = vec![500.0, 300.0];
    // A deliberately near-unique u for field 1 -- the shape that explodes.
    let u_in = vec![vec![0.9, 0.1], vec![0.999, 0.001]];

    let out = train_em_from_counts(
        &fields,
        &patterns,
        &counts,
        &u_in,
        &[false, true],
        &EmParams::default(),
    );

    assert_eq!(
        out.match_weights[1],
        vec![-3.0, 3.0],
        "a conditioned field must take the bounded ramp; the near-unique u it \
         was handed must not reach the weights at all"
    );
    assert_eq!(
        out.u_probs[1],
        vec![0.5, 0.5],
        "and its u must be neutralised"
    );
    assert_ne!(
        out.match_weights[0],
        vec![-3.0, 3.0],
        "field 0 is free and must still be LEARNED -- if it also came back \
         [-3, 3] the rule is being applied to everything"
    );
}
