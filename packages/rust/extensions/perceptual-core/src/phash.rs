//! DCT perceptual image hash (pHash) -- byte-identical to
//! `goldenmatch/core/perceptual.py::phash_image`.
//!
//! Pipeline: align-corners bilinear resize to 32x32 -> direct 2D DCT-II -> take
//! the 8x8 low-frequency block -> threshold each coefficient against the median
//! of the 64 coefficients. Bit `i = row*8 + col` (LSB-first) is set when the
//! coefficient strictly exceeds the median; exact ties resolve to 0.

use std::f64::consts::PI;
use std::sync::OnceLock;

pub const IMG_RESIZE: usize = 32;
pub const HASH_SIZE: usize = 8;

/// Unnormalized DCT-II basis: `M[k][i] = cos(pi * (i + 0.5) * k / n)`.
fn dct_basis(n: usize) -> Vec<Vec<f64>> {
    (0..n)
        .map(|k| {
            (0..n)
                .map(|i| (PI * (i as f64 + 0.5) * k as f64 / n as f64).cos())
                .collect()
        })
        .collect()
}

fn dct_m() -> &'static Vec<Vec<f64>> {
    static M: OnceLock<Vec<Vec<f64>>> = OnceLock::new();
    M.get_or_init(|| dct_basis(IMG_RESIZE))
}

/// Source sample coordinates for an align-corners resize of a length-`n`
/// dimension to `size` outputs: `(i0, i1, weight)` per output index.
fn src_coords(n: usize, size: usize) -> Vec<(usize, usize, f64)> {
    let denom = if size > 1 { size - 1 } else { 1 };
    (0..size)
        .map(|o| {
            if n == 1 {
                (0usize, 0usize, 0.0f64)
            } else {
                let s = (o * (n - 1)) as f64 / denom as f64;
                let mut i0 = s.floor() as usize;
                if i0 >= n - 1 {
                    i0 = n - 2;
                }
                (i0, i0 + 1, s - i0 as f64)
            }
        })
        .collect()
}

pub(crate) fn bilinear_resize(grid: &[Vec<f64>], size: usize) -> Vec<Vec<f64>> {
    let h = grid.len();
    let w = grid[0].len();
    let ys = src_coords(h, size);
    let xs = src_coords(w, size);
    let mut out = vec![vec![0.0f64; size]; size];
    for (oy, &(y0, y1, wy)) in ys.iter().enumerate() {
        for (ox, &(x0, x1, wx)) in xs.iter().enumerate() {
            let top = grid[y0][x0] * (1.0 - wx) + grid[y0][x1] * wx;
            let bot = grid[y1][x0] * (1.0 - wx) + grid[y1][x1] * wx;
            out[oy][ox] = top * (1.0 - wy) + bot * wy;
        }
    }
    out
}

/// 2D separable DCT-II of `block` (size x size); rows first, then columns (the
/// fixed order the Python reference uses). Returns the top-left `keep` x `keep`.
fn dct2_topleft(block: &[Vec<f64>], size: usize, keep: usize, m: &[Vec<f64>]) -> Vec<Vec<f64>> {
    let mut tmp = vec![vec![0.0f64; keep]; size];
    for i in 0..size {
        for k in 0..keep {
            let mut acc = 0.0;
            for x in 0..size {
                acc += block[i][x] * m[k][x];
            }
            tmp[i][k] = acc;
        }
    }
    let mut out = vec![vec![0.0f64; keep]; keep];
    for l in 0..keep {
        for k in 0..keep {
            let mut acc = 0.0;
            for y in 0..size {
                acc += tmp[y][l] * m[k][y];
            }
            out[k][l] = acc;
        }
    }
    out
}

/// 64-bit DCT perceptual hash of a decoded luma grid (rows of grayscale values).
///
/// # Panics
/// Panics if the grid is empty or its first row is empty (mirrors the Python
/// reference raising `ValueError`).
pub fn phash_image(grid: &[Vec<f64>]) -> u64 {
    assert!(
        !grid.is_empty() && !grid[0].is_empty(),
        "luma grid must be non-empty"
    );
    let small = bilinear_resize(grid, IMG_RESIZE);
    let block = dct2_topleft(&small, IMG_RESIZE, HASH_SIZE, dct_m());

    let mut coeffs = Vec::with_capacity(HASH_SIZE * HASH_SIZE);
    for row in block.iter().take(HASH_SIZE) {
        for &v in row.iter().take(HASH_SIZE) {
            coeffs.push(v);
        }
    }
    let mut ordered = coeffs.clone();
    ordered.sort_by(|a, b| a.partial_cmp(b).expect("no NaN coefficients"));
    let n = ordered.len();
    let median = (ordered[n / 2 - 1] + ordered[n / 2]) / 2.0;

    let mut h = 0u64;
    for (i, &v) in coeffs.iter().enumerate() {
        if v > median {
            h |= 1u64 << i;
        }
    }
    h
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn distinct_grids_differ() {
        let a = vec![vec![0.0f64; 16]; 16];
        let mut b = vec![vec![0.0f64; 16]; 16];
        for (y, row) in b.iter_mut().enumerate() {
            for (x, v) in row.iter_mut().enumerate() {
                *v = ((x + y) % 256) as f64;
            }
        }
        assert_ne!(phash_image(&a), phash_image(&b));
    }
}
