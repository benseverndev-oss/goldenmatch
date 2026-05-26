//! `goldenembed` — a standalone, local embedding runtime for GoldenMatch.
//!
//! Loads a model saved by `goldenmatch.embeddings.inhouse.GoldenEmbedModel.save`
//! (a directory with `config.json` + `model.onnx`), featurizes text with the
//! char-n-gram kernel, and runs the learned projection head through onnxruntime
//! — no Python, no torch. This is the runtime behind the roadmap's
//! `provider="inhouse"` embed path at the edge / in SQL UDFs.
use std::path::Path;

use anyhow::{Context, Result};
use ort::session::Session;
use ort::value::Tensor;
use serde::Deserialize;

mod featurizer;
pub use featurizer::FeaturizerConfig;

#[derive(Debug, Deserialize)]
struct ModelConfig {
    dim: usize,
    #[allow(dead_code)]
    use_bias: bool,
    featurizer: FeaturizerConfig,
}

pub struct GoldenEmbed {
    featurizer: FeaturizerConfig,
    dim: usize,
    session: Session,
}

impl GoldenEmbed {
    /// Load a saved model directory (`config.json` + `model.onnx`).
    pub fn load(dir: impl AsRef<Path>) -> Result<Self> {
        let dir = dir.as_ref();
        let cfg_text = std::fs::read_to_string(dir.join("config.json"))
            .with_context(|| format!("reading {}/config.json", dir.display()))?;
        let cfg: ModelConfig = serde_json::from_str(&cfg_text)?;
        let session = Session::builder()?
            .commit_from_file(dir.join("model.onnx"))
            .with_context(|| format!("loading {}/model.onnx", dir.display()))?;
        Ok(Self {
            featurizer: cfg.featurizer,
            dim: cfg.dim,
            session,
        })
    }

    pub fn dim(&self) -> usize {
        self.dim
    }

    /// Embed `texts` into `texts.len()` row vectors of length `dim`.
    pub fn embed(&mut self, texts: &[&str]) -> Result<Vec<Vec<f32>>> {
        let n = texts.len();
        if n == 0 {
            return Ok(Vec::new());
        }
        let f = self.featurizer.n_features;
        let feats = self.featurizer.featurize(texts); // flat (n * f), row-major
        let input = Tensor::from_array(([n, f], feats))?;
        let outputs = self.session.run(ort::inputs!["features" => input])?;
        let (shape, data) = outputs["embedding"].try_extract_tensor::<f32>()?;
        let dim = *shape.last().unwrap_or(&(self.dim as i64)) as usize;
        let mut rows = Vec::with_capacity(n);
        for i in 0..n {
            rows.push(data[i * dim..(i + 1) * dim].to_vec());
        }
        Ok(rows)
    }
}
