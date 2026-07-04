//! End-to-end proof of the native (ONNX-free) runtime path: load a committed
//! model fixture (`config.json` + `weights.npz`, no `model.onnx`) via
//! `GoldenEmbed::load` with the DEFAULT features (no `ort`), embed a corpus, and
//! assert it matches the numpy reference `GoldenEmbedModel.embed(backend="numpy")`
//! within cosine tolerance. Exercises the whole native chain: `weights.npz`
//! parse -> featurize -> `goldenembed-core::project`. This is what lets the SQL
//! surfaces stop linking ONNX Runtime.

use goldenembed::GoldenEmbed;
use std::path::PathBuf;

#[test]
fn native_load_embeds_match_numpy_reference() {
    let dir = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/model");
    let meta: serde_json::Value =
        serde_json::from_str(&std::fs::read_to_string(dir.join("expected.json")).unwrap()).unwrap();
    let corpus: Vec<String> = meta["corpus"]
        .as_array()
        .unwrap()
        .iter()
        .map(|v| v.as_str().unwrap().to_string())
        .collect();
    let dim = meta["dim"].as_u64().unwrap() as usize;
    let expected: Vec<Vec<f32>> = meta["expected"]
        .as_array()
        .unwrap()
        .iter()
        .map(|row| {
            row.as_array()
                .unwrap()
                .iter()
                .map(|x| x.as_f64().unwrap() as f32)
                .collect()
        })
        .collect();

    // Native path: the fixture has no model.onnx, so this only works because
    // load() reads weights.npz and embed() runs the matmul — no `ort`.
    let mut model = GoldenEmbed::load(&dir).expect("load native model");
    assert_eq!(model.dim(), dim);
    let refs: Vec<&str> = corpus.iter().map(String::as_str).collect();
    let got = model.embed(&refs).expect("embed");
    assert_eq!(got.len(), expected.len());

    let mut worst_cos = 0.0f64;
    let mut worst_abs = 0.0f64;
    for (g, e) in got.iter().zip(&expected) {
        assert_eq!(g.len(), dim);
        for (&gi, &ei) in g.iter().zip(e) {
            worst_abs = worst_abs.max((gi as f64 - ei as f64).abs());
        }
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
            worst_cos = worst_cos.max(1.0 - dot);
        }
    }
    assert!(
        worst_cos < 1e-5,
        "cosine distance {worst_cos:.3e} exceeds 1e-5"
    );
    assert!(
        worst_abs < 1e-4,
        "max component diff {worst_abs:.3e} exceeds 1e-4"
    );
}
