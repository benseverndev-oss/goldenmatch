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

/// Query-param keys treated as tracking noise for dedup (matched
/// case-insensitively on the KEY only). A curated, stable set shared
/// byte-for-byte with the Python/TS fallbacks -- keep this list in lockstep
/// across the three surfaces.
const TRACKING_PARAMS: &[&str] = &[
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "utm_id",
    "utm_name",
    "utm_cid",
    "utm_reader",
    "utm_referrer",
    "utm_social",
    "utm_social_type",
    "gclid",
    "gclsrc",
    "dclid",
    "gbraid",
    "wbraid",
    "fbclid",
    "msclkid",
    "mc_eid",
    "mc_cid",
    "yclid",
    "igshid",
    "twclid",
    "_ga",
    "_gl",
    "ref",
    "ref_src",
    "spm",
];

/// True when `key` (case-insensitively) is a tracking param.
fn is_tracking_key(key: &str) -> bool {
    let lower = key.to_ascii_lowercase();
    TRACKING_PARAMS.contains(&lower.as_str())
}

/// Drop tracking params from a raw query string (the part after `?`, before
/// any `#`). Non-tracking params keep their original order + verbatim bytes.
fn strip_tracking_query(query: &str) -> String {
    query
        .split('&')
        .filter(|param| !is_tracking_key(param.split('=').next().unwrap_or("")))
        .collect::<Vec<_>>()
        .join("&")
}

/// Remove tracking query params (utm_*, gclid, fbclid, ...) from a URL,
/// preserving everything else verbatim (scheme, host case, path, remaining
/// query order, and any `#fragment`). The `?` is dropped entirely when no
/// params survive. `None` for empty (post-trim) input.
pub fn url_strip_tracking(s: &str) -> Option<String> {
    let t = s.trim();
    if t.is_empty() {
        return None;
    }
    // Fragment is everything from the first '#'.
    let (main, fragment) = match t.find('#') {
        Some(i) => (&t[..i], &t[i..]),
        None => (t, ""),
    };
    let result = match main.find('?') {
        None => format!("{main}{fragment}"),
        Some(i) => {
            let prefix = &main[..i];
            let stripped = strip_tracking_query(&main[i + 1..]);
            if stripped.is_empty() {
                format!("{prefix}{fragment}")
            } else {
                format!("{prefix}?{stripped}{fragment}")
            }
        }
    };
    Some(result)
}

/// Strip a leading `www.` label from the host (case-insensitive), preserving
/// the scheme, path, and host case otherwise verbatim. `None` for empty
/// (post-trim) input.
pub fn url_strip_www(s: &str) -> Option<String> {
    let t = s.trim();
    if t.is_empty() {
        return None;
    }
    let (scheme, rest) = match t.find("://") {
        Some(i) => (&t[..i + 3], &t[i + 3..]),
        None => ("", t),
    };
    let (host, path) = match rest.find('/') {
        Some(i) => (&rest[..i], &rest[i..]),
        None => (rest, ""),
    };
    // Byte compare avoids a char-boundary panic on a non-ASCII host.
    let host = if host.len() >= 4 && host.as_bytes()[..4].eq_ignore_ascii_case(b"www.")
    {
        &host[4..]
    } else {
        host
    };
    Some(format!("{scheme}{host}{path}"))
}

/// Composite dedup key: ensure a scheme, lowercase scheme+host, strip a
/// leading `www.`, drop the `#fragment`, remove tracking query params, and
/// strip a trailing slash (the `url_normalize` rule). The "are these the same
/// page" key. `None` for empty (post-trim) input.
pub fn url_canonical(s: &str) -> Option<String> {
    let t = s.trim();
    if t.is_empty() {
        return None;
    }
    // Drop fragment.
    let main = match t.find('#') {
        Some(i) => &t[..i],
        None => t,
    };
    let with_scheme = if has_scheme(main) {
        main.to_string()
    } else {
        format!("https://{main}")
    };
    let scheme_end = with_scheme.find("://").expect("scheme present") + 3;
    let scheme = with_scheme[..scheme_end].to_lowercase();
    let rest = &with_scheme[scheme_end..];
    let (host_raw, path) = match rest.find('/') {
        Some(i) => (&rest[..i], rest[i..].to_string()),
        None => (rest, String::new()),
    };
    let mut host = host_raw.to_lowercase();
    if host.len() >= 4 && host.as_bytes()[..4].eq_ignore_ascii_case(b"www.") {
        host = host[4..].to_string();
    }
    // Split the path from its query, strip ALL trailing slashes off the path
    // (a dedup key treats `/Path/` == `/Path` == root `/` == ""), then drop
    // tracking params from the query.
    let (pathpart, query_raw) = match path.find('?') {
        None => (path.as_str(), ""),
        Some(i) => (&path[..i], &path[i + 1..]),
    };
    let pathpart = pathpart.trim_end_matches('/');
    let query = strip_tracking_query(query_raw);
    let mut result = format!("{scheme}{host}{pathpart}");
    if !query.is_empty() {
        result.push('?');
        result.push_str(&query);
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
    fn strip_tracking_removes_utm_and_keeps_order() {
        assert_eq!(
            url_strip_tracking("https://a.com/p?utm_source=x&id=7&utm_medium=y&q=1"),
            Some("https://a.com/p?id=7&q=1".to_string())
        );
        // all params are tracking -> drop the '?'.
        assert_eq!(
            url_strip_tracking("https://a.com/p?utm_source=x&fbclid=abc"),
            Some("https://a.com/p".to_string())
        );
        // fragment preserved; tracking removed before it.
        assert_eq!(
            url_strip_tracking("https://a.com/p?gclid=1&k=2#frag"),
            Some("https://a.com/p?k=2#frag".to_string())
        );
        // no query -> unchanged (post-trim).
        assert_eq!(
            url_strip_tracking("https://a.com/p"),
            Some("https://a.com/p".to_string())
        );
        // key match is case-insensitive.
        assert_eq!(
            url_strip_tracking("https://a.com?UTM_Source=x&keep=1"),
            Some("https://a.com?keep=1".to_string())
        );
        assert_eq!(url_strip_tracking(""), None);
    }

    #[test]
    fn strip_www_removes_leading_label_only() {
        assert_eq!(
            url_strip_www("https://www.Example.com/Path"),
            Some("https://Example.com/Path".to_string())
        );
        assert_eq!(
            url_strip_www("http://WWW.a.com"),
            Some("http://a.com".to_string())
        );
        // no scheme.
        assert_eq!(url_strip_www("www.a.com/x"), Some("a.com/x".to_string()));
        // no www -> unchanged.
        assert_eq!(
            url_strip_www("https://a.com"),
            Some("https://a.com".to_string())
        );
        // www NOT the leading label -> untouched.
        assert_eq!(
            url_strip_www("https://sub.www.a.com"),
            Some("https://sub.www.a.com".to_string())
        );
        assert_eq!(url_strip_www(""), None);
    }

    #[test]
    fn canonical_full_dedup_key() {
        assert_eq!(
            url_canonical("HTTPS://WWW.Example.com/Path/?utm_source=x&id=7#frag"),
            Some("https://example.com/Path?id=7".to_string())
        );
        // adds scheme, lowercases host, strips www + trailing slash.
        assert_eq!(
            url_canonical("WWW.A.com/"),
            Some("https://a.com".to_string())
        );
        // all-tracking query dropped; root slash removed.
        assert_eq!(
            url_canonical("http://a.com/?fbclid=1"),
            Some("http://a.com".to_string())
        );
        assert_eq!(url_canonical(""), None);
        assert_eq!(url_canonical("   "), None);
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
