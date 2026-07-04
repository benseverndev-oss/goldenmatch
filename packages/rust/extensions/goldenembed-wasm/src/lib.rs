//! wasm-bindgen wrapper over `goldenembed-core`, so the in-house embedder runs
//! at the edge: char n-gram **featurize** + the linear **projection head** —
//! the SAME kernels the Python native path and the SQL surfaces run. Edge-safe
//! (pure wasm, no `node:*`, no ONNX). Closes parity-roadmap P10.
//!
//! The model (a `(n_features, dim)` projection matrix + optional length-`dim`
//! bias) is supplied by the caller as `Float32Array`s at construction and reused
//! across `embed` calls; texts cross as `string[]` and the `(n, dim)` row-major
//! embedding matrix crosses back as one `Float32Array`.

use goldenembed_core::{project, FeaturizerConfig};
use wasm_bindgen::prelude::*;

/// An initialized in-house embedder: featurizer config + the projection weights.
/// Construct once with the model, then call `embed` per batch.
#[wasm_bindgen]
pub struct Embedder {
    fc: FeaturizerConfig,
    weights: Vec<f32>,
    bias: Option<Vec<f32>>,
    dim: usize,
}

#[wasm_bindgen]
impl Embedder {
    /// Build an embedder. `weights` is the row-major `(n_features * dim)`
    /// projection matrix; `bias` an optional length-`dim` vector (pass
    /// `undefined`/`null` for none). The featurizer params must match the model
    /// (`n_features`, `ngram_min`, `ngram_max`, `lowercase`, `boundary`, `seed`).
    /// `seed` is taken as an `f64` for JS ergonomics (seeds are small, exact).
    #[wasm_bindgen(constructor)]
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        weights: Vec<f32>,
        dim: usize,
        bias: Option<Vec<f32>>,
        n_features: usize,
        ngram_min: usize,
        ngram_max: usize,
        lowercase: bool,
        boundary: String,
        seed: f64,
    ) -> Result<Embedder, JsError> {
        if dim == 0 || n_features == 0 {
            return Err(JsError::new("dim and n_features must be positive"));
        }
        if weights.len() != n_features * dim {
            return Err(JsError::new("weights length must equal n_features * dim"));
        }
        if let Some(b) = &bias {
            if b.len() != dim {
                return Err(JsError::new("bias length must equal dim"));
            }
        }
        Ok(Embedder {
            fc: FeaturizerConfig {
                n_features,
                ngram_min,
                ngram_max,
                lowercase,
                boundary,
                seed: seed as u64,
            },
            weights,
            bias,
            dim,
        })
    }

    /// The embedding dimension.
    #[wasm_bindgen(getter)]
    pub fn dim(&self) -> usize {
        self.dim
    }

    /// Embed `texts` into a row-major `(texts.len() * dim)` `Float32Array`
    /// (each row L2-normalized). Same kernel + output (within cosine tolerance)
    /// as the Python / native / ONNX surfaces.
    pub fn embed(&self, texts: Vec<String>) -> Vec<f32> {
        let refs: Vec<&str> = texts.iter().map(String::as_str).collect();
        let feats = self.fc.featurize(&refs);
        project(
            &feats,
            refs.len(),
            self.fc.n_features,
            &self.weights,
            self.dim,
            self.bias.as_deref(),
        )
    }
}
