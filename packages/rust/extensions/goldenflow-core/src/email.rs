//! Owned email kernels (pyo3-free): lowercase, normalize (Gmail dot-strip +
//! `+tag` strip), domain extraction, and format validation. These are the
//! reference implementations; the Python/TS fallbacks must reproduce their
//! bytes exactly (byte-parity harness, `tests/parity/identifiers_corpus.jsonl`).
//!
//! Deliberately NOT using a `regex` crate dependency (mirrors the
//! checksummed-identifier kernels' no-regex policy for cross-surface parity
//! guarantees) -- `email_validate` hand-rolls the equivalent of
//! `^[^@\s]+@[^@\s]+\.[^@\s]+$` via byte/char scanning.

/// Trim ASCII/Unicode whitespace and lowercase the whole address.
/// Always returns a `String` -- there is no "invalid input" for lowercasing.
pub fn email_lowercase(s: &str) -> String {
    s.trim().to_lowercase()
}

/// Normalize an email address: lowercase, strip a `+tag` from the local
/// part, and strip dots from the local part for Gmail/Googlemail domains.
///
/// Preserves the ORIGINAL (untrimmed) input verbatim when the trimmed +
/// lowercased value is empty or has no `@` -- mirrors the Python reference's
/// "preserve invalid values" behavior. Always returns a `String`.
pub fn email_normalize(s: &str) -> String {
    let v = s.trim().to_lowercase();
    if v.is_empty() || !v.contains('@') {
        return s.to_string();
    }
    // Split on the LAST '@' (mirrors Python's `rsplit("@", 1)`).
    let idx = v.rfind('@').expect("checked contains('@') above");
    let local = &v[..idx];
    let domain = &v[idx + 1..];
    let local = local.split('+').next().unwrap_or("");
    let local = if domain == "gmail.com" || domain == "googlemail.com" {
        local.replace('.', "")
    } else {
        local.to_string()
    };
    format!("{local}@{domain}")
}

/// Full dedup key: `email_normalize`, then alias `googlemail.com` ->
/// `gmail.com` so Gmail variants collapse completely (normalize already
/// dot-strips both, but leaves the `googlemail.com` domain intact, so
/// `a.b@googlemail.com` and `ab@gmail.com` would NOT otherwise dedupe).
/// Preserves invalid input verbatim, exactly like `email_normalize`.
pub fn email_canonical(s: &str) -> String {
    let normalized = email_normalize(s);
    if let Some(idx) = normalized.rfind('@') {
        let local = &normalized[..idx];
        let domain = &normalized[idx + 1..];
        if domain == "googlemail.com" {
            return format!("{local}@gmail.com");
        }
    }
    normalized
}

/// PII mask: trim + lowercase, then keep the FIRST char of the local part and
/// replace the remaining local chars with `*`, keeping `@domain` intact
/// (e.g. `John@Example.com` -> `j***@example.com`). `None` when the input has
/// no `@`, an empty local part, or an empty domain (nothing meaningful to
/// mask) -- matching the `ssn_mask`/`cc_mask` "None on unmaskable input" shape.
pub fn email_mask(s: &str) -> Option<String> {
    let v = s.trim().to_lowercase();
    let idx = v.rfind('@')?;
    let local = &v[..idx];
    let domain = &v[idx + 1..];
    if local.is_empty() || domain.is_empty() {
        return None;
    }
    let first = local.chars().next().expect("local non-empty");
    let stars = "*".repeat(local.chars().count() - 1);
    Some(format!("{first}{stars}@{domain}"))
}

/// Extract the lowercased domain after the LAST `@`. `None` if there is no
/// `@`, or nothing follows it (mirrors the regex `@([^@]+)$`).
pub fn email_extract_domain(s: &str) -> Option<String> {
    let t = s.trim();
    let idx = t.rfind('@')?;
    let domain = &t[idx + 1..];
    if domain.is_empty() {
        return None;
    }
    Some(domain.to_lowercase())
}

/// Validate email format against the hand-rolled equivalent of
/// `^[^@\s]+@[^@\s]+\.[^@\s]+$`: exactly one `@`; a non-empty,
/// whitespace-free local part; a non-empty, whitespace-free domain part
/// containing a `.` that is neither the first nor the last character.
/// Empty (after trim) input is `Some(false)`, never `None` -- there is no
/// null-propagation case for a non-null input.
pub fn email_validate(s: &str) -> Option<bool> {
    let t = s.trim();
    if t.is_empty() {
        return Some(false);
    }
    if t.matches('@').count() != 1 {
        return Some(false);
    }
    let idx = t.find('@').expect("checked exactly one '@' above");
    let local = &t[..idx];
    let domain = &t[idx + 1..];
    if local.is_empty() || local.chars().any(char::is_whitespace) {
        return Some(false);
    }
    if domain.is_empty() || domain.chars().any(char::is_whitespace) {
        return Some(false);
    }
    // domain must contain a '.' that is neither the first nor last byte, i.e.
    // there's >=1 byte on each side (mirrors `[^@\s]+\.[^@\s]+` fully
    // consuming the domain -- already guaranteed @/whitespace-free above).
    let has_valid_dot = domain
        .char_indices()
        .any(|(i, c)| c == '.' && i != 0 && i + 1 != domain.len());
    Some(has_valid_dot)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn lowercase() {
        assert_eq!(email_lowercase(" John@X.COM "), "john@x.com");
        assert_eq!(email_lowercase("A@B.com"), "a@b.com");
    }

    #[test]
    fn normalize() {
        assert_eq!(
            email_normalize("John.Doe+tag@Gmail.com"),
            "johndoe@gmail.com"
        );
        assert_eq!(email_normalize("a+b@x.com"), "a@x.com");
        assert_eq!(email_normalize("notanemail"), "notanemail");
        assert_eq!(email_normalize("A@B.com"), "a@b.com");
        assert_eq!(
            email_normalize("j.o.h.n@googlemail.com"),
            "john@googlemail.com"
        );
        assert_eq!(email_normalize(""), "");
        assert_eq!(email_normalize("  "), "  ");
    }

    #[test]
    fn extract_domain() {
        assert_eq!(
            email_extract_domain("x@Foo.COM"),
            Some("foo.com".to_string())
        );
        assert_eq!(email_extract_domain("noat"), None);
        assert_eq!(email_extract_domain("trailing@"), None);
        assert_eq!(email_extract_domain("a@b@c.com"), Some("c.com".to_string()));
    }

    #[test]
    fn canonical() {
        // googlemail.com aliases to gmail.com (dots already stripped by normalize).
        assert_eq!(
            email_canonical("j.o.h.n@googlemail.com"),
            "john@gmail.com"
        );
        assert_eq!(email_canonical("John.Doe+tag@Gmail.com"), "johndoe@gmail.com");
        // non-gmail domains pass through normalize only.
        assert_eq!(email_canonical("a+b@x.com"), "a@x.com");
        assert_eq!(email_canonical("notanemail"), "notanemail");
        assert_eq!(email_canonical(""), "");
    }

    #[test]
    fn mask() {
        assert_eq!(email_mask("John@Example.com"), Some("j***@example.com".to_string()));
        assert_eq!(email_mask("a@b.co"), Some("a@b.co".to_string())); // 1-char local
        assert_eq!(email_mask("j.doe@x.com"), Some("j****@x.com".to_string()));
        assert_eq!(email_mask("a@b@c.com"), Some("a**@c.com".to_string())); // last '@'
        assert_eq!(email_mask("notanemail"), None);
        assert_eq!(email_mask("@no-local.com"), None);
        assert_eq!(email_mask("no-domain@"), None);
        assert_eq!(email_mask(""), None);
    }

    #[test]
    fn validate() {
        assert_eq!(email_validate("a@b.co"), Some(true));
        assert_eq!(email_validate("a@b"), Some(false));
        assert_eq!(email_validate("a b@c.com"), Some(false));
        assert_eq!(email_validate("a@@b.com"), Some(false));
        assert_eq!(email_validate(""), Some(false));
        assert_eq!(email_validate("   "), Some(false));
        assert_eq!(email_validate("@no-local.com"), Some(false));
        assert_eq!(email_validate("no-domain@"), Some(false));
        assert_eq!(email_validate("valid@example.com"), Some(true));
        assert_eq!(
            email_validate("also.valid+tag@sub.example.co.uk"),
            Some(true)
        );
    }
}
