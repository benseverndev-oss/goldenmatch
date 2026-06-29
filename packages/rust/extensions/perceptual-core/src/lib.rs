//! `goldenmatch-perceptual-core`: pyo3-free deterministic perceptual media hashes.
//!
//! The crawl tier of multimodal entity resolution (ADR 0022). Two in-house,
//! deterministic hashes, each reduced to a bit-string compared by hamming
//! distance:
//!
//! - **Image** — a 64-bit DCT perceptual hash (pHash) over a decoded luma grid.
//! - **Audio** — a Haitsma-Kalker-style robust fingerprint: a sequence of 32-bit
//!   sub-fingerprints over log-spaced spectral bands, over decoded mono PCM.
//!
//! This crate is the byte-for-byte counterpart of the authoritative Python
//! reference (`goldenmatch/core/perceptual.py`); the committed
//! `perceptual_golden.json` fixture is the shared parity oracle. The transforms
//! are direct (no FFT) so the floating-point operation order matches the Python
//! reference exactly — on a shared libm (Linux CI) the transcendental results are
//! bit-identical, and the hash thresholds therefore agree.
//!
//! The kernel operates on *decoded* input (luma grid / mono PCM) by design, so it
//! stays codec-free and parity-clean; format decoding is an upstream adapter.

pub mod audio_fp;
pub mod phash;
pub mod radial;
mod tables;

pub use audio_fp::{
    fingerprint_audio, AUDIO_BANDS, AUDIO_FRAME, AUDIO_F_MAX, AUDIO_F_MIN, AUDIO_HOP,
};
pub use phash::{phash_image, HASH_SIZE, IMG_RESIZE};
pub use radial::{radial_variance, RADIAL_ANGLES, RADIAL_RESIZE};

/// Hamming distance between two equal-width bit-packed hashes.
pub fn hamming(a: u64, b: u64) -> u32 {
    (a ^ b).count_ones()
}

/// Bit-error-rate between two audio fingerprints, frame-aligned over the shorter
/// length. 0.0 == identical, ~0.5 == unrelated. Empty inputs -> 1.0. Mirrors
/// `perceptual.audio_ber`.
pub fn audio_ber(a: &[u32], b: &[u32]) -> f64 {
    let n = a.len().min(b.len());
    if n == 0 {
        return 1.0;
    }
    let mut bits = 0u32;
    for i in 0..n {
        bits += (a[i] ^ b[i]).count_ones();
    }
    f64::from(bits) / (n as f64 * (AUDIO_BANDS as f64 - 1.0))
}
