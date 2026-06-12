//! Pyo3-free aggregation kernels for GoldenAnalysis.
//!
//! Byte-identical Rust mirror of the pure-Python loops in
//! `goldenanalysis/core/aggregate.py` (`histogram`, `quantile`). The pure-Python
//! path stays the reference + fallback; these kernels back the optional
//! `analysis-native` abi3 wheel. No pyo3, no Arrow -- plain slices in, plain
//! values out -- so the same logic can later back a SQL surface.
//!
//! Inputs are assumed finite (cluster sizes / scores). NaN/inf are out of the
//! parity contract: the Python reference (`min`/`max`/`sorted`) is undefined on
//! them too.

/// Equal-width histogram over `[min, max]`, mirroring `aggregate.histogram`.
///
/// Returns `[(left_edge, count), ...]` with `bins` entries; the right edge is
/// inclusive (the max lands in the last bin); all-equal input collapses to a
/// single `[(value, count)]` bin; empty input or `bins < 1` => `[]`. Uses the
/// SAME float op order as the Python loop so the edges + bucket assignment are
/// bit-identical.
pub fn histogram(values: &[f64], bins: i64) -> Vec<(f64, i64)> {
    if values.is_empty() || bins < 1 {
        return Vec::new();
    }
    let bins = bins as usize;
    let mut lo = values[0];
    let mut hi = values[0];
    for &v in values {
        if v < lo {
            lo = v;
        }
        if v > hi {
            hi = v;
        }
    }
    if hi == lo {
        return vec![(lo, values.len() as i64)];
    }
    let width = (hi - lo) / bins as f64;
    let mut counts = vec![0i64; bins];
    for &v in values {
        // int((v - lo) / width): truncates toward zero == Python int() for >= 0.
        let mut idx = ((v - lo) / width) as usize;
        if idx >= bins {
            idx = bins - 1; // right-edge inclusive
        }
        counts[idx] += 1;
    }
    (0..bins).map(|i| (lo + i as f64 * width, counts[i])).collect()
}

/// Linear-interpolation quantile (numpy default), mirroring `aggregate.quantile`.
///
/// Empty input => `0.0`. Single value => that value. Otherwise interpolates
/// between the two order statistics straddling `q*(n-1)`, the same op order as
/// the Python loop. Inputs assumed finite.
pub fn quantile(values: &[f64], q: f64) -> f64 {
    if values.is_empty() {
        return 0.0;
    }
    let mut vals = values.to_vec();
    vals.sort_by(|a, b| a.total_cmp(b));
    if vals.len() == 1 {
        return vals[0];
    }
    let pos = q * (vals.len() - 1) as f64;
    let lo_idx = pos as usize; // int(pos); pos >= 0 for q in [0, 1]
    let frac = pos - lo_idx as f64;
    if lo_idx + 1 < vals.len() {
        vals[lo_idx] + (vals[lo_idx + 1] - vals[lo_idx]) * frac
    } else {
        vals[lo_idx]
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn histogram_empty_or_no_bins() {
        assert_eq!(histogram(&[], 10), Vec::new());
        assert_eq!(histogram(&[1.0, 2.0], 0), Vec::new());
        assert_eq!(histogram(&[1.0, 2.0], -1), Vec::new());
    }

    #[test]
    fn histogram_all_equal_collapses() {
        assert_eq!(histogram(&[5.0], 3), vec![(5.0, 1)]);
        assert_eq!(histogram(&[2.0, 2.0, 2.0], 4), vec![(2.0, 3)]);
    }

    #[test]
    fn histogram_right_edge_inclusive() {
        // 0..=10 over 10 bins (width 1): the max (10) lands in the last bin with 9.
        let vals: Vec<f64> = (0..=10).map(|i| i as f64).collect();
        let got = histogram(&vals, 10);
        let expected: Vec<(f64, i64)> = (0..10)
            .map(|i| (i as f64, if i == 9 { 2 } else { 1 }))
            .collect();
        assert_eq!(got, expected);
    }

    #[test]
    fn quantile_edges_and_interpolation() {
        // cluster sizes [1, 1, 3, 2] -> sorted [1, 1, 2, 3]
        let sizes = [1.0, 1.0, 3.0, 2.0];
        assert_eq!(quantile(&sizes, 0.5), 1.5); // 1 + (2-1)*0.5
        assert!((quantile(&sizes, 0.95) - 2.85).abs() < 1e-12); // 2 + (3-2)*0.85
        assert_eq!(quantile(&sizes, 0.0), 1.0); // min
        assert_eq!(quantile(&sizes, 1.0), 3.0); // max
    }

    #[test]
    fn quantile_empty_and_single() {
        assert_eq!(quantile(&[], 0.5), 0.0);
        assert_eq!(quantile(&[7.0], 0.5), 7.0);
    }
}
