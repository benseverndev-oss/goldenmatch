//! Char n-gram feature-hashing kernel — behavior-exact port of
//! `goldenmatch/embeddings/inhouse/featurizer.py::CharNGramFeaturizer.transform`.
//!
//! This is the in-house embedder's tokenizer: signed feature hashing over
//! character n-grams. Moving it native makes the whole embed path native (this
//! kernel produces the feature vectors the ONNX projection head consumes).
//!
//! Parity contract with the Python reference:
//! - text prep: lowercase (`str::to_lowercase` == Python `str.lower`) then
//!   whitespace-collapse (`split_whitespace().join(" ")` == Python `" ".join(s.split())`),
//!   boundary-wrapped unless empty;
//! - n-grams iterate over Unicode scalar values (chars), matching Python's
//!   code-point slicing `s[i:i+n]`;
//! - hash: `BLAKE2b(seed_le_bytes ++ ngram_utf8)` truncated to 8 bytes, read
//!   little-endian — identical to `hashlib.blake2b(..., digest_size=8)`. Index
//!   `= h % n_features`, sign `= +1 if bit63 else -1`;
//! - L2 normalize with a float32 sum-of-squares + float32 sqrt, then divide in
//!   f64 and round to f32. The nonzero counts are small exact integers, so the
//!   sum carries no rounding and the result matches numpy bit-for-bit.
use blake2::digest::{Update, VariableOutput};
use blake2::Blake2bVar;
use pyo3::prelude::*;
use pyo3::types::PyBytes;
use rayon::prelude::*;

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

/// `(index, sign)` for one n-gram. Matches the Python `_hash`.
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

fn featurize_one(
    text: &str,
    n_features: usize,
    ngram_min: usize,
    ngram_max: usize,
    lowercase: bool,
    boundary: &str,
    seed_le: &[u8; 8],
) -> Vec<f32> {
    let mut row = vec![0.0_f32; n_features];
    let prepared = prepare(text, lowercase, boundary);
    let chars: Vec<char> = prepared.chars().collect();
    for n in ngram_min..=ngram_max {
        if chars.len() < n {
            continue;
        }
        for i in 0..=(chars.len() - n) {
            let gram: String = chars[i..i + n].iter().collect();
            let (idx, sign) = hash_gram(seed_le, &gram, n_features as u64);
            row[idx] += sign;
        }
    }
    // L2 normalize (see module docs for the bit-parity rationale).
    let sumsq: f32 = row.iter().map(|&v| v * v).sum();
    let norm = sumsq.sqrt() as f64;
    if norm > 0.0 {
        for v in row.iter_mut() {
            *v = (*v as f64 / norm) as f32;
        }
    }
    row
}

/// Fused featurize + project for one text: accumulate `sign * W[idx]` straight
/// into a `dim`-vector, then L2-normalize. Never materializes the dense
/// `n_features`-wide feature row, and never runs the full dense matmul — for a
/// bias-free linear head this is exactly `L2norm(L2norm(features) @ W)` because
/// the feature-norm scalar cancels under the final normalization. Accumulation
/// is f64 so the result is at least as accurate as the f32 dense matmul it
/// mirrors.
#[allow(clippy::too_many_arguments)]
fn project_one(
    text: &str,
    w: &[f32],
    n_features: usize,
    dim: usize,
    ngram_min: usize,
    ngram_max: usize,
    lowercase: bool,
    boundary: &str,
    seed_le: &[u8; 8],
) -> Vec<f32> {
    let mut acc = vec![0.0_f64; dim];
    let prepared = prepare(text, lowercase, boundary);
    let chars: Vec<char> = prepared.chars().collect();
    for n in ngram_min..=ngram_max {
        if chars.len() < n {
            continue;
        }
        for i in 0..=(chars.len() - n) {
            let gram: String = chars[i..i + n].iter().collect();
            let (idx, sign) = hash_gram(seed_le, &gram, n_features as u64);
            let row = &w[idx * dim..idx * dim + dim];
            let s = sign as f64;
            for d in 0..dim {
                acc[d] += s * row[d] as f64;
            }
        }
    }
    let norm = acc.iter().map(|&v| v * v).sum::<f64>().sqrt();
    let mut out = vec![0.0_f32; dim];
    if norm > 0.0 {
        for d in 0..dim {
            out[d] = (acc[d] / norm) as f32;
        }
    }
    out
}

/// Fused featurize + project over `texts`. `weights` is the row-major
/// `(n_features * dim)` f32 projection matrix as native-endian bytes (e.g.
/// `model.weights.tobytes()`). Returns a flat `(n * dim)` f32 buffer as bytes
/// (wrap with `np.frombuffer(...).reshape(n, dim)`), mirroring
/// `char_ngram_features`. Only valid for the bias-free linear head.
#[allow(clippy::too_many_arguments)]
#[pyfunction]
pub fn char_ngram_project<'py>(
    py: Python<'py>,
    texts: Vec<Option<String>>,
    weights: &Bound<'py, PyBytes>,
    n_features: usize,
    dim: usize,
    ngram_min: usize,
    ngram_max: usize,
    lowercase: bool,
    boundary: String,
    seed: u64,
) -> Bound<'py, PyBytes> {
    let seed_le = seed.to_le_bytes();
    let w: Vec<f32> = weights
        .as_bytes()
        .chunks_exact(4)
        .map(|c| f32::from_ne_bytes([c[0], c[1], c[2], c[3]]))
        .collect();
    let floats: Vec<f32> = py.allow_threads(|| {
        texts
            .par_iter()
            .flat_map_iter(|text| {
                let s = text.as_deref().unwrap_or("");
                project_one(
                    s, &w, n_features, dim, ngram_min, ngram_max, lowercase, &boundary, &seed_le,
                )
            })
            .collect()
    });
    let bytes = unsafe {
        std::slice::from_raw_parts(
            floats.as_ptr() as *const u8,
            std::mem::size_of_val(&floats[..]),
        )
    };
    PyBytes::new(py, bytes)
}

/// Featurize `texts` into a flat row-major `(n * n_features)` f32 buffer,
/// returned as native-endian `bytes` so the Python caller can wrap it with
/// `np.frombuffer(...).reshape(n, n_features)` — one memcpy, no per-float
/// Python objects (returning `Vec<f32>` makes pyo3 allocate millions of Python
/// floats, which is far slower than the pure-Python loop). The buffer is
/// ephemeral and consumed in-process, so native endianness is fine. Rows are
/// independent, computed in parallel with `rayon` under `allow_threads`.
#[allow(clippy::too_many_arguments)]
#[pyfunction]
pub fn char_ngram_features<'py>(
    py: Python<'py>,
    texts: Vec<Option<String>>,
    n_features: usize,
    ngram_min: usize,
    ngram_max: usize,
    lowercase: bool,
    boundary: String,
    seed: u64,
) -> Bound<'py, PyBytes> {
    let seed_le = seed.to_le_bytes();
    let floats: Vec<f32> = py.allow_threads(|| {
        texts
            .par_iter()
            .flat_map_iter(|text| {
                let s = text.as_deref().unwrap_or("");
                featurize_one(
                    s, n_features, ngram_min, ngram_max, lowercase, &boundary, &seed_le,
                )
            })
            .collect()
    });
    let bytes = unsafe {
        std::slice::from_raw_parts(
            floats.as_ptr() as *const u8,
            std::mem::size_of_val(&floats[..]),
        )
    };
    PyBytes::new(py, bytes)
}

#[cfg(test)]
mod tests {
    use super::*;

    const SEED: [u8; 8] = 42u64.to_le_bytes();

    #[test]
    fn prepare_lowercases_collapses_and_wraps() {
        assert_eq!(prepare("  John   SMITH ", true, "#"), "#john smith#");
        assert_eq!(prepare("John Smith", false, "#"), "#John Smith#");
    }

    #[test]
    fn prepare_empty_stays_empty_no_boundary() {
        assert_eq!(prepare("   ", true, "#"), "");
        assert_eq!(prepare("", true, "#"), "");
    }

    #[test]
    fn hash_gram_is_deterministic_and_in_range() {
        let (i1, s1) = hash_gram(&SEED, "abc", 64);
        let (i2, s2) = hash_gram(&SEED, "abc", 64);
        assert_eq!((i1, s1), (i2, s2));
        assert!(i1 < 64);
        assert!(s1 == 1.0 || s1 == -1.0);
    }

    #[test]
    fn featurize_one_is_l2_unit_norm_for_nonempty() {
        let row = featurize_one("john smith", 256, 2, 3, true, "#", &SEED);
        let norm: f32 = row.iter().map(|v| v * v).sum::<f32>().sqrt();
        assert!((norm - 1.0).abs() < 1e-5, "expected unit norm, got {norm}");
    }

    #[test]
    fn featurize_one_empty_text_is_zero_vector() {
        let row = featurize_one("   ", 256, 2, 3, true, "#", &SEED);
        assert!(row.iter().all(|&v| v == 0.0));
    }

    #[test]
    fn project_one_is_l2_unit_norm_for_nonempty() {
        let dim = 4usize;
        let nf = 8usize;
        let w = vec![1.0f32; nf * dim];
        let out = project_one("john", &w, nf, dim, 2, 3, true, "#", &SEED);
        let norm: f32 = out.iter().map(|v| v * v).sum::<f32>().sqrt();
        assert!((norm - 1.0).abs() < 1e-5 || norm == 0.0, "got {norm}");
    }
}
