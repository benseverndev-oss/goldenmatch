//! Parity harness: the native `goldenembed::project` matmul reproduces the
//! numpy reference `GoldenEmbedModel.project` (`L2norm((feats @ W) + b)`) within
//! cosine tolerance — the bar the thresholded-similarity callers need (the
//! output feeds cosine blocking, not a hash). The fixture `golden/
//! project_golden.json` was generated from the numpy reference over a small
//! model + corpus. This is what lets goldenembed drop ONNX Runtime: the pure
//! Rust path is provably equivalent, so `session.run` is unnecessary.

use base64::Engine;
use goldenembed_core::{project, FeaturizerConfig};
use serde::Deserialize;

#[derive(Deserialize)]
struct FeatCfg {
    n_features: usize,
    ngram_min: usize,
    ngram_max: usize,
    lowercase: bool,
    boundary: String,
    seed: u64,
}

#[derive(Deserialize)]
struct Case {
    dim: usize,
    #[allow(dead_code)]
    use_bias: bool,
    featurizer: FeatCfg,
    n_features: usize,
    weights_b64: String,
    bias_b64: Option<String>,
    corpus: Vec<String>,
    expected_b64: String,
}

fn f32s(b64: &str) -> Vec<f32> {
    let bytes = base64::engine::general_purpose::STANDARD
        .decode(b64)
        .expect("valid base64");
    bytes
        .chunks_exact(4)
        .map(|c| f32::from_le_bytes([c[0], c[1], c[2], c[3]]))
        .collect()
}

#[test]
fn native_project_matches_numpy_reference() {
    let raw = include_str!("../golden/project_golden.json");
    let cases: Vec<Case> = serde_json::from_str(raw).expect("fixture parses");
    assert!(cases.len() >= 3, "expected multiple configs");

    for (ci, case) in cases.iter().enumerate() {
        let fc = FeaturizerConfig {
            n_features: case.featurizer.n_features,
            ngram_min: case.featurizer.ngram_min,
            ngram_max: case.featurizer.ngram_max,
            lowercase: case.featurizer.lowercase,
            boundary: case.featurizer.boundary.clone(),
            seed: case.featurizer.seed,
        };
        let texts: Vec<&str> = case.corpus.iter().map(String::as_str).collect();
        let feats = fc.featurize(&texts);
        let n = texts.len();
        let weights = f32s(&case.weights_b64);
        let bias = case.bias_b64.as_deref().map(f32s);
        let expected = f32s(&case.expected_b64);

        let got = project(
            &feats,
            n,
            case.n_features,
            &weights,
            case.dim,
            bias.as_deref(),
        );
        assert_eq!(got.len(), expected.len());

        let d = case.dim;
        let mut worst_cos = 0.0f64;
        let mut worst_abs = 0.0f64;
        for row in 0..n {
            let g = &got[row * d..(row + 1) * d];
            let e = &expected[row * d..(row + 1) * d];
            // Max absolute component diff over ALL rows (catches a zero row that
            // should be zero, where cosine is undefined).
            for (&gi, &ei) in g.iter().zip(e) {
                worst_abs = worst_abs.max((gi as f64 - ei as f64).abs());
            }
            // Cosine only where the reference row is non-zero.
            let enorm: f64 = e
                .iter()
                .map(|&v| (v as f64) * (v as f64))
                .sum::<f64>()
                .sqrt();
            if enorm > 0.0 {
                let dot: f64 = g
                    .iter()
                    .zip(e)
                    .map(|(&gi, &ei)| gi as f64 * ei as f64)
                    .sum();
                worst_cos = worst_cos.max(1.0 - dot); // both ~unit -> cosine sim
            }
        }
        assert!(
            worst_cos < 1e-5,
            "case {ci}: cosine distance {worst_cos:.3e} exceeds 1e-5",
        );
        assert!(
            worst_abs < 1e-4,
            "case {ci}: max component diff {worst_abs:.3e} exceeds 1e-4",
        );
    }
}
