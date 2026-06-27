//! BLESS-guarded golden suggestion fixtures -- the INDEPENDENT ORACLE.
//!
//! The fixtures' `expected` arrays are authored + guarded by the Rust kernel
//! (`suggest_from_json`), NOT by the wasm binding we later test against them
//! (that would be tautological -- it would pin determinism, not correctness).
//! The TS build script copies these files into the TS parity fixtures so the
//! wasm/TS path is checked against an expectation the kernel itself produced.
//!
//! Two modes:
//!   - `BLESS_SUGGEST_FIXTURES=1 cargo test --features arrow --test bless`
//!     WRITES each `{ input, expected }` to tests/golden/suggest/<case>.json.
//!   - plain `cargo test --features arrow --test bless` READS each committed
//!     file and asserts `suggest_from_json(input...) == expected`.
//!
//! Gated on `feature = "arrow"` so it runs in the same lane as the golden suite
//! (the kernel call itself is arrow-free; the gate just scopes the oracle).
#![cfg(feature = "arrow")]

use goldenmatch_suggest_core::suggest_from_json;
use std::path::PathBuf;

/// The five packed JSON inputs for one case (each is a JSON string, exactly the
/// `suggest_from_json` arguments).
struct Case {
    name: &'static str,
    scored_pairs: String,
    clusters: String,
    column_signals: String,
    config: String,
    priors: String,
}

fn priors_empty() -> String {
    r#"{"counts": {}}"#.to_string()
}

/// A clean column signal that triggers NEITHER swap_scorer NOR add_negative_evidence
/// (so threshold-rule cases produce only their threshold suggestion).
fn clean_signals() -> String {
    r#"[{
        "field": "record_id",
        "col_type": "identifier",
        "scorer": "exact",
        "in_blocking": true,
        "in_negative_evidence": false,
        "identity_score": 0.3,
        "corruption_score": 0.0,
        "collision_rate": 0.0,
        "cardinality_ratio": 0.4,
        "null_rate": 0.0,
        "variant_rate": 0.0
    }]"#
    .to_string()
}

/// Bimodal score distribution (low cluster ~0.05-0.25, high cluster ~0.70-0.90,
/// empty middle). With a threshold at 0.85 the dip sits well below it -> a
/// `lower_threshold` suggestion.
fn bimodal_pairs() -> String {
    let mut scores: Vec<f64> = Vec::new();
    for _ in 0..20 {
        scores.extend_from_slice(&[0.05, 0.10, 0.15, 0.20, 0.25]);
    }
    for _ in 0..20 {
        scores.extend_from_slice(&[0.70, 0.75, 0.80, 0.85, 0.90]);
    }
    let n_pairs = scores.len();
    serde_json::to_string(&serde_json::json!({ "score": scores, "n_pairs": n_pairs })).unwrap()
}

fn config_one_matchkey(threshold: f64) -> String {
    serde_json::json!({
        "matchkeys": [{
            "name": "person",
            "kind": "weighted",
            "threshold": threshold,
            "fields": [{"field": "record_id", "scorer": "exact", "weight": 1.0}]
        }],
        "negative_evidence": []
    })
    .to_string()
}

fn cases() -> Vec<Case> {
    vec![
        // (1) lower_threshold: bimodal scores, threshold above the valley.
        Case {
            name: "lower_threshold",
            scored_pairs: bimodal_pairs(),
            clusters: r#"[{"quality": "strong", "oversized": false}]"#.to_string(),
            column_signals: clean_signals(),
            config: config_one_matchkey(0.85),
            priors: priors_empty(),
        },
        // (2) raise_threshold: every score clears a low threshold (mass_above > 0.90),
        //     unimodal (no dip) -> a single raise_threshold suggestion.
        Case {
            name: "raise_threshold",
            scored_pairs: serde_json::to_string(&serde_json::json!({
                "score": [0.92, 0.94, 0.95, 0.96, 0.97, 0.98, 0.99],
                "n_pairs": 7
            }))
            .unwrap(),
            clusters: r#"[{"quality": "strong", "oversized": false}]"#.to_string(),
            column_signals: clean_signals(),
            config: config_one_matchkey(0.50),
            priors: priors_empty(),
        },
        // (3) swap_scorer: empty pairs (no threshold signal), corrupted address
        //     column scored with token_sort -> a single swap_scorer suggestion.
        Case {
            name: "swap_scorer",
            scored_pairs: r#"{"score": [], "n_pairs": 0}"#.to_string(),
            clusters: "[]".to_string(),
            column_signals: r#"[{
                "field": "res_street_address",
                "col_type": "address",
                "scorer": "token_sort",
                "in_blocking": false,
                "in_negative_evidence": false,
                "identity_score": 0.0,
                "corruption_score": 0.6,
                "collision_rate": 0.0,
                "cardinality_ratio": 0.5,
                "null_rate": 0.0,
                "variant_rate": 0.0
            }]"#
            .to_string(),
            config: config_one_matchkey(0.80),
            priors: priors_empty(),
        },
        // (4) add_negative_evidence: empty pairs, a strong identity column that
        //     collides within merged clusters -> a single add_negative_evidence
        //     suggestion. col_type "string"+scorer "exact" so swap does NOT fire.
        Case {
            name: "add_negative_evidence",
            scored_pairs: r#"{"score": [], "n_pairs": 0}"#.to_string(),
            clusters: "[]".to_string(),
            column_signals: r#"[{
                "field": "npi",
                "col_type": "string",
                "scorer": "exact",
                "in_blocking": false,
                "in_negative_evidence": false,
                "identity_score": 0.9,
                "corruption_score": 0.0,
                "collision_rate": 0.6,
                "cardinality_ratio": 0.8,
                "null_rate": 0.0,
                "variant_rate": 0.0
            }]"#
            .to_string(),
            config: config_one_matchkey(0.80),
            priors: priors_empty(),
        },
        // (5) empty/no-op: empty pairs + empty clusters + empty signals -> no
        //     suggestion fires (the graceful-empty contract).
        Case {
            name: "empty",
            scored_pairs: r#"{"score": [], "n_pairs": 0}"#.to_string(),
            clusters: "[]".to_string(),
            column_signals: "[]".to_string(),
            config: config_one_matchkey(0.80),
            priors: priors_empty(),
        },
    ]
}

fn golden_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("tests")
        .join("golden")
        .join("suggest")
}

fn run_case(c: &Case) -> serde_json::Value {
    let out = suggest_from_json(
        &c.scored_pairs,
        &c.clusters,
        &c.column_signals,
        &c.config,
        &c.priors,
    )
    .unwrap_or_else(|e| panic!("suggest_from_json failed for case {}: {e}", c.name));
    serde_json::from_str(&out)
        .unwrap_or_else(|e| panic!("case {} output is not valid JSON: {e}", c.name))
}

#[test]
fn golden_suggest_fixtures() {
    let bless = std::env::var("BLESS_SUGGEST_FIXTURES").is_ok();
    let dir = golden_dir();
    if bless {
        std::fs::create_dir_all(&dir).expect("create golden/suggest dir");
    }

    for c in cases() {
        let expected = run_case(&c);
        let doc = serde_json::json!({
            "input": {
                "scored_pairs": c.scored_pairs,
                "clusters": c.clusters,
                "column_signals": c.column_signals,
                "config": c.config,
                "priors": c.priors,
            },
            "expected": expected,
        });
        let path = dir.join(format!("{}.json", c.name));

        if bless {
            let pretty = serde_json::to_string_pretty(&doc).unwrap();
            std::fs::write(&path, pretty + "\n")
                .unwrap_or_else(|e| panic!("write golden {}: {e}", path.display()));
            println!("blessed {}", path.display());
        } else {
            let raw = std::fs::read_to_string(&path).unwrap_or_else(|e| {
                panic!(
                    "missing golden fixture {} ({e}). Run with BLESS_SUGGEST_FIXTURES=1 to author.",
                    path.display()
                )
            });
            let committed: serde_json::Value =
                serde_json::from_str(&raw).expect("golden fixture must be valid JSON");
            // Re-run the kernel over the committed input and assert it matches the
            // committed expected (independent-oracle invariant).
            let input = &committed["input"];
            let got = suggest_from_json(
                input["scored_pairs"].as_str().unwrap(),
                input["clusters"].as_str().unwrap(),
                input["column_signals"].as_str().unwrap(),
                input["config"].as_str().unwrap(),
                input["priors"].as_str().unwrap(),
            )
            .unwrap_or_else(|e| panic!("suggest_from_json failed for committed {}: {e}", c.name));
            let got_val: serde_json::Value = serde_json::from_str(&got).unwrap();
            assert_eq!(
                got_val, committed["expected"],
                "case {} kernel output drifted from committed golden",
                c.name
            );
        }
    }
}
