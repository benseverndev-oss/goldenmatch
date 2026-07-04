//! ABA routing number (US bank routing transit number) checksum validation.
//! Structural: exactly 9 ASCII digits. Checksum per the standard ABA formula.

use super::strip_sep;

/// True if `s` normalizes to exactly 9 ASCII digits and passes the ABA
/// routing-number checksum: `3*(d0+d3+d6) + 7*(d1+d4+d7) + 1*(d2+d5+d8)`
/// is a multiple of 10.
pub fn aba_validate(s: &str) -> bool {
    let t = strip_sep(s);
    if t.len() != 9 || !t.bytes().all(|b| b.is_ascii_digit()) {
        return false;
    }
    let d: Vec<u32> = t.bytes().map(|b| (b - b'0') as u32).collect();
    let sum = 3 * (d[0] + d[3] + d[6]) + 7 * (d[1] + d[4] + d[7]) + (d[2] + d[5] + d[8]);
    sum.is_multiple_of(10)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn valid_aba_numbers() {
        assert!(aba_validate("011000015"));
        assert!(aba_validate("021000021"));
        assert!(aba_validate("122105155"));
    }

    #[test]
    fn invalid_aba_numbers() {
        assert!(!aba_validate("011000016")); // bad checksum
        assert!(!aba_validate("12345")); // wrong length
        assert!(!aba_validate("01100001a")); // non-digit
        assert!(!aba_validate("")); // empty
    }
}
