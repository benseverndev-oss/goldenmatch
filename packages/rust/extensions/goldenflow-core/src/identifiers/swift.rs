//! SWIFT/BIC (ISO 9362) structural validation. No checksum exists for BIC --
//! validity is purely structural: 8 or 11 chars, institution (4 letters) +
//! country (2 letters) + location (2 alnum) + optional branch (3 alnum).

/// Normalize: uppercase + remove ASCII spaces only. Unlike other identifiers
/// in this module, `-`/`.` are NOT stripped -- a well-formed BIC never
/// contains them, and silently stripping them could let a malformed value
/// pass structural validation.
fn normalize(s: &str) -> String {
    s.chars()
        .filter(|c| *c != ' ')
        .collect::<String>()
        .to_ascii_uppercase()
}

fn is_alpha_upper(c: char) -> bool {
    c.is_ascii_uppercase()
}

fn is_alnum_upper(c: char) -> bool {
    c.is_ascii_uppercase() || c.is_ascii_digit()
}

/// True if `s` is a structurally valid SWIFT/BIC code (length 8 or 11).
pub fn swift_validate(s: &str) -> bool {
    let t = normalize(s);
    let chars: Vec<char> = t.chars().collect();
    let len = chars.len();
    if len != 8 && len != 11 {
        return false;
    }
    if !chars[0..4].iter().all(|&c| is_alpha_upper(c)) {
        return false;
    }
    if !chars[4..6].iter().all(|&c| is_alpha_upper(c)) {
        return false;
    }
    if !chars[6..8].iter().all(|&c| is_alnum_upper(c)) {
        return false;
    }
    if len == 11 && !chars[8..11].iter().all(|&c| is_alnum_upper(c)) {
        return false;
    }
    true
}

/// Normalized uppercase form of a valid SWIFT/BIC; `None` if invalid.
pub fn swift_format(s: &str) -> Option<String> {
    if !swift_validate(s) {
        return None;
    }
    Some(normalize(s))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn valid_swift_codes() {
        assert!(swift_validate("DEUTDEFF"));
        assert!(swift_validate("DEUTDEFF500"));
        assert!(swift_validate("NEDSZAJJXXX"));
        assert!(swift_validate("deutdeff"));
    }

    #[test]
    fn invalid_swift_codes() {
        assert!(!swift_validate("DEUTDEFF5")); // len 9
        assert!(!swift_validate("DEUT1EFF")); // digit in institution
        assert!(!swift_validate("1234DEFF")); // digits in institution
        assert!(!swift_validate("")); // empty
    }

    #[test]
    fn format_valid_and_invalid() {
        assert_eq!(swift_format("deutdeff").as_deref(), Some("DEUTDEFF"));
        assert_eq!(swift_format("DEUTDEFF500").as_deref(), Some("DEUTDEFF500"));
        assert_eq!(swift_format("DEUTDEFF5"), None);
        assert_eq!(swift_format(""), None);
    }
}
