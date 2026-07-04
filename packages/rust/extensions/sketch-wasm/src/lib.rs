//! wasm-bindgen wrapper over `sketch-core` (MinHash + LSH sketching), so the
//! JS/TS MinHash-LSH / SimHash blocker runs the SAME kernel as the Python
//! reference and the Rust core — byte-identical hash family, edge-safe (pure
//! wasm, no `node:*`). Mirrors the sibling `goldenhnsw-wasm` / `goldencheck-wasm`
//! shims.
//!
//! The 64-bit sketch hashes exceed JS's safe-integer range, so they cross the
//! boundary as `BigUint64Array` (`Vec<u64>` / `&[u64]`), never JSON — no
//! precision loss, matching the TS port's `bigint` arithmetic. `seed` is a
//! `u64` (JS `bigint`). Text/mode come in as strings; a bad `mode` is a
//! `JsError`.
//!
//! Only the per-record path is exposed; the caller loops over records host-side
//! (Approach A: grouping by `(band, bucket)` is the host language's job), so the
//! rayon batch path in sketch-core — and any wasm thread-pool concern — is never
//! touched.

use goldenmatch_sketch_core::{
    band_hashes as core_band_hashes, base_hash as core_base_hash, estimate_jaccard as core_jaccard,
    optimal_bands as core_optimal_bands, shingle as core_shingle, signature as core_signature,
    ShingleMode,
};
use wasm_bindgen::prelude::*;

fn parse_mode(mode: &str) -> Result<ShingleMode, JsError> {
    ShingleMode::parse(mode).ok_or_else(|| {
        JsError::new(&format!(
            "sketch-wasm: unknown shingle mode {mode:?} (want \"char\" or \"word\")"
        ))
    })
}

/// FNV-1a(64) + splitmix64 finalizer over `data` — the base hash the whole
/// sketch family is built on.
#[wasm_bindgen]
pub fn base_hash(data: &[u8]) -> u64 {
    core_base_hash(data)
}

/// Shingle `text` into the sorted, de-duplicated set of `u64` shingle hashes.
/// `mode` is `"char"` (k-grams of characters) or `"word"` (k-grams of words).
#[wasm_bindgen]
pub fn shingle(text: &str, mode: &str, k: usize) -> Result<Vec<u64>, JsError> {
    Ok(core_shingle(text, parse_mode(mode)?, k))
}

/// MinHash signature (`num_perms` values) of a shingle set, seeded by `seed`.
#[wasm_bindgen]
pub fn signature(shingles: &[u64], num_perms: usize, seed: u64) -> Vec<u64> {
    core_signature(shingles, num_perms, seed)
}

/// Banded LSH bucket hashes: one `u64` per band over a MinHash signature.
/// `sig.len()` must be divisible by `num_bands`.
#[wasm_bindgen]
pub fn band_hashes(sig: &[u64], num_bands: usize) -> Result<Vec<u64>, JsError> {
    if num_bands == 0 || !sig.len().is_multiple_of(num_bands) {
        return Err(JsError::new(&format!(
            "sketch-wasm: signature length {} not divisible by num_bands {}",
            sig.len(),
            num_bands
        )));
    }
    Ok(core_band_hashes(sig, num_bands))
}

/// End-to-end for one record: shingle -> MinHash signature -> band hashes.
/// Returns one bucket hash per band (`num_bands` values).
#[wasm_bindgen]
pub fn sketch_band_hashes(
    text: &str,
    mode: &str,
    k: usize,
    num_perms: usize,
    num_bands: usize,
    seed: u64,
) -> Result<Vec<u64>, JsError> {
    let mode = parse_mode(mode)?;
    let sig = core_signature(&core_shingle(text, mode, k), num_perms, seed);
    band_hashes(&sig, num_bands)
}

/// Per-record band hashes for many texts, as a FLAT `BigUint64Array` of length
/// `texts.len() * num_bands` (row-major). The caller reshapes into
/// `[texts.len()][num_bands]` and groups by `(band_idx, bucket)`. Runs on the
/// calling thread (no rayon), one record at a time.
#[wasm_bindgen]
pub fn sketch_band_hashes_batch(
    texts: Vec<String>,
    mode: &str,
    k: usize,
    num_perms: usize,
    num_bands: usize,
    seed: u64,
) -> Result<Vec<u64>, JsError> {
    let mode = parse_mode(mode)?;
    let mut out = Vec::with_capacity(texts.len().saturating_mul(num_bands));
    for text in &texts {
        let sig = core_signature(&core_shingle(text, mode, k), num_perms, seed);
        if num_bands == 0 || !sig.len().is_multiple_of(num_bands) {
            return Err(JsError::new(&format!(
                "sketch-wasm: signature length {} not divisible by num_bands {}",
                sig.len(),
                num_bands
            )));
        }
        out.extend(core_band_hashes(&sig, num_bands));
    }
    Ok(out)
}

/// Estimated Jaccard similarity of two MinHash signatures (fraction of equal
/// positions). Both must be the same length.
#[wasm_bindgen]
pub fn estimate_jaccard(sig_a: &[u64], sig_b: &[u64]) -> f64 {
    core_jaccard(sig_a, sig_b)
}

/// Optimal `(num_bands, rows_per_band)` for a target Jaccard `threshold` given
/// `num_perms`. Returned as a 2-element `Uint32Array` `[num_bands, rows]`.
#[wasm_bindgen]
pub fn optimal_bands(num_perms: usize, threshold: f64) -> Vec<u32> {
    let (bands, rows) = core_optimal_bands(num_perms, threshold);
    vec![bands as u32, rows as u32]
}
