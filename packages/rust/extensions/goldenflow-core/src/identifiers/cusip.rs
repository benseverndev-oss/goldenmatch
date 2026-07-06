//! CUSIP validation: 9 chars (8 alphanumeric issue/issuer + 1 check digit),
//! verified by the weighted mod-10 CUSIP algorithm. Reference implementation;
//! Python/TS fallbacks reproduce these bytes exactly.

/// Value of a CUSIP char: digits 0-9, letters A=10..Z=35, and the special
/// chars `*`=36, `@`=37, `#`=38. `None` for anything else.
fn cusip_value(b: u8) -> Option<u32> {
    match b {
        b'0'..=b'9' => Some((b - b'0') as u32),
        b'A'..=b'Z' => Some((b - b'A') as u32 + 10),
        b'*' => Some(36),
        b'@' => Some(37),
        b'#' => Some(38),
        _ => None,
    }
}

/// Validate a CUSIP. Tolerates ASCII whitespace; case-insensitive. Empty /
/// wrong-length / bad-char / non-digit-check / bad-checksum -> false.
pub fn cusip_validate(s: &str) -> bool {
    let t: String = s
        .chars()
        .filter(|c| !c.is_whitespace())
        .flat_map(char::to_uppercase)
        .collect();
    if t.len() != 9 {
        return false;
    }
    let bytes = t.as_bytes();
    // The check digit must be an ASCII digit.
    if !bytes[8].is_ascii_digit() {
        return false;
    }
    let mut sum = 0u32;
    for (i, &b) in bytes[..8].iter().enumerate() {
        let mut v = match cusip_value(b) {
            Some(v) => v,
            None => return false,
        };
        if i % 2 == 1 {
            v *= 2;
        }
        sum += v / 10 + v % 10;
    }
    let check = (10 - (sum % 10)) % 10;
    check == (bytes[8] - b'0') as u32
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn valid() {
        assert!(cusip_validate("037833100")); // Apple
        assert!(cusip_validate("037833 100")); // spaced
        assert!(cusip_validate("38259P508")); // Google (letter in body)
        assert!(cusip_validate("594918104")); // Microsoft
    }

    #[test]
    fn invalid() {
        assert!(!cusip_validate("037833101")); // bad check digit
        assert!(!cusip_validate("03783310")); // 8 chars
        assert!(!cusip_validate("38259P50X")); // non-digit check
        assert!(!cusip_validate("037833_00")); // bad char
        assert!(!cusip_validate(""));
    }
}
