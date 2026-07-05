//! Owned fuzzy category-autocorrect kernel (pyo3-free). The reference
//! implementation; the Python (`goldenflow/transforms/auto_correct.py`) and TS
//! (`transforms/auto-correct.ts`) surfaces conform to its output byte-for-byte.
//!
//! `category_auto_correct` is data-dependent: it builds a correction map from a
//! column's value frequencies, then applies it. This kernel owns the WHOLE
//! map-building algorithm (frequency -> canonical -> fuzzy) so the corrections
//! are identical across surfaces. The host computes `value_counts` and applies
//! the returned map; only the algorithm lives here.
//!
//! `fuzz_ratio` is the rapidfuzz `fuzz.ratio` primitive (normalized Indel /
//! LCS similarity), replacing the pure-Python `rapidfuzz` call and the (wrongly
//! divergent) Levenshtein-based TS ratio.

use std::collections::HashMap;

/// Length of the longest common subsequence of `a` and `b` (over chars).
fn lcs_len(a: &[char], b: &[char]) -> usize {
    let n = b.len();
    if a.is_empty() || n == 0 {
        return 0;
    }
    // Single-row DP.
    let mut prev = vec![0usize; n + 1];
    for &ca in a {
        let mut prev_diag = 0usize;
        let mut row = vec![0usize; n + 1];
        for j in 1..=n {
            row[j] = if ca == b[j - 1] {
                prev_diag + 1
            } else {
                prev[j].max(row[j - 1])
            };
            prev_diag = prev[j];
        }
        prev = row;
    }
    prev[n]
}

/// rapidfuzz `fuzz.ratio`: `100 * (1 - indel/(len_a+len_b))` where
/// `indel = len_a + len_b - 2*LCS`. Two empty strings -> 100. Operates on
/// chars (codepoints), matching rapidfuzz on Python `str`.
pub fn fuzz_ratio(a: &str, b: &str) -> f64 {
    let ca: Vec<char> = a.chars().collect();
    let cb: Vec<char> = b.chars().collect();
    let la = ca.len();
    let lb = cb.len();
    if la == 0 && lb == 0 {
        return 100.0;
    }
    let lcs = lcs_len(&ca, &cb);
    let total = (la + lb) as f64;
    let indel = (la + lb - 2 * lcs) as f64;
    100.0 * (1.0 - indel / total)
}

/// An insertion-ordered accumulator: keeps keys in first-seen order (mirrors a
/// Python dict / `Counter`) while allowing O(1)-ish count updates.
struct OrderedCounts {
    order: Vec<String>,
    idx: HashMap<String, usize>,
    counts: Vec<i64>,
}

impl OrderedCounts {
    fn new() -> Self {
        Self {
            order: Vec::new(),
            idx: HashMap::new(),
            counts: Vec::new(),
        }
    }

    fn add(&mut self, key: &str, count: i64) {
        if let Some(&i) = self.idx.get(key) {
            self.counts[i] += count;
        } else {
            let i = self.order.len();
            self.idx.insert(key.to_string(), i);
            self.order.push(key.to_string());
            self.counts.push(count);
        }
    }
}

/// Build the variant->canonical correction map from `(value, count)` pairs
/// (typically a `value_counts(sort=True)` result). Byte-identical to
/// `auto_correct.py::_build_canonical_map`. Returns `(from_casing,
/// to_canonical)` pairs (keyed by the STRIPPED original casing).
///
/// Order-deterministic: keys are processed in the input order (= value_counts
/// order), `most_common`/`best-score` ties resolve to the FIRST (insertion
/// order), exactly like Python's `Counter.most_common` + `score > best`.
pub fn build_canonical_map(
    values: &[Option<&str>],
    counts: &[i64],
    freq_threshold: f64,
    match_threshold: f64,
) -> Vec<(String, String)> {
    // lowercase -> total count (insertion-ordered), and lowercase -> ordered
    // list of (original casing, count).
    let mut lower = OrderedCounts::new();
    let mut case_map: HashMap<String, Vec<(String, i64)>> = HashMap::new();

    for (v_opt, &count) in values.iter().zip(counts.iter()) {
        let Some(v) = v_opt else { continue };
        let v_stripped = v.trim();
        if v_stripped.is_empty() {
            continue;
        }
        let low = v_stripped.to_lowercase();
        lower.add(&low, count);
        let casings = case_map.entry(low).or_default();
        if let Some(entry) = casings.iter_mut().find(|(c, _)| c == v_stripped) {
            entry.1 += count;
        } else {
            casings.push((v_stripped.to_string(), count));
        }
    }

    let total: i64 = lower.counts.iter().sum();
    if total == 0 {
        return Vec::new();
    }

    // Canonical determination (in insertion order).
    let mut canonical: HashMap<String, String> = HashMap::new(); // low -> best casing
    let mut canonical_order: Vec<String> = Vec::new();
    let mut low_freq: Vec<String> = Vec::new();

    for (i, low) in lower.order.iter().enumerate() {
        let count = lower.counts[i];
        if (count as f64 / total as f64) >= freq_threshold {
            // most_common(1): highest count; tie -> first (insertion order).
            let casings = &case_map[low];
            let mut best_casing = &casings[0].0;
            let mut best_count = casings[0].1;
            for (casing, c) in &casings[1..] {
                if *c > best_count {
                    best_count = *c;
                    best_casing = casing;
                }
            }
            canonical.insert(low.clone(), best_casing.clone());
            canonical_order.push(low.clone());
        } else {
            low_freq.push(low.clone());
        }
    }

    let mut corrections: Vec<(String, String)> = Vec::new();

    // Exact case-insensitive corrections (non-best casings -> best casing).
    for low in &canonical_order {
        let best = &canonical[low];
        for (casing, _) in &case_map[low] {
            if casing != best {
                corrections.push((casing.clone(), best.clone()));
            }
        }
    }

    // Fuzzy corrections for low-frequency values.
    for low in &low_freq {
        let mut best_score = 0.0f64;
        let mut best_match: Option<&String> = None;
        for canon_low in &canonical_order {
            let score = fuzz_ratio(low, canon_low);
            if score > best_score {
                best_score = score;
                best_match = Some(canon_low);
            }
        }
        if best_score >= match_threshold {
            if let Some(bm) = best_match {
                let canon_casing = &canonical[bm];
                for (casing, _) in &case_map[low] {
                    corrections.push((casing.clone(), canon_casing.clone()));
                }
            }
        }
    }

    corrections
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fuzz_ratio_matches_rapidfuzz() {
        // Pinned against Python rapidfuzz.fuzz.ratio.
        assert!((fuzz_ratio("active", "actve") - 90.9090909090909).abs() < 1e-9);
        assert!((fuzz_ratio("aaa", "aa") - 80.0).abs() < 1e-9);
        assert!((fuzz_ratio("kitten", "sitting") - 61.53846153846154).abs() < 1e-9);
        assert_eq!(fuzz_ratio("abc", ""), 0.0);
        assert_eq!(fuzz_ratio("", ""), 100.0);
        assert_eq!(fuzz_ratio("abc", "abc"), 100.0);
    }

    fn corrections_map(pairs: &[(Option<&str>, i64)], ft: f64, mt: f64) -> HashMap<String, String> {
        let values: Vec<Option<&str>> = pairs.iter().map(|(v, _)| *v).collect();
        let counts: Vec<i64> = pairs.iter().map(|(_, c)| *c).collect();
        build_canonical_map(&values, &counts, ft, mt)
            .into_iter()
            .collect()
    }

    #[test]
    fn build_map_case_and_fuzzy() {
        // "active" dominant; "Active"/"ACTIVE" are case variants; "actve" is a
        // low-freq typo (fuzzy match); "banana" is unrelated (no match).
        let m = corrections_map(
            &[
                (Some("active"), 50),
                (Some("Active"), 10),
                (Some("ACTIVE"), 5),
                (Some("actve"), 2),
                (Some("banana"), 1),
            ],
            0.05,
            85.0,
        );
        // canonical low "active" -> best casing "active" (highest count).
        assert_eq!(m.get("Active").map(String::as_str), Some("active"));
        assert_eq!(m.get("ACTIVE").map(String::as_str), Some("active"));
        // "actve" fuzzy-matches "active" (90.9 >= 85) -> "active".
        assert_eq!(m.get("actve").map(String::as_str), Some("active"));
        // "banana" is below threshold vs "active" -> no correction.
        assert!(!m.contains_key("banana"));
        // the canonical best casing itself is not a correction key.
        assert!(!m.contains_key("active"));
    }

    #[test]
    fn build_map_empty_and_whitespace() {
        // None / empty / whitespace-only inputs are skipped; no canonicals -> {}.
        let m = corrections_map(&[(None, 3), (Some(""), 2), (Some("   "), 1)], 0.05, 85.0);
        assert!(m.is_empty());
    }

    #[test]
    fn build_map_no_canonical_when_all_below_threshold() {
        // All values equally rare (each 20%): with freq_threshold 0.25 none is
        // canonical -> no corrections.
        let m = corrections_map(
            &[
                (Some("aa"), 1),
                (Some("bb"), 1),
                (Some("cc"), 1),
                (Some("dd"), 1),
                (Some("ee"), 1),
            ],
            0.25,
            85.0,
        );
        assert!(m.is_empty());
    }
}
