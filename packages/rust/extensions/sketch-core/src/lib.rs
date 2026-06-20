//! `goldenmatch-sketch-core`: pyo3-free MinHash + LSH sketching kernels.
//!
//! Shingling -> MinHash signatures -> banded LSH bucket hashes. The hash family
//! is hand-rolled and dependency-free so output is byte-identical with the
//! Python reference (`goldenmatch/core/sketch.py`) and the TypeScript port; the
//! committed `sketch_golden.json` fixture is the shared parity oracle.
//!
//! This crate does per-record sketching only. Grouping records by `(band,
//! bucket)` into blocks is the host language's job (Approach A).

pub mod hash;
pub mod lsh;
pub mod minhash;
pub mod shingle;
pub mod simhash;

pub use hash::{base_hash, splitmix64};
pub use lsh::{band_hashes, optimal_bands};
pub use minhash::{estimate_jaccard, signature};
pub use shingle::{shingle, ShingleMode};
pub use simhash::*;

use rayon::prelude::*;

/// Env knob: fan out batch sketching to rayon only at/above this row count.
/// Below it, run on the calling thread (the #688 `LockLatch` lesson — rayon is
/// pure overhead for small batches and can park the caller on some schedulers).
const RAYON_MIN_ROWS_DEFAULT: usize = 10_000;

fn rayon_min_rows() -> usize {
    std::env::var("GOLDENMATCH_NATIVE_SKETCH_RAYON_MIN_ROWS")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(RAYON_MIN_ROWS_DEFAULT)
}

/// End-to-end for one string: shingle -> signature -> band hashes.
pub fn sketch_band_hashes(
    text: &str,
    mode: ShingleMode,
    k: usize,
    num_perms: usize,
    num_bands: usize,
    seed: u64,
) -> Vec<u64> {
    band_hashes(
        &signature(&shingle(text, mode, k), num_perms, seed),
        num_bands,
    )
}

/// Per-record band hashes for many texts. Rayon-parallel at/above the row
/// threshold, calling-thread below.
pub fn band_hashes_batch(
    texts: &[String],
    mode: ShingleMode,
    k: usize,
    num_perms: usize,
    num_bands: usize,
    seed: u64,
) -> Vec<Vec<u64>> {
    let f = |t: &String| sketch_band_hashes(t, mode, k, num_perms, num_bands, seed);
    if texts.len() >= rayon_min_rows() {
        texts.par_iter().map(f).collect()
    } else {
        texts.iter().map(f).collect()
    }
}

/// Per-record MinHash signatures for many texts. Rayon-parallel at/above the row
/// threshold, calling-thread below.
pub fn signature_batch(
    texts: &[String],
    mode: ShingleMode,
    k: usize,
    num_perms: usize,
    seed: u64,
) -> Vec<Vec<u64>> {
    let f = |t: &String| signature(&shingle(t, mode, k), num_perms, seed);
    if texts.len() >= rayon_min_rows() {
        texts.par_iter().map(f).collect()
    } else {
        texts.iter().map(f).collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn end_to_end_golden() {
        assert_eq!(
            sketch_band_hashes("hello world", ShingleMode::Char, 3, 8, 4, 42),
            vec![
                12901963457859849374,
                4306753959614852008,
                8435817867480225113,
                7834504510243305493,
            ]
        );
    }

    #[test]
    fn batch_matches_singles_both_paths() {
        let texts: Vec<String> = (0..50).map(|i| format!("record number {i} here")).collect();
        let single: Vec<Vec<u64>> = texts
            .iter()
            .map(|t| sketch_band_hashes(t, ShingleMode::Word, 2, 16, 8, 3))
            .collect();
        // Sequential path (below threshold).
        std::env::set_var("GOLDENMATCH_NATIVE_SKETCH_RAYON_MIN_ROWS", "1000000");
        assert_eq!(
            band_hashes_batch(&texts, ShingleMode::Word, 2, 16, 8, 3),
            single
        );
        // Parallel path (force rayon).
        std::env::set_var("GOLDENMATCH_NATIVE_SKETCH_RAYON_MIN_ROWS", "0");
        assert_eq!(
            band_hashes_batch(&texts, ShingleMode::Word, 2, 16, 8, 3),
            single
        );
        std::env::remove_var("GOLDENMATCH_NATIVE_SKETCH_RAYON_MIN_ROWS");
    }
}
