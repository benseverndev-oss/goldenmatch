//! Pyo3-free graph kernels. Behavior-exact extraction of the loops that lived in
//! `native/src/{cluster,pairs}.rs`; the `native` crate keeps thin `#[pyfunction]`
//! shims delegating here (one source of truth, like `score-core`).
use std::collections::BTreeMap;

/// Canonicalize each pair as `(min,max)` and keep the max score per pair.
/// Behavior-exact port of `native::pairs::dedup_pairs_max_score`.
pub fn dedup_pairs_max_score(pairs: &[(i64, i64, f64)]) -> Vec<(i64, i64, f64)> {
    let mut best: BTreeMap<(i64, i64), f64> = BTreeMap::new();
    for &(a, b, s) in pairs {
        let key = if a <= b { (a, b) } else { (b, a) };
        match best.get(&key) {
            Some(&cur) if s <= cur => {}
            _ => {
                best.insert(key, s);
            }
        }
    }
    best.into_iter().map(|((a, b), s)| (a, b, s)).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn dedup_keeps_max_and_canonicalizes() {
        let got = dedup_pairs_max_score(&[(2, 1, 0.5), (1, 2, 0.9), (3, 3, 0.1)]);
        assert_eq!(got, vec![(1, 2, 0.9), (3, 3, 0.1)]);
    }
}
