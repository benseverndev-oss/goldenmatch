//! Owned URL kernels (pyo3-free): normalize (scheme/domain-lowercase +
//! trailing-slash strip) and domain extraction. These are the reference
//! implementations; the Python/TS fallbacks must reproduce their bytes
//! exactly (byte-parity harness, `tests/parity/identifiers_corpus.jsonl`).
//!
//! Deliberately NOT using a `regex` crate dependency (mirrors the other
//! goldenflow-core kernels' no-regex policy for cross-surface parity
//! guarantees) -- the `^https?://` scheme check is hand-rolled via an
//! ASCII-case-insensitive prefix compare.

/// Case-insensitive check for a leading `http://` or `https://` scheme
/// (mirrors the Python reference's `re.compile(r"^https?://", re.IGNORECASE)`).
fn has_scheme(s: &str) -> bool {
    let lower = s.to_ascii_lowercase();
    lower.starts_with("https://") || lower.starts_with("http://")
}

/// Normalize a URL: ensure a scheme, lowercase the scheme + domain, keep the
/// path as-is, and strip a trailing slash (unless the path IS just `/`, in
/// which case exactly one trailing slash is dropped). `None` for empty
/// (post-trim) input. Always `Some` otherwise.
pub fn url_normalize(s: &str) -> Option<String> {
    let trimmed = s.trim();
    if trimmed.is_empty() {
        return None;
    }
    let mut val = trimmed.to_string();
    if !has_scheme(&val) {
        val = format!("https://{val}");
    }
    // Split scheme from rest -- guaranteed present after the prepend above.
    let scheme_idx = val.find("://").expect("scheme guaranteed present");
    let scheme_end = scheme_idx + 3;
    let scheme = val[..scheme_end].to_lowercase();
    let rest = &val[scheme_end..];
    // Lowercase the domain (everything before the first '/').
    let (domain, path) = match rest.find('/') {
        None => (rest.to_lowercase(), String::new()),
        Some(slash_idx) => (
            rest[..slash_idx].to_lowercase(),
            rest[slash_idx..].to_string(),
        ),
    };
    let mut result = format!("{scheme}{domain}{path}");
    // Strip trailing slash (but not if path is just "/").
    if result.ends_with('/') {
        let scheme_len = scheme.chars().count(); // ASCII scheme: chars == byte offset.
        let domain_len = domain.chars().count();
        let result_len = result.chars().count();
        if result_len > scheme_len + domain_len + 1 {
            result = result.trim_end_matches('/').to_string();
        } else if path == "/" {
            result.pop();
        }
    }
    Some(result)
}

/// Extract the lowercased domain from a URL: strip an optional `scheme://`
/// prefix, then take everything before the first `/`. `None` for empty
/// (post-trim) input or an empty domain.
pub fn url_extract_domain(s: &str) -> Option<String> {
    let trimmed = s.trim();
    if trimmed.is_empty() {
        return None;
    }
    let after_scheme = match trimmed.find("://") {
        Some(idx) => &trimmed[idx + 3..],
        None => trimmed,
    };
    let domain = after_scheme.split('/').next().unwrap_or("");
    if domain.is_empty() {
        None
    } else {
        Some(domain.to_lowercase())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normalize_adds_scheme_and_lowercases_domain() {
        assert_eq!(
            url_normalize("Example.COM/Path/"),
            Some("https://example.com/Path".to_string())
        );
    }

    #[test]
    fn normalize_strips_single_trailing_slash_when_path_is_root() {
        assert_eq!(
            url_normalize("http://X.com/"),
            Some("http://x.com".to_string())
        );
    }

    #[test]
    fn normalize_leaves_no_trailing_slash_unchanged() {
        assert_eq!(
            url_normalize("https://a.com"),
            Some("https://a.com".to_string())
        );
    }

    #[test]
    fn normalize_strips_all_trailing_slashes_when_path_has_more() {
        assert_eq!(
            url_normalize("https://a.com/x/"),
            Some("https://a.com/x".to_string())
        );
        assert_eq!(
            url_normalize("https://a.com/x//"),
            Some("https://a.com/x".to_string())
        );
    }

    #[test]
    fn normalize_case_insensitive_scheme_detection() {
        assert_eq!(
            url_normalize("HTTPS://Foo.com"),
            Some("https://foo.com".to_string())
        );
        assert_eq!(
            url_normalize("HtTp://Foo.com"),
            Some("http://foo.com".to_string())
        );
    }

    #[test]
    fn normalize_empty_and_whitespace() {
        assert_eq!(url_normalize(""), None);
        assert_eq!(url_normalize("   "), None);
    }

    #[test]
    fn normalize_no_path() {
        assert_eq!(
            url_normalize("EXAMPLE.com"),
            Some("https://example.com".to_string())
        );
    }

    #[test]
    fn extract_domain_strips_scheme_and_lowercases() {
        assert_eq!(
            url_extract_domain("https://Foo.com/x"),
            Some("foo.com".to_string())
        );
    }

    #[test]
    fn extract_domain_no_scheme() {
        assert_eq!(url_extract_domain("bar.com"), Some("bar.com".to_string()));
    }

    #[test]
    fn extract_domain_empty() {
        assert_eq!(url_extract_domain(""), None);
        assert_eq!(url_extract_domain("   "), None);
    }

    #[test]
    fn extract_domain_multi_at_scheme() {
        assert_eq!(
            url_extract_domain("http://sub.domain.org/path/more"),
            Some("sub.domain.org".to_string())
        );
    }
}
