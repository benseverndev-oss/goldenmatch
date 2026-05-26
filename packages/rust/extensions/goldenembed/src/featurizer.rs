//! Char n-gram feature hashing — byte-identical to the Python reference
//! (`goldenmatch/embeddings/inhouse/featurizer.py`) and the pyo3 kernel
//! (`extensions/native/src/featurize.rs`). Pure Rust (no pyo3) so the runtime
//! is standalone.
//!
//! Parity contract: lowercase + whitespace-collapse, boundary-wrap unless empty;
//! n-grams over Unicode scalar values; hash = BLAKE2b(seed_le ++ ngram_utf8)
//! truncated to 8 bytes little-endian; index = h % n_features, sign = +1 if
//! bit63 else -1; L2-normalize with f32 sum-of-squares + f32 sqrt. The nonzero
//! counts are small exact integers, so the result matches numpy bit-for-bit.
use blake2::digest::{Update, VariableOutput};
use blake2::Blake2bVar;
use serde::Deserialize;

#[derive(Debug, Clone, Deserialize)]
pub struct FeaturizerConfig {
    pub n_features: usize,
    pub ngram_min: usize,
    pub ngram_max: usize,
    pub lowercase: bool,
    pub boundary: String,
    pub seed: u64,
}

fn prepare(text: &str, lowercase: bool, boundary: &str) -> String {
    let lowered = if lowercase {
        text.to_lowercase()
    } else {
        text.to_string()
    };
    let collapsed = lowered.split_whitespace().collect::<Vec<_>>().join(" ");
    if collapsed.is_empty() {
        return String::new();
    }
    format!("{boundary}{collapsed}{boundary}")
}

fn hash_gram(seed_le: &[u8; 8], gram: &str, n_features: u64) -> (usize, f32) {
    let mut hasher = Blake2bVar::new(8).expect("blake2b-8 is valid");
    hasher.update(seed_le);
    hasher.update(gram.as_bytes());
    let mut buf = [0u8; 8];
    hasher
        .finalize_variable(&mut buf)
        .expect("8-byte output fits");
    let h = u64::from_le_bytes(buf);
    let idx = (h % n_features) as usize;
    let sign = if (h >> 63) & 1 == 1 {
        1.0_f32
    } else {
        -1.0_f32
    };
    (idx, sign)
}

impl FeaturizerConfig {
    /// Featurize one text into an `n_features`-long L2-normalized row.
    pub fn featurize_one(&self, text: &str) -> Vec<f32> {
        let seed_le = self.seed.to_le_bytes();
        let mut row = vec![0.0_f32; self.n_features];
        let prepared = prepare(text, self.lowercase, &self.boundary);
        let chars: Vec<char> = prepared.chars().collect();
        for n in self.ngram_min..=self.ngram_max {
            if chars.len() < n {
                continue;
            }
            for i in 0..=(chars.len() - n) {
                let gram: String = chars[i..i + n].iter().collect();
                let (idx, sign) = hash_gram(&seed_le, &gram, self.n_features as u64);
                row[idx] += sign;
            }
        }
        let sumsq: f32 = row.iter().map(|&v| v * v).sum();
        let norm = sumsq.sqrt() as f64;
        if norm > 0.0 {
            for v in row.iter_mut() {
                *v = (*v as f64 / norm) as f32;
            }
        }
        row
    }

    /// Featurize `texts` into a flat row-major `(texts.len() * n_features)` buffer.
    pub fn featurize(&self, texts: &[&str]) -> Vec<f32> {
        let mut out = Vec::with_capacity(texts.len() * self.n_features);
        for t in texts {
            out.extend(self.featurize_one(t));
        }
        out
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn cfg() -> FeaturizerConfig {
        FeaturizerConfig {
            n_features: 2048,
            ngram_min: 2,
            ngram_max: 4,
            lowercase: true,
            boundary: "\u{2}".to_string(),
            seed: 0,
        }
    }

    fn norm(v: &[f32]) -> f32 {
        v.iter().map(|x| x * x).sum::<f32>().sqrt()
    }

    fn dot(a: &[f32], b: &[f32]) -> f32 {
        a.iter().zip(b).map(|(x, y)| x * y).sum()
    }

    #[test]
    fn deterministic_and_unit_norm() {
        let c = cfg();
        let a = c.featurize_one("Acme Corp");
        let b = c.featurize_one("Acme Corp");
        assert_eq!(a, b);
        assert!((norm(&a) - 1.0).abs() < 1e-6);
    }

    #[test]
    fn lowercase_and_whitespace_collapse() {
        let c = cfg();
        assert_eq!(c.featurize_one("Acme  Corp"), c.featurize_one("acme corp"));
    }

    #[test]
    fn empty_is_zero_vector() {
        let c = cfg();
        assert!(c.featurize_one("").iter().all(|&x| x == 0.0));
    }

    #[test]
    fn similar_closer_than_dissimilar() {
        let c = cfg();
        let a = c.featurize_one("John Smith");
        let b = c.featurize_one("Jon Smith");
        let d = c.featurize_one("Zebra Industries");
        assert!(dot(&a, &b) > dot(&a, &d));
    }
}
