//! InferMap kernels (pyo3-free). Single source of truth mirrored value-for-value by
//! `infermap/detect.py::_detect_core_pure` and `packages/typescript/infermap` `detect.ts`.

use regex::Regex;
use std::sync::OnceLock;

#[derive(Debug, Clone, PartialEq)]
pub struct Detection {
    pub domain: Option<String>,
    pub score: f64,
    pub runner_up: Option<String>,
    pub runner_up_score: f64,
    pub reason: String,
}

/// Tokenize on `_`, `-`, `.`, and whitespace; lowercase; drop empties.
///
/// See the design spec (§6): Python regex `\s` and Rust `char::is_whitespace()` diverge
/// at `\x1c`-`\x1f`/`\x85`, and `str.lower()` vs `to_lowercase()` diverge on some
/// non-ASCII chars. Real column names are ASCII, where all three surfaces agree; the
/// exotic-whitespace / non-ASCII cases are the documented parity edge.
fn tokens(s: &str) -> Vec<String> {
    s.split(|c: char| c == '_' || c == '-' || c == '.' || c.is_whitespace())
        .filter(|t| !t.is_empty())
        .map(|t| t.to_lowercase())
        .collect()
}

/// True iff `hint`'s tokens appear as a contiguous run in `col`'s tokens.
fn hint_matches(hint: &str, col: &str) -> bool {
    let h = tokens(hint);
    let c = tokens(col);
    if h.is_empty() || c.is_empty() {
        return false;
    }
    // windows(n) yields nothing when n > c.len() -- no usize underflow (cf. Python's
    // `range(len - n + 1)` yielding empty).
    c.windows(h.len()).any(|w| w == h.as_slice())
}

/// Domain auto-detection. `columns`: the df's column names. `domains`: (name, deduped
/// name_hints) IN HOST ORDER. Byte-mirror of `detect.py::detect_domain_detailed`'s
/// scoring + decision.
pub fn detect_domain(
    columns: &[String],
    domains: &[(String, Vec<String>)],
    min_score: f64,
) -> Detection {
    let no_data = || Detection {
        domain: None,
        score: 0.0,
        runner_up: None,
        runner_up_score: 0.0,
        reason: "no_data".to_string(),
    };
    if columns.is_empty() {
        return no_data();
    }
    let mut scored: Vec<(String, f64)> = Vec::new();
    for (name, hints) in domains {
        if hints.is_empty() {
            continue;
        }
        let hits = columns
            .iter()
            .filter(|c| hints.iter().any(|h| hint_matches(h, c)))
            .count();
        scored.push((name.clone(), hits as f64 / columns.len() as f64));
    }
    if scored.is_empty() {
        return no_data();
    }
    // STABLE descending sort by score; equal scores keep host order (matches Python
    // `sort(key=score, reverse=True)`, which is stable and leaves ties in place).
    scored.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap());
    let (best_name, best_score) = scored[0].clone();
    let (runner_up, runner_up_score) = match scored.get(1) {
        Some((n, s)) => (Some(n.clone()), *s),
        None => (None, 0.0),
    };
    if best_score < min_score {
        return Detection {
            domain: None,
            score: best_score,
            runner_up,
            runner_up_score,
            reason: "below_min_score".to_string(),
        };
    }
    let top_count = scored.iter().filter(|(_, s)| *s == best_score).count();
    if top_count > 1 {
        return Detection {
            domain: None,
            score: best_score,
            runner_up,
            runner_up_score,
            reason: "tie".to_string(),
        };
    }
    Detection {
        domain: Some(best_name),
        score: best_score,
        runner_up,
        runner_up_score,
        reason: "confident".to_string(),
    }
}

// ===========================================================================
// Wave 2: pure name-scorer kernels. Mirror infermap/scorers/{exact,fuzzy_name,
// initialism}.py value-for-value. Each returns the SCORE; the Python scorer class
// keeps its reasoning string (dodges float-format parity).
// ===========================================================================

use goldenmatch_score_core::jaro_winkler_similarity;

/// ExactScorer: 1.0 iff trimmed-lowercased names are equal, else 0.0.
pub fn exact_score(a: &str, b: &str) -> f64 {
    if a.trim().to_lowercase() == b.trim().to_lowercase() {
        1.0
    } else {
        0.0
    }
}

/// normalize = strip + lower + remove `_`, `-`, ` ` (mirrors `fuzzy_name._normalize`).
fn normalize(s: &str) -> String {
    s.trim()
        .to_lowercase()
        .chars()
        .filter(|&c| c != '_' && c != '-' && c != ' ')
        .collect()
}

/// FuzzyNameScorer: Jaro-Winkler on normalized names (reuses score-core).
pub fn fuzzy_name_score(a: &str, b: &str) -> f64 {
    jaro_winkler_similarity(&normalize(a), &normalize(b))
}

/// Tokenizer -- a hand-written char-scanner reproducing the INLINE regex at
/// `initialism.py:40` (`[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+`) with its
/// backtracking, since Rust's `regex` crate has no lookahead. Splits on `_ - .`
/// whitespace; per chunk, an uppercase run of len>=2 immediately followed by a
/// lowercase peels its last char onto the following word (`providerIDs`->
/// `[provider,i,ds]`); else the whole run / word / digit-run is a token; all lowercased.
fn tokenize(name: &str) -> Vec<String> {
    let mut tokens: Vec<String> = Vec::new();
    for chunk in name.split(|c: char| c == '_' || c == '-' || c == '.' || c.is_whitespace()) {
        if chunk.is_empty() {
            continue;
        }
        let ch: Vec<char> = chunk.chars().collect();
        let n = ch.len();
        let mut i = 0;
        while i < n {
            let c = ch[i];
            if c.is_ascii_uppercase() {
                let mut e = i;
                while e < n && ch[e].is_ascii_uppercase() {
                    e += 1;
                }
                let run_len = e - i;
                if run_len >= 2 && e < n && ch[e].is_ascii_lowercase() {
                    // alt1: acronym = run minus its last char; last char starts next word.
                    tokens.push(ch[i..e - 1].iter().collect::<String>().to_lowercase());
                    i = e - 1;
                } else if run_len == 1 && e < n && ch[e].is_ascii_lowercase() {
                    // alt2: [A-Z]?[a-z]+ word.
                    let mut w = e;
                    while w < n && ch[w].is_ascii_lowercase() {
                        w += 1;
                    }
                    tokens.push(ch[i..w].iter().collect::<String>().to_lowercase());
                    i = w;
                } else {
                    // alt3: [A-Z]+ acronym (end-of-chunk or followed by non-lowercase).
                    tokens.push(ch[i..e].iter().collect::<String>().to_lowercase());
                    i = e;
                }
            } else if c.is_ascii_lowercase() {
                // alt2 with empty [A-Z]?: a lowercase run.
                let mut w = i;
                while w < n && ch[w].is_ascii_lowercase() {
                    w += 1;
                }
                tokens.push(ch[i..w].iter().collect::<String>().to_lowercase());
                i = w;
            } else if c.is_ascii_digit() {
                // alt4: \d+.
                let mut d = i;
                while d < n && ch[d].is_ascii_digit() {
                    d += 1;
                }
                tokens.push(ch[i..d].iter().collect::<String>());
                i = d;
            } else {
                i += 1; // non-matching char (findall skips it)
            }
        }
    }
    tokens
}

/// DP: can `target` be formed by concatenating >=1-char prefixes of `source_tokens`
/// in order, using each exactly once? Mirrors `initialism._is_prefix_concat` (char-wise).
fn is_prefix_concat(target: &str, source_tokens: &[String]) -> bool {
    let t: Vec<char> = target.to_lowercase().chars().collect();
    let toks: Vec<Vec<char>> = source_tokens.iter().map(|s| s.chars().collect()).collect();
    let (n_src, n_tgt) = (toks.len(), t.len());
    if n_src == 0 || n_tgt == 0 {
        return false;
    }
    let mut dp = vec![vec![false; n_tgt + 1]; n_src + 1];
    dp[0][0] = true;
    for i in 1..=n_src {
        let tok = &toks[i - 1];
        for j in 1..=n_tgt {
            let kmax = tok.len().min(j);
            for k in 1..=kmax {
                if t[j - k..j] == tok[..k] && dp[i - 1][j - k] {
                    dp[i][j] = true;
                    break;
                }
            }
        }
    }
    dp[n_src][n_tgt]
}

/// InitialismScorer: `0.6 + 0.35*(len_short/len_long)` when one side is a prefix-concat
/// abbreviation of the other; `None` (abstain) otherwise. Mirrors `_score_pair`.
pub fn initialism_score(a: &str, b: &str) -> Option<f64> {
    let tok_a = tokenize(a);
    let tok_b = tokenize(b);
    let joined_a: String = tok_a.concat();
    let joined_b: String = tok_b.concat();
    if joined_a.is_empty() || joined_b.is_empty() {
        return None;
    }
    if joined_a == joined_b {
        return None;
    }
    let (long, short) = if is_prefix_concat(&joined_b, &tok_a) {
        (&joined_a, &joined_b)
    } else if is_prefix_concat(&joined_a, &tok_b) {
        (&joined_b, &joined_a)
    } else {
        return None;
    };
    // CHAR count (Python `len()`), not byte `.len()`; exact op order for byte-parity.
    let ratio = short.chars().count() as f64 / long.chars().count() as f64;
    Some(0.6 + 0.35 * ratio)
}

/// max(0, 1 - |a-b|) -- matches Python `max(0.0, 1.0 - abs(a - b))` arg order.
fn similarity(a: f64, b: f64) -> f64 {
    (1.0 - (a - b).abs()).max(0.0)
}

/// Byte-parity reference: infermap.scorers.profile._profile_score_pure.
/// Returns the raw (pre-clamp) profile score. The caller owns the abstain check
/// (value_count == 0), average-length reduction, and reasoning string.
///
/// Fixed five-add order (no loop / no iter().sum() SIMD-reduction) -> byte-identical
/// to the Python source under IEEE-754.
#[allow(clippy::too_many_arguments)]
pub fn profile_score(
    src_dtype: &str,
    tgt_dtype: &str,
    src_null: f64,
    tgt_null: f64,
    src_uniq: f64,
    tgt_uniq: f64,
    src_val_count: f64,
    tgt_val_count: f64,
    src_avg_len: f64,
    tgt_avg_len: f64,
) -> f64 {
    let mut total = 0.0_f64;

    // dtype match (0.4)
    let dtype_match = if src_dtype == tgt_dtype { 1.0 } else { 0.0 };
    total += 0.4 * dtype_match;

    // null-rate similarity (0.2)
    total += 0.2 * similarity(src_null, tgt_null);

    // uniqueness similarity (0.2)
    total += 0.2 * similarity(src_uniq, tgt_uniq);

    // value-length similarity (0.1)
    let max_len = src_avg_len.max(tgt_avg_len).max(1.0);
    total += 0.1 * (1.0 - (src_avg_len - tgt_avg_len).abs() / max_len);

    // cardinality-ratio similarity (0.1)
    let src_card = src_uniq * src_val_count;
    let tgt_card = tgt_uniq * tgt_val_count;
    let max_card = src_card.max(tgt_card).max(1.0);
    total += 0.1 * (1.0 - (src_card - tgt_card).abs() / max_card);

    total
}

const N_SEMANTIC_TYPES: usize = 8;

/// The 8 semantic-type regexes, in SEMANTIC_TYPES insertion order (bit index).
/// currency drops the non-ASCII backslash-escapes (`\£`/`\€` fail to compile in
/// the `regex` crate; `£`/`€` are literal codepoints either way).
fn semantic_patterns() -> &'static [Regex; N_SEMANTIC_TYPES] {
    static PATS: OnceLock<[Regex; N_SEMANTIC_TYPES]> = OnceLock::new();
    PATS.get_or_init(|| {
        [
            Regex::new(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$").unwrap(),
            Regex::new(
                r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$",
            )
            .unwrap(),
            Regex::new(r"^\d{4}-\d{2}-\d{2}$").unwrap(),
            Regex::new(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$").unwrap(),
            Regex::new(r"^https?://[^\s]+$").unwrap(),
            Regex::new(r"^[\+\d]?(\d[\s\-\.]?){7,14}\d$").unwrap(),
            Regex::new(r"^\d{5}(-\d{4})?$").unwrap(),
            Regex::new(r"^[$£€]\s?\d[\d,]*(\.\d{1,2})?$").unwrap(),
        ]
    })
}

/// Byte-parity reference: infermap.scorers.pattern_type._match_types_pure (per element).
/// bit i (LSB=0) set iff the (host-pre-stripped) sample matches SEMANTIC_TYPES[i].
/// Boolean membership only; `^...$` on a newline-free string == Python `.match` full-match.
pub fn pattern_match_types(samples: &[String]) -> Vec<u32> {
    let pats = semantic_patterns();
    samples
        .iter()
        .map(|s| {
            let mut mask = 0u32;
            for (i, re) in pats.iter().enumerate() {
                if re.is_match(s) {
                    mask |= 1 << i;
                }
            }
            mask
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn exact_match_and_mismatch() {
        assert_eq!(exact_score("City", " city "), 1.0);
        assert_eq!(exact_score("a", "b"), 0.0);
    }

    #[test]
    fn profile_identical_and_dtype_mismatch() {
        // identical profiles -> all 5 terms = 1.0
        let s = profile_score("string", "string", 0.1, 0.1, 0.5, 0.5,
                              100.0, 100.0, 8.0, 8.0);
        assert_eq!(s, 1.0);
        // dtype mismatch only -> 1.0 - 0.4 = 0.6
        let s2 = profile_score("string", "int", 0.1, 0.1, 0.5, 0.5,
                               100.0, 100.0, 8.0, 8.0);
        assert_eq!(s2, 0.6);
        // avg_len 0/0 floors denom to 1.0 -> len term stays 1.0 (no div-by-zero)
        let s3 = profile_score("string", "string", 0.0, 0.0, 0.0, 0.0,
                               1.0, 1.0, 0.0, 0.0);
        assert_eq!(s3, 1.0);
    }

    #[test]
    fn fuzzy_identical_and_disjoint() {
        assert_eq!(fuzzy_name_score("city", "city"), 1.0);
        assert_eq!(fuzzy_name_score("abc", "xyz"), 0.0);
    }

    #[test]
    fn tokenize_camelcase_examples() {
        assert_eq!(tokenize("HTTPSConnection"), vec!["https", "connection"]);
        assert_eq!(tokenize("providerID"), vec!["provider", "id"]);
        assert_eq!(tokenize("order_id"), vec!["order", "id"]);
        assert_eq!(tokenize("ABC"), vec!["abc"]);
        assert_eq!(tokenize("v2Name"), vec!["v", "2", "name"]);
        // Load-bearing boundary: N-upper run + single trailing lowercase.
        assert_eq!(tokenize("providerIDs"), vec!["provider", "i", "ds"]);
        assert_eq!(tokenize("URLs"), vec!["ur", "ls"]);
        assert_eq!(tokenize("iOS"), vec!["i", "os"]);
        assert_eq!(tokenize("macOS"), vec!["mac", "os"]);
        assert_eq!(tokenize("Name"), vec!["name"]);
    }

    #[test]
    fn initialism_abbrev_and_abstain() {
        let s = initialism_score("assay_id", "ASSI").unwrap();
        assert!((s - (0.6 + 0.35 * (4.0 / 7.0))).abs() < 1e-12);
        assert_eq!(initialism_score("city", "town"), None);
        assert_eq!(initialism_score("city", "city"), None);
    }

    fn d(name: &str, hints: &[&str]) -> (String, Vec<String>) {
        (name.to_string(), hints.iter().map(|s| s.to_string()).collect())
    }
    fn cols(xs: &[&str]) -> Vec<String> {
        xs.iter().map(|s| s.to_string()).collect()
    }

    #[test]
    fn confident_multitoken_hint() {
        let r = detect_domain(
            &cols(&["provider_npi", "first_name"]),
            &[d("health", &["provider npi"]), d("fin", &["iban"])],
            0.3,
        );
        assert_eq!(r.domain, Some("health".to_string()));
        assert_eq!(r.reason, "confident");
        assert_eq!(r.score, 0.5);
    }

    #[test]
    fn empty_columns_no_data() {
        assert_eq!(detect_domain(&[], &[d("h", &["x"])], 0.3).reason, "no_data");
    }

    #[test]
    fn no_hints_no_data() {
        assert_eq!(detect_domain(&cols(&["a"]), &[d("h", &[])], 0.3).reason, "no_data");
    }

    #[test]
    fn below_min_score() {
        let r = detect_domain(&cols(&["a", "b", "c", "d"]), &[d("h", &["a"])], 0.3);
        assert_eq!(r.reason, "below_min_score");
        assert_eq!(r.domain, None);
    }

    #[test]
    fn tie_two_domains() {
        let r = detect_domain(&cols(&["a", "b"]), &[d("x", &["a"]), d("y", &["b"])], 0.3);
        assert_eq!(r.reason, "tie");
        assert_eq!(r.domain, None);
    }

    #[test]
    fn three_way_tie_keeps_host_order() {
        // all score 0.5; stable sort keeps host order -> runner_up is the 2nd (y)
        let r = detect_domain(
            &cols(&["a", "b"]),
            &[d("x", &["a"]), d("y", &["b"]), d("z", &["a"])],
            0.3,
        );
        assert_eq!(r.reason, "tie");
        assert_eq!(r.runner_up, Some("y".to_string()));
    }

    #[test]
    fn hint_longer_than_column_no_underflow() {
        assert!(!hint_matches("a b c", "a"));
    }

    #[test]
    fn ascii_case_insensitive() {
        assert!(hint_matches("NPI", "provider_npi"));
    }

    #[test]
    fn pattern_match_types_bits() {
        let mk = |x: &str| x.to_string();
        let out = pattern_match_types(&[
            mk("user@example.com"), // email          -> bit 0
            mk("2026-07-06"),       // date_iso + phone -> bits 2|5 (co-match by construction)
            mk("hello world"),      // none            -> 0
            mk("$5"),               // currency        -> bit 7
        ]);
        assert_eq!(
            out,
            vec![1u32 << 0, (1u32 << 2) | (1u32 << 5), 0u32, 1u32 << 7]
        );
    }
}
