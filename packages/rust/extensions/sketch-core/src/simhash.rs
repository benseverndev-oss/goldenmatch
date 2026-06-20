//! SimHash (random ±1 hyperplane) LSH over f64 vectors. Byte-identical with
//! the Python reference (core/sketch.py) and TS port; see the #1082 spec.
//!
//! SimHash buckets DENSE embedding-style vectors (one bit per random hyperplane),
//! complementing MinHash's bucketing of SPARSE shingle sets. Both share the hash
//! family in `crate::hash`, and band hashes use the identical byte layout
//! (`u64` band-index prefix + per-element bytes).

use crate::hash::{base_hash, splitmix64};
use rayon::prelude::*;

/// Env knob: fan out batch SimHash projection to rayon only at/above this row
/// count. Below it, run on the calling thread (the #688 `LockLatch` lesson —
/// rayon is pure overhead for small batches and can park the caller on some
/// schedulers). Shares the knob with the MinHash batch path.
const RAYON_MIN_ROWS_DEFAULT: usize = 10_000;

fn rayon_min_rows() -> usize {
    std::env::var("GOLDENMATCH_NATIVE_SKETCH_RAYON_MIN_ROWS")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(RAYON_MIN_ROWS_DEFAULT)
}

/// LSB-first bitstream over a splitmix64 keystream. Draws one ±1 Rademacher
/// entry per bit, refilling a 64-bit buffer from the stream when exhausted.
struct BitStream {
    state: u64,
    buf: u64,
    left: u32,
}

impl BitStream {
    fn new(seed: u64) -> Self {
        Self {
            state: seed,
            buf: 0,
            left: 0,
        }
    }

    #[inline]
    fn draw_pm1(&mut self) -> f64 {
        if self.left == 0 {
            let (v, s) = splitmix64(self.state);
            self.buf = v;
            self.state = s;
            self.left = 64;
        }
        let bit = self.buf & 1;
        self.buf >>= 1;
        self.left -= 1;
        if bit == 1 {
            1.0
        } else {
            -1.0
        }
    }
}

/// Row-major `num_planes x dim` Rademacher (±1) projection matrix from a
/// splitmix64 bitstream seeded at `seed`. Draw order: plane 0 col 0..dim,
/// plane 1 col 0..dim, ...
fn projection_matrix(num_planes: usize, dim: usize, seed: u64) -> Vec<Vec<f64>> {
    let mut bs = BitStream::new(seed);
    (0..num_planes)
        .map(|_| (0..dim).map(|_| bs.draw_pm1()).collect())
        .collect()
}

/// Project one vector through a prebuilt projection matrix into a 0/1 signature.
/// `sig[i] = 1` iff plane `i`'s dot product is `>= 0.0` (tie, incl. the all-zero
/// vector, resolves to 1). Dot sums `j` ascending in f64.
#[inline]
fn project(planes: &[Vec<f64>], vector: &[f64]) -> Vec<u8> {
    planes
        .iter()
        .map(|row| {
            let mut dot = 0.0_f64;
            for j in 0..vector.len() {
                dot += row[j] * vector[j];
            }
            if dot >= 0.0 {
                1u8
            } else {
                0u8
            }
        })
        .collect()
}

/// SimHash signature: one byte (0/1) per plane. Empty/zero vector -> all ones.
pub fn simhash_signature(vector: &[f64], num_planes: usize, seed: u64) -> Vec<u8> {
    let planes = projection_matrix(num_planes, vector.len(), seed);
    project(&planes, vector)
}

/// Banded LSH over the 0/1 signature bytes.
///
/// # Panics
/// Panics if `num_bands == 0` or `sig.len()` is not divisible by `num_bands`.
pub fn simhash_band_hashes(sig: &[u8], num_bands: usize) -> Vec<u64> {
    let n = sig.len();
    assert!(
        num_bands > 0 && n.is_multiple_of(num_bands),
        "num_planes {n} not divisible by num_bands {num_bands}"
    );
    let r = n / num_bands;
    (0..num_bands)
        .map(|b| {
            let mut buf = Vec::with_capacity(8 + r);
            buf.extend_from_slice(&(b as u64).to_le_bytes());
            buf.extend_from_slice(&sig[b * r..(b + 1) * r]);
            base_hash(&buf)
        })
        .collect()
}

/// Per-record SimHash band hashes for many vectors. The projection matrix is
/// built ONCE per `(seed, dim, num_planes)` and reused across all rows. Rayon-
/// parallel at/above the row threshold, calling-thread below.
pub fn simhash_band_hashes_batch(
    vectors: &[Vec<f64>],
    num_planes: usize,
    num_bands: usize,
    seed: u64,
) -> Vec<Vec<u64>> {
    if vectors.is_empty() {
        return Vec::new();
    }
    let dim = vectors[0].len();
    let planes = projection_matrix(num_planes, dim, seed);
    let f = |v: &Vec<f64>| simhash_band_hashes(&project(&planes, v), num_bands);
    if vectors.len() >= rayon_min_rows() {
        vectors.par_iter().map(f).collect()
    } else {
        vectors.iter().map(f).collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // Fixed mixed-sign dense vector shared across the golden constants.
    const V: [f64; 8] = [0.5, -0.3, 0.8, 0.1, -0.9, 0.4, -0.2, 0.7];

    #[test]
    fn signature_v_planes8_seed42_golden() {
        assert_eq!(simhash_signature(&V, 8, 42), vec![1, 1, 1, 1, 1, 0, 1, 1]);
    }

    #[test]
    fn signature_v_planes16_seed7_golden() {
        assert_eq!(
            simhash_signature(&V, 16, 7),
            vec![1, 1, 0, 0, 1, 1, 1, 0, 0, 1, 0, 1, 1, 0, 1, 1]
        );
    }

    #[test]
    fn band_hashes_golden() {
        let sig = vec![1u8, 1, 1, 1, 1, 0, 1, 1];
        assert_eq!(
            simhash_band_hashes(&sig, 4),
            vec![
                8326405673782927272,
                10087387020540333614,
                407431194778926956,
                13491348438230804516,
            ]
        );
    }

    #[test]
    fn zero_vector_is_all_ones() {
        // Every dot is exactly 0.0; the tie (dot >= 0.0) resolves to 1.
        assert_eq!(simhash_signature(&[0.0; 8], 8, 42), vec![1u8; 8]);
    }

    #[test]
    fn empty_vector_is_all_ones() {
        // No dimensions -> every plane's dot is the empty sum 0.0 -> tie -> 1.
        assert_eq!(simhash_signature(&[], 4, 1), vec![1u8; 4]);
    }

    #[test]
    #[should_panic(expected = "not divisible")]
    fn band_hashes_non_divisible_panics() {
        simhash_band_hashes(&[1, 0, 1, 1, 0, 1, 0, 1], 3);
    }

    #[test]
    fn batch_matches_singles_both_paths() {
        let vectors: Vec<Vec<f64>> = vec![
            V.to_vec(),
            vec![0.0; 8],
            vec![-1.0, 2.0, -3.0, 4.0, -5.0, 6.0, -7.0, 8.0],
        ];
        let single: Vec<Vec<u64>> = vectors
            .iter()
            .map(|v| simhash_band_hashes(&simhash_signature(v, 8, 42), 4))
            .collect();
        // Sequential path (below threshold).
        std::env::set_var("GOLDENMATCH_NATIVE_SKETCH_RAYON_MIN_ROWS", "1000000");
        assert_eq!(simhash_band_hashes_batch(&vectors, 8, 4, 42), single);
        // Parallel path (force rayon).
        std::env::set_var("GOLDENMATCH_NATIVE_SKETCH_RAYON_MIN_ROWS", "0");
        assert_eq!(simhash_band_hashes_batch(&vectors, 8, 4, 42), single);
        std::env::remove_var("GOLDENMATCH_NATIVE_SKETCH_RAYON_MIN_ROWS");
    }

    #[test]
    fn batch_empty_is_empty() {
        let empty: Vec<Vec<f64>> = Vec::new();
        assert_eq!(
            simhash_band_hashes_batch(&empty, 8, 4, 42),
            Vec::<Vec<u64>>::new()
        );
    }
}
