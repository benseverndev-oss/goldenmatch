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
pub mod cache;
pub mod model_id;

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
    model_id: Option<String>,
    onnx_digest: String,
    session: Session,
}

impl GoldenEmbed {
    /// Load a saved model directory (`config.json` + `model.onnx`).
    pub fn load(dir: impl AsRef<Path>) -> Result<Self> {
        let dir = dir.as_ref();
        let cfg_text = std::fs::read_to_string(dir.join("config.json"))
            .with_context(|| format!("reading {}/config.json", dir.display()))?;
        let cfg: ModelConfig = serde_json::from_str(&cfg_text)?;
        let model_id = crate::model_id::compute_model_id(dir, cfg.dim).ok();
        let onnx_bytes = std::fs::read(dir.join("model.onnx"))
            .with_context(|| format!("reading {}/model.onnx", dir.display()))?;
        let onnx_digest = {
            use blake2::digest::{Update, VariableOutput};
            let mut h = blake2::Blake2bVar::new(8).expect("blake2b-8 is valid");
            h.update(&onnx_bytes);
            let mut out = [0u8; 8];
            h.finalize_variable(&mut out).expect("8-byte output fits");
            crate::model_id::hex(&out)
        };
        let session = Session::builder()?
            .commit_from_file(dir.join("model.onnx"))
            .with_context(|| format!("loading {}/model.onnx", dir.display()))?;
        Ok(Self {
            featurizer: cfg.featurizer,
            dim: cfg.dim,
            model_id,
            onnx_digest,
            session,
        })
    }

    pub fn dim(&self) -> usize {
        self.dim
    }

    /// The Python-parity cache namespace, or `None` when `weights.npz` is absent
    /// (ONNX-only deployment).
    pub fn model_id(&self) -> Option<&str> {
        self.model_id.as_deref()
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

    /// Embed `texts` with cache lookups keyed by `(model_id, sha256(normalize_text))`.
    /// Unique misses (deduped by text_hash) are embedded in one batched ONNX run,
    /// chunked to bound memory. Output is in input order.
    pub fn embed_cached(
        &mut self,
        texts: &[&str],
        cache: &mut crate::cache::EmbedCache,
    ) -> anyhow::Result<Vec<Vec<f32>>> {
        use crate::cache::{normalize_text, text_hash};
        use std::collections::HashMap;
        const MISS_CHUNK: usize = 4096;

        let model_id = self
            .model_id()
            .map(str::to_owned)
            .unwrap_or_else(|| self.onnx_fallback_namespace());

        let normalized: Vec<String> = texts.iter().map(|t| normalize_text(t)).collect();
        let hashes: Vec<String> = normalized.iter().map(|n| text_hash(n)).collect();

        let mut resolved: HashMap<String, Vec<f32>> = HashMap::new();
        let mut miss_order: Vec<String> = Vec::new();
        let mut miss_text: HashMap<String, String> = HashMap::new();
        for (norm, h) in normalized.iter().zip(&hashes) {
            if resolved.contains_key(h) || miss_text.contains_key(h) {
                continue;
            }
            if let Some(v) = cache.get(&model_id, h) {
                resolved.insert(h.clone(), v);
            } else {
                miss_order.push(h.clone());
                miss_text.insert(h.clone(), norm.clone());
            }
        }

        for chunk in miss_order.chunks(MISS_CHUNK) {
            let batch_texts: Vec<&str> = chunk.iter().map(|h| miss_text[h].as_str()).collect();
            let vecs = self.embed(&batch_texts)?;
            for (h, vec) in chunk.iter().zip(vecs) {
                cache.put(&model_id, h, vec.clone())?;
                resolved.insert(h.clone(), vec);
            }
        }

        Ok(hashes.iter().map(|h| resolved[h].clone()).collect())
    }

    /// Deterministic cache namespace when `weights.npz` is absent: blake2b-8 over
    /// the raw `model.onnx` bytes. Will NOT match Python's `inhouse:…` id (parity
    /// needs the weights) — fine, since Python doesn't share this redb file.
    fn onnx_fallback_namespace(&self) -> String {
        format!("onnx:d{}:{}", self.dim, self.onnx_digest)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    fn tiny() -> PathBuf {
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/tiny_model")
    }

    #[test]
    fn embed_cached_equals_uncached() {
        let mut m = GoldenEmbed::load(tiny()).unwrap();
        let texts = ["Acme Corp", "acme  corp", "Zebra Inc"];
        let direct = m.embed(&texts).unwrap();
        let mut cache = crate::cache::EmbedCache::in_memory();
        let cached = m.embed_cached(&texts, &mut cache).unwrap();
        assert_eq!(direct.len(), cached.len());
        for (a, b) in direct.iter().zip(&cached) {
            for (x, y) in a.iter().zip(b) {
                assert!((x - y).abs() < 1e-6, "{x} vs {y}");
            }
        }
        // "Acme Corp" and "acme  corp" normalize identically -> one cache entry.
        assert_eq!(cache.len(), 2);
    }
}
