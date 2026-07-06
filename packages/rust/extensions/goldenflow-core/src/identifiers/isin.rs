//! ISIN (ISO 6166) validation: 2-letter country code + 9 alphanumeric NSIN +
//! 1 check digit, verified by the Luhn algorithm over the letter-expanded
//! digit string (A=10..Z=35, each letter emitted as two digits). Reference
//! implementation; Python/TS fallbacks reproduce these bytes exactly.

use super::luhn::luhn_ok;

/// Validate an ISIN. Tolerates ASCII spaces and `-`; case-insensitive. Empty /
/// wrong-length / non-alphanumeric / bad-country-code / bad-checksum -> false.
pub fn isin_validate(s: &str) -> bool {
    let t: String = s
        .chars()
        .filter(|c| !c.is_whitespace() && *c != '-')
        .flat_map(char::to_uppercase)
        .collect();
    if t.len() != 12 {
        return false;
    }
    let bytes = t.as_bytes();
    // First two chars must be letters (country code).
    if !bytes[..2].iter().all(u8::is_ascii_uppercase) {
        return false;
    }
    // Every char must be an ASCII digit or uppercase letter.
    if !bytes.iter().all(|b| b.is_ascii_digit() || b.is_ascii_uppercase()) {
        return false;
    }
    // Expand: digit -> itself; letter -> (c - 'A' + 10) as two digits.
    let mut expanded = String::with_capacity(24);
    for &b in bytes {
        if b.is_ascii_digit() {
            expanded.push(b as char);
        } else {
            let v = (b - b'A') as u32 + 10; // 10..=35
            expanded.push_str(&v.to_string());
        }
    }
    luhn_ok(&expanded)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn valid() {
        assert!(isin_validate("US0378331005")); // Apple
        assert!(isin_validate("us0378331005")); // lowercase
        assert!(isin_validate("US-0378331005")); // dashed
        assert!(isin_validate("AU0000XVGZA3")); // letters in NSIN
        assert!(isin_validate("GB0002634946")); // BAE
    }

    #[test]
    fn invalid() {
        assert!(!isin_validate("US0378331006")); // bad check digit
        assert!(!isin_validate("0378331005")); // no country code / too short
        assert!(!isin_validate("US037833100")); // 11 chars
        assert!(!isin_validate("1S0378331005")); // non-letter country code
        assert!(!isin_validate("US03783310_5")); // non-alphanumeric
        assert!(!isin_validate(""));
    }
}
