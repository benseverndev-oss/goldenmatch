//! IMEI (International Mobile Equipment Identity) checksum validation.
//! Structural: exactly 15 ASCII digits. Checksum: Luhn (reuses the shared
//! `luhn::luhn_ok` -- one Luhn implementation for both `cc` and `imei`).

use super::strip_sep;

/// True if `s` normalizes to exactly 15 ASCII digits and passes the Luhn
/// checksum.
pub fn imei_validate(s: &str) -> bool {
    let t = strip_sep(s);
    if t.len() != 15 || !t.bytes().all(|b| b.is_ascii_digit()) {
        return false;
    }
    super::luhn::luhn_ok(&t)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn valid_imei_numbers() {
        assert!(imei_validate("490154203237518"));
        assert!(imei_validate("356938035643809"));
    }

    #[test]
    fn invalid_imei_numbers() {
        assert!(!imei_validate("490154203237519")); // bad Luhn
        assert!(!imei_validate("12345")); // wrong length
        assert!(!imei_validate("")); // empty
    }
}
