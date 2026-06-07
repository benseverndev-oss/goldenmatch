//! Fuzzy near-duplicate VALUE clustering.
//!
//! Given the distinct values of a (categorical/string) column, find clusters of
//! values that are edit-distance-close -- inconsistent encodings of the same
//! thing: `"California"` / `"Californa"` / `"CALIFORNIA "`, or `"Jon"` / `"John"`.
//! This complements `relations/approx_duplicate.py` (which catches values that
//! are *equal* after normalization); here the values differ even after
//! normalization but are typo-close.
//!
//! Whole-ROW fuzzy matching is deliberately out of scope -- that's entity
//! resolution (GoldenMatch's job). This stays at the value level: a bounded,
//! per-column data-quality check.
//!
//! Algorithm (blocking + scoring + union-find):
//!   - **Blocking** generates candidate pairs cheaply so we never do the full
//!     O(n^2) comparison: two values are candidates if they share a character
//!     trigram OR the same first-two-character prefix (the prefix block catches
//!     short strings like `jon`/`john`, which share no trigram). Over-common
//!     blocks (size > `MAX_BLOCK`) are skipped to bound the work.
//!   - **Scoring** each candidate pair with a Levenshtein similarity ratio
//!     `1 - dist/max(len_a, len_b)` on the normalized (lowercased, whitespace-
//!     collapsed) form. Pairs >= `min_similarity` are linked.
//!   - **Union-find** groups linked values into clusters; clusters of size >= 2
//!     are returned (as index lists into the input `values`).
//!
//! Pairwise edit distance is the part that is painfully slow in Python and fast
//! here -- this kernel genuinely beats a pure-Python fallback.

use rustc_hash::{FxHashMap, FxHashSet};

const MAX_BLOCK: usize = 300;
/// Values shorter than this are compared via the prefix block only (too short
/// for meaningful trigrams), and never matched below `MIN_LEN_FOR_FUZZY` to
/// avoid pairing near-everything (e.g. 1-2 char codes).
const MIN_LEN_FOR_FUZZY: usize = 3;

/// Normalize for matching: lowercase + collapse internal whitespace + trim.
/// (Punctuation is kept -- stripping it is the exact-after-normalization job of
/// the Polars duplicate profiler; here we measure edit distance.)
fn normalize(s: &str) -> String {
    let lower = s.to_lowercase();
    let mut out = String::with_capacity(lower.len());
    let mut prev_space = false;
    for ch in lower.chars() {
        if ch.is_whitespace() {
            if !out.is_empty() && !prev_space {
                out.push(' ');
                prev_space = true;
            }
        } else {
            out.push(ch);
            prev_space = false;
        }
    }
    if out.ends_with(' ') {
        out.pop();
    }
    out
}

fn char_trigrams(chars: &[char]) -> Vec<[char; 3]> {
    if chars.len() < 3 {
        return Vec::new();
    }
    (0..=chars.len() - 3)
        .map(|i| [chars[i], chars[i + 1], chars[i + 2]])
        .collect()
}

/// Levenshtein edit distance between two char slices (classic two-row DP).
fn levenshtein(a: &[char], b: &[char]) -> usize {
    if a.is_empty() {
        return b.len();
    }
    if b.is_empty() {
        return a.len();
    }
    let mut prev: Vec<usize> = (0..=b.len()).collect();
    let mut cur = vec![0usize; b.len() + 1];
    for (i, &ca) in a.iter().enumerate() {
        cur[0] = i + 1;
        for (j, &cb) in b.iter().enumerate() {
            let cost = if ca == cb { 0 } else { 1 };
            cur[j + 1] = (prev[j + 1] + 1).min(cur[j] + 1).min(prev[j] + cost);
        }
        std::mem::swap(&mut prev, &mut cur);
    }
    prev[b.len()]
}

fn similarity(a: &[char], b: &[char]) -> f64 {
    let maxlen = a.len().max(b.len());
    if maxlen == 0 {
        return 1.0;
    }
    1.0 - (levenshtein(a, b) as f64) / (maxlen as f64)
}

struct UnionFind {
    parent: Vec<usize>,
}
impl UnionFind {
    fn new(n: usize) -> Self {
        Self {
            parent: (0..n).collect(),
        }
    }
    fn find(&mut self, mut x: usize) -> usize {
        while self.parent[x] != x {
            self.parent[x] = self.parent[self.parent[x]];
            x = self.parent[x];
        }
        x
    }
    fn union(&mut self, a: usize, b: usize) {
        let (ra, rb) = (self.find(a), self.find(b));
        if ra != rb {
            self.parent[ra] = rb;
        }
    }
}

/// Cluster the distinct `values` into groups of edit-distance-close strings.
/// Returns clusters (each a sorted list of indices into `values`) of size >= 2.
pub fn near_duplicate_clusters(values: &[String], min_similarity: f64) -> Vec<Vec<usize>> {
    let n = values.len();
    if n < 2 {
        return Vec::new();
    }
    // Normalize once; keep char vectors for distance + trigrams.
    let norm: Vec<Vec<char>> = values
        .iter()
        .map(|v| normalize(v).chars().collect())
        .collect();

    // Blocking buckets: trigram -> indices, and 2-char-prefix -> indices.
    let mut trigram_buckets: FxHashMap<[char; 3], Vec<usize>> = FxHashMap::default();
    let mut prefix_buckets: FxHashMap<[char; 2], Vec<usize>> = FxHashMap::default();
    for (i, chars) in norm.iter().enumerate() {
        if chars.len() < MIN_LEN_FOR_FUZZY {
            continue;
        }
        for tg in char_trigrams(chars) {
            trigram_buckets.entry(tg).or_default().push(i);
        }
        prefix_buckets
            .entry([chars[0], chars[1]])
            .or_default()
            .push(i);
    }

    // Candidate pairs (i < j), de-duplicated across both blocking strategies.
    let mut candidates: FxHashSet<(usize, usize)> = FxHashSet::default();
    for bucket in trigram_buckets.values().chain(prefix_buckets.values()) {
        if bucket.len() < 2 || bucket.len() > MAX_BLOCK {
            continue; // singleton or over-common block (bounds the work)
        }
        for a in 0..bucket.len() {
            for b in (a + 1)..bucket.len() {
                let (i, j) = (bucket[a], bucket[b]);
                candidates.insert(if i < j { (i, j) } else { (j, i) });
            }
        }
    }

    let mut uf = UnionFind::new(n);
    let mut linked = false;
    for (i, j) in candidates {
        if similarity(&norm[i], &norm[j]) >= min_similarity {
            uf.union(i, j);
            linked = true;
        }
    }
    if !linked {
        return Vec::new();
    }

    // Gather clusters of size >= 2.
    let mut groups: FxHashMap<usize, Vec<usize>> = FxHashMap::default();
    for i in 0..n {
        let r = uf.find(i);
        groups.entry(r).or_default().push(i);
    }
    let mut clusters: Vec<Vec<usize>> = groups.into_values().filter(|g| g.len() >= 2).collect();
    for c in &mut clusters {
        c.sort_unstable();
    }
    clusters.sort_unstable();
    clusters
}

#[cfg(test)]
mod tests {
    use super::*;

    fn v(items: &[&str]) -> Vec<String> {
        items.iter().map(|s| s.to_string()).collect()
    }

    #[test]
    fn clusters_typos_and_case() {
        let values = v(&["California", "Californa", "CALIFORNIA", "Texas", "New York"]);
        let clusters = near_duplicate_clusters(&values, 0.8);
        // The three California variants cluster; Texas / New York stand alone.
        assert_eq!(clusters.len(), 1);
        assert_eq!(clusters[0], vec![0, 1, 2]);
    }

    #[test]
    fn short_string_typo_via_prefix_block() {
        let values = v(&["Jon", "John", "Jane"]);
        let clusters = near_duplicate_clusters(&values, 0.7);
        assert_eq!(clusters, vec![vec![0, 1]]); // jon/john; jane separate
    }

    #[test]
    fn nothing_when_all_distinct() {
        let values = v(&["apple", "banana", "cherry"]);
        assert!(near_duplicate_clusters(&values, 0.8).is_empty());
    }

    #[test]
    fn empty_and_singleton() {
        assert!(near_duplicate_clusters(&[], 0.8).is_empty());
        assert!(near_duplicate_clusters(&v(&["x"]), 0.8).is_empty());
    }
}
