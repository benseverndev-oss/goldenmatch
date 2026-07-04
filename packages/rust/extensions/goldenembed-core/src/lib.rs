//! `goldenembed-core` — the pyo3-free / ort-free / fs-free compute core of the
//! in-house embedder: the char n-gram **featurizer** and the linear
//! **projection head**. One source of truth shared by `goldenembed` (the native
//! runtime, with load/cache and an optional ONNX fallback) and `goldenembed-wasm`
//! (the edge surface). Pulls no ONNX/C++/filesystem, so it compiles to wasm32 —
//! which is what unblocks edge embedding (parity roadmap P10).

mod featurizer;
pub use featurizer::FeaturizerConfig;

/// Native projection head — `L2norm((feats @ W) + b)`, the same computation the
/// ONNX graph runs (`MatMul` -> optional `Add` -> `LpNormalization`) and the
/// numpy reference `GoldenEmbedModel.project`. Pure Rust (no ONNX, no C++), so
/// it also compiles to wasm32 for the edge.
///
/// `feats` is the row-major `(n, n_features)` feature matrix (from the
/// featurizer), `weights` the row-major `(n_features, dim)` projection, `bias`
/// an optional length-`dim` vector. The matmul accumulates in **f64** (at least
/// as accurate as the f32 dense matmul), rounds to f32, then L2-normalizes each
/// row (a zero row stays zero). Not byte-identical to numpy/ONNX — f32
/// accumulation order differs — but agrees to cosine distance < 2e-7 (proven in
/// `tests/project_parity.rs`), which is what the thresholded-similarity callers
/// need. Zero feature values are skipped, so this IS the sparse fused path for
/// the (typically very sparse) char-n-gram feature rows.
pub fn project(
    feats: &[f32],
    n: usize,
    n_features: usize,
    weights: &[f32],
    dim: usize,
    bias: Option<&[f32]>,
) -> Vec<f32> {
    let mut out = vec![0.0f32; n * dim];
    let mut acc = vec![0.0f64; dim];
    for i in 0..n {
        let frow = &feats[i * n_features..(i + 1) * n_features];
        for a in acc.iter_mut() {
            *a = 0.0;
        }
        for (k, &fv) in frow.iter().enumerate() {
            if fv == 0.0 {
                continue;
            }
            let fv = fv as f64;
            let wrow = &weights[k * dim..(k + 1) * dim];
            for (j, &w) in wrow.iter().enumerate() {
                acc[j] += fv * w as f64;
            }
        }
        if let Some(b) = bias {
            for (j, &bj) in b.iter().enumerate() {
                acc[j] += bj as f64;
            }
        }
        // Round to f32 (numpy's intermediate dtype), then normalize.
        let zf: Vec<f32> = acc.iter().map(|&v| v as f32).collect();
        let norm = zf
            .iter()
            .map(|&v| (v as f64) * (v as f64))
            .sum::<f64>()
            .sqrt();
        let norm = if norm == 0.0 { 1.0 } else { norm };
        let orow = &mut out[i * dim..(i + 1) * dim];
        for (o, &z) in orow.iter_mut().zip(&zf) {
            *o = (z as f64 / norm) as f32;
        }
    }
    out
}
