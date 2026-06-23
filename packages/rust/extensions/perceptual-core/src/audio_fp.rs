//! Haitsma-Kalker-style robust audio fingerprint -- byte-identical to
//! `goldenmatch/core/perceptual.py::fingerprint_audio`.
//!
//! Frames the decoded mono PCM, computes log-spaced spectral-band energies via a
//! direct (partial) DFT, and emits one 32-bit sub-fingerprint per frame
//! transition:
//!
//! ```text
//! bit(n, m) = 1 if (E[n,m] - E[n,m+1]) - (E[n-1,m] - E[n-1,m+1]) > 0 else 0
//! ```
//!
//! for `m` in `0..31` (LSB-first). The signal is zero-padded to at least two
//! frames so at least one sub-fingerprint is always produced.

use std::f64::consts::PI;
use std::sync::OnceLock;

pub const AUDIO_FRAME: usize = 4096;
pub const AUDIO_HOP: usize = 2048;
pub const AUDIO_BANDS: usize = 33;
pub const AUDIO_F_MIN: f64 = 300.0;
pub const AUDIO_F_MAX: f64 = 2000.0;

/// Round half to even (Python `round`), matching the band-edge derivation in the
/// reference. The transcendental band frequencies never land exactly on `x.5`,
/// but the tie branch keeps the contract exact rather than probabilistic.
fn py_round(x: f64) -> f64 {
    let f = x.floor();
    let diff = x - f;
    if diff < 0.5 {
        f
    } else if diff > 0.5 {
        f + 1.0
    } else if (f as i64) % 2 == 0 {
        f
    } else {
        f + 1.0
    }
}

fn hann_window() -> &'static Vec<f64> {
    static H: OnceLock<Vec<f64>> = OnceLock::new();
    H.get_or_init(|| {
        let two_pi = 2.0 * PI;
        (0..AUDIO_FRAME)
            .map(|i| 0.5 - 0.5 * (two_pi * i as f64 / (AUDIO_FRAME as f64 - 1.0)).cos())
            .collect()
    })
}

/// DFT bin index of each of the `AUDIO_BANDS + 1` log-spaced band edges.
fn band_bins(sample_rate: u32) -> Vec<usize> {
    let ratio = AUDIO_F_MAX / AUDIO_F_MIN;
    (0..=AUDIO_BANDS)
        .map(|i| {
            let freq = AUDIO_F_MIN * ratio.powf(i as f64 / AUDIO_BANDS as f64);
            py_round(freq * AUDIO_FRAME as f64 / sample_rate as f64) as usize
        })
        .collect()
}

fn frame_band_energies(
    samples: &[f64],
    start: usize,
    bins: &[usize],
    cos_t: &[Vec<f64>],
    sin_t: &[Vec<f64>],
    hann: &[f64],
) -> Vec<f64> {
    let lo = bins[0];
    let frame: Vec<f64> = (0..AUDIO_FRAME).map(|i| hann[i] * samples[start + i]).collect();
    let mut mags = Vec::with_capacity(cos_t.len());
    for (rc, rs) in cos_t.iter().zip(sin_t.iter()) {
        let mut re = 0.0;
        let mut im = 0.0;
        for idx in 0..AUDIO_FRAME {
            let x = frame[idx];
            re += x * rc[idx];
            im += x * rs[idx];
        }
        mags.push(re * re + im * im);
    }
    (0..AUDIO_BANDS)
        .map(|m| {
            let mut acc = 0.0;
            for k in bins[m]..bins[m + 1] {
                acc += mags[k - lo];
            }
            acc
        })
        .collect()
}

/// Haitsma-Kalker-style robust audio fingerprint of decoded mono PCM. Returns one
/// 32-bit sub-fingerprint per frame transition (always at least one).
///
/// # Panics
/// Panics if `sample_rate` is 0 (mirrors the Python reference raising `ValueError`).
pub fn fingerprint_audio(samples: &[f64], sample_rate: u32) -> Vec<u32> {
    assert!(sample_rate > 0, "sample_rate must be positive");
    let min_len = AUDIO_FRAME + AUDIO_HOP; // guarantees >= 2 frames
    let mut data = samples.to_vec();
    if data.len() < min_len {
        data.resize(min_len, 0.0);
    }
    let n_frames = 1 + (data.len() - AUDIO_FRAME) / AUDIO_HOP;

    let bins = band_bins(sample_rate);
    let lo = bins[0];
    let hi = bins[AUDIO_BANDS];
    let hann = hann_window();
    let two_pi = 2.0 * PI;
    let mut cos_t = Vec::with_capacity(hi - lo);
    let mut sin_t = Vec::with_capacity(hi - lo);
    for k in lo..hi {
        let ang = -two_pi * k as f64 / AUDIO_FRAME as f64;
        cos_t.push((0..AUDIO_FRAME).map(|idx| (ang * idx as f64).cos()).collect::<Vec<f64>>());
        sin_t.push((0..AUDIO_FRAME).map(|idx| (ang * idx as f64).sin()).collect::<Vec<f64>>());
    }

    let mut prev = frame_band_energies(&data, 0, &bins, &cos_t, &sin_t, hann);
    let mut out = Vec::with_capacity(n_frames - 1);
    for f in 1..n_frames {
        let cur = frame_band_energies(&data, f * AUDIO_HOP, &bins, &cos_t, &sin_t, hann);
        let mut word = 0u32;
        for m in 0..AUDIO_BANDS - 1 {
            let d = (cur[m] - cur[m + 1]) - (prev[m] - prev[m + 1]);
            if d > 0.0 {
                word |= 1u32 << m;
            }
        }
        out.push(word);
        prev = cur;
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn short_signal_pads_to_one_subfingerprint() {
        let fp = fingerprint_audio(&[0.0; 100], 44100);
        assert_eq!(fp.len(), 1);
    }
}
