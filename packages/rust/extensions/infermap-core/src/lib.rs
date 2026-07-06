//! InferMap kernels (pyo3-free). Single source of truth mirrored value-for-value by
//! `infermap/detect.py::_detect_core_pure` and `packages/typescript/infermap` `detect.ts`.

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

#[cfg(test)]
mod tests {
    use super::*;

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
}
