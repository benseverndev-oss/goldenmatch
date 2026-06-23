//! Rotation/crop-aware radial-variance image feature -- byte-identical to
//! `goldenmatch/core/perceptual.py::radial_variance` (ADR 0022, finding 1).
//!
//! pHash is photometric, not geometric (0.0 recall on rotation/crop). The radial-
//! variance profile is the geometric counterpart: for each of `RADIAL_ANGLES`
//! lines through the center of an align-corners resize, the variance of the luma
//! sampled along that line. Rotation cyclically shifts this profile; the Python
//! `radial_align_similarity` searches that shift (the scoring-side compare stays
//! in Python, like `audio_ber_aligned` -- only the profile is the parity contract).
//!
//! The transform is direct (resize + nearest-neighbour sampling via the same
//! banker's-rounding the audio band edges use), so the f64 operation order matches
//! the Python reference exactly and the profile is bit-identical on a shared libm.

use std::f64::consts::PI;

use crate::audio_fp::py_round;
use crate::phash::bilinear_resize;

pub const RADIAL_RESIZE: usize = 32;
pub const RADIAL_ANGLES: usize = 48;

/// Per-angle pixel-variance profile of a decoded luma grid.
///
/// # Panics
/// Panics if the grid is empty or its first row is empty (mirrors the Python
/// reference raising `ValueError`).
pub fn radial_variance(grid: &[Vec<f64>]) -> Vec<f64> {
    assert!(
        !grid.is_empty() && !grid[0].is_empty(),
        "luma grid must be non-empty"
    );
    let small = bilinear_resize(grid, RADIAL_RESIZE);
    let n = RADIAL_RESIZE;
    let center = (n as f64 - 1.0) / 2.0;
    // steps = i * 0.5 for i in -2n..=2n (covers the full diagonal extent)
    let bound = 2 * n as isize;
    let steps: Vec<f64> = (-bound..=bound).map(|i| i as f64 * 0.5).collect();

    let mut profile = Vec::with_capacity(RADIAL_ANGLES);
    for line in 0..RADIAL_ANGLES {
        let theta = PI * line as f64 / RADIAL_ANGLES as f64;
        let cos_t = theta.cos();
        let sin_t = theta.sin();
        let mut vals: Vec<f64> = Vec::new();
        for &t in &steps {
            let x = py_round(center + t * cos_t) as isize;
            let y = py_round(center + t * sin_t) as isize;
            if x >= 0 && (x as usize) < n && y >= 0 && (y as usize) < n {
                vals.push(small[y as usize][x as usize]);
            }
        }
        if vals.len() < 2 {
            profile.push(0.0);
            continue;
        }
        let mean = vals.iter().sum::<f64>() / vals.len() as f64;
        // Faithful translation of CPython's `sum((v-mean)*(v-mean) for v in vals)`:
        // each squared deviation is a distinct rounded float, then summed left to
        // right. Materialising the squares (rather than `.map().sum()`) keeps the
        // multiply and the accumulating add from ever contracting into one fused
        // mul-add, so the result is bit-identical to the reference (the golden
        // fixture is the bit-exact check).
        let squares: Vec<f64> = vals.iter().map(|&v| (v - mean) * (v - mean)).collect();
        let var = squares.iter().sum::<f64>() / vals.len() as f64;
        profile.push(var);
    }
    profile
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn profile_has_one_value_per_angle_and_is_nonnegative() {
        let grid: Vec<Vec<f64>> = (0..40)
            .map(|y| (0..40).map(|x| ((x * 7 + y * 3) % 256) as f64).collect())
            .collect();
        let p = radial_variance(&grid);
        assert_eq!(p.len(), RADIAL_ANGLES);
        assert!(p.iter().all(|&v| v >= 0.0));
    }
}
