//! NPI (US National Provider Identifier) validation: 10 digits, verified by the
//! Luhn algorithm over the string `"80840"` + the 10 digits (the ISO 7812
//! prefix assigned to the NPI). Reference implementation; Python/TS fallbacks
//! reproduce these bytes exactly.

use super::luhn::luhn_ok;

/// Validate an NPI. Tolerates ASCII spaces and `-`. Empty / wrong-length /
/// non-digit / bad-checksum -> false.
pub fn npi_validate(s: &str) -> bool {
    let d: String = s
        .chars()
        .filter(|c| !c.is_whitespace() && *c != '-')
        .collect();
    if d.len() != 10 || !d.bytes().all(|b| b.is_ascii_digit()) {
        return false;
    }
    // Luhn over the "80840" prefix + the full 10 digits (check digit included).
    luhn_ok(&format!("80840{d}"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn valid() {
        assert!(npi_validate("1234567893")); // canonical NPI example
        assert!(npi_validate("1245319599")); // real-format example
        assert!(npi_validate("123-456-7893")); // dashed
    }

    #[test]
    fn invalid() {
        assert!(!npi_validate("1234567890")); // bad check digit
        assert!(!npi_validate("123456789")); // 9 digits
        assert!(!npi_validate("12345678933")); // 11 digits
        assert!(!npi_validate("123456789a")); // non-digit
        assert!(!npi_validate(""));
    }
}
