//! String scorers — pure-Rust reimplementations of the `rapidfuzz` scorers that
//! `core/scorer.py::score_field` uses, for the Phase 2 native block-scorer.
//!
//! Hand-rolled (no `rapidfuzz-rs` crate) for two reasons: it builds with only
//! pyo3 (no extra crates to fetch), and it lets us control the formula to match
//! Python `rapidfuzz` exactly. Parity is asserted in tests/test_native_parity.py
//! against the installed `rapidfuzz`. All functions operate on Unicode chars
//! (codepoints), matching rapidfuzz.
use pyo3::prelude::*;

fn jaro_similarity(s1: &[char], s2: &[char]) -> f64 {
    let len1 = s1.len();
    let len2 = s2.len();
    if len1 == 0 && len2 == 0 {
        return 1.0;
    }
    if len1 == 0 || len2 == 0 {
        return 0.0;
    }
    let match_dist = (len1.max(len2) / 2).saturating_sub(1);
    let mut s1_matches = vec![false; len1];
    let mut s2_matches = vec![false; len2];
    let mut matches = 0usize;
    for i in 0..len1 {
        let start = i.saturating_sub(match_dist);
        let end = (i + match_dist + 1).min(len2);
        for j in start..end {
            if !s2_matches[j] && s1[i] == s2[j] {
                s1_matches[i] = true;
                s2_matches[j] = true;
                matches += 1;
                break;
            }
        }
    }
    if matches == 0 {
        return 0.0;
    }
    let mut transpositions = 0usize;
    let mut k = 0usize;
    for (i, &matched) in s1_matches.iter().enumerate() {
        if matched {
            while !s2_matches[k] {
                k += 1;
            }
            if s1[i] != s2[k] {
                transpositions += 1;
            }
            k += 1;
        }
    }
    // rapidfuzz floors half-transpositions (integer divide), which differs from
    // float /2.0 only when greedy matching yields an odd mismatch count.
    let t = (transpositions / 2) as f64;
    let m = matches as f64;
    (m / len1 as f64 + m / len2 as f64 + (m - t) / m) / 3.0
}

/// rapidfuzz `JaroWinkler.similarity` (prefix_weight default 0.1, max prefix 4).
/// rapidfuzz applies the prefix boost unconditionally (no 0.7 threshold).
fn jaro_winkler(s1: &[char], s2: &[char], prefix_weight: f64) -> f64 {
    let jaro = jaro_similarity(s1, s2);
    // rapidfuzz applies the prefix boost only when jaro > 0.7 (Winkler's
    // boost threshold); below it, JaroWinkler == Jaro.
    if jaro <= 0.7 {
        return jaro;
    }
    let max_prefix = s1.len().min(s2.len()).min(4);
    let mut prefix = 0usize;
    for i in 0..max_prefix {
        if s1[i] == s2[i] {
            prefix += 1;
        } else {
            break;
        }
    }
    jaro + prefix as f64 * prefix_weight * (1.0 - jaro)
}

fn levenshtein_distance(s1: &[char], s2: &[char]) -> usize {
    let (len1, len2) = (s1.len(), s2.len());
    if len1 == 0 {
        return len2;
    }
    if len2 == 0 {
        return len1;
    }
    let mut prev: Vec<usize> = (0..=len2).collect();
    let mut cur = vec![0usize; len2 + 1];
    for i in 1..=len1 {
        cur[0] = i;
        for j in 1..=len2 {
            let cost = if s1[i - 1] == s2[j - 1] { 0 } else { 1 };
            cur[j] = (prev[j] + 1).min(cur[j - 1] + 1).min(prev[j - 1] + cost);
        }
        std::mem::swap(&mut prev, &mut cur);
    }
    prev[len2]
}

/// rapidfuzz `Levenshtein.normalized_similarity` with uniform weights:
/// 1 - dist/max(len1, len2).
fn levenshtein_normalized_similarity(s1: &[char], s2: &[char]) -> f64 {
    let maxlen = s1.len().max(s2.len());
    if maxlen == 0 {
        return 1.0;
    }
    1.0 - levenshtein_distance(s1, s2) as f64 / maxlen as f64
}

/// Length of the longest common subsequence (for Indel-based `ratio`).
fn lcs_length(s1: &[char], s2: &[char]) -> usize {
    let (len1, len2) = (s1.len(), s2.len());
    if len1 == 0 || len2 == 0 {
        return 0;
    }
    let mut prev = vec![0usize; len2 + 1];
    let mut cur = vec![0usize; len2 + 1];
    for i in 1..=len1 {
        for j in 1..=len2 {
            cur[j] = if s1[i - 1] == s2[j - 1] {
                prev[j - 1] + 1
            } else {
                prev[j].max(cur[j - 1])
            };
        }
        std::mem::swap(&mut prev, &mut cur);
    }
    prev[len2]
}

/// rapidfuzz `fuzz.ratio` (Indel-based): 2*LCS/(len1+len2) * 100.
fn ratio(s1: &[char], s2: &[char]) -> f64 {
    let total = s1.len() + s2.len();
    if total == 0 {
        return 100.0;
    }
    2.0 * lcs_length(s1, s2) as f64 / total as f64 * 100.0
}

fn token_sort(s: &str) -> Vec<char> {
    let mut toks: Vec<&str> = s.split_whitespace().collect();
    toks.sort_unstable();
    toks.join(" ").chars().collect()
}

// ---- PyO3 surface (returns the same 0-1 / 0-100 scale score_field expects) ----

#[pyfunction]
pub fn jaro_winkler_similarity(a: &str, b: &str) -> f64 {
    let s1: Vec<char> = a.chars().collect();
    let s2: Vec<char> = b.chars().collect();
    jaro_winkler(&s1, &s2, 0.1)
}

#[pyfunction]
pub fn levenshtein_similarity(a: &str, b: &str) -> f64 {
    let s1: Vec<char> = a.chars().collect();
    let s2: Vec<char> = b.chars().collect();
    levenshtein_normalized_similarity(&s1, &s2)
}

/// token_sort_ratio on the 0-100 scale (score_field divides by 100).
#[pyfunction]
pub fn token_sort_ratio(a: &str, b: &str) -> f64 {
    ratio(&token_sort(a), &token_sort(b))
}
