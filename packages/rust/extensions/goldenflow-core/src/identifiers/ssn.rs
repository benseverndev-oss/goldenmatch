//! US Social Security Number formatting + masking. Pure string ops (no SSA
//! area/group/serial validity rules): when the input has exactly 9 ASCII
//! digits, format to `XXX-XX-XXXX` / mask to `***-**-XXXX` (last 4 visible);
//! otherwise preserve the input unchanged. Byte-identical to
//! `identifiers.py::ssn_format` / `ssn_mask`.

use super::ascii_digits;

/// `XXX-XX-XXXX` if `s` normalizes to exactly 9 ASCII digits, else `s` unchanged.
pub fn ssn_format(s: &str) -> String {
    let d = ascii_digits(s);
    if d.len() != 9 {
        return s.to_string();
    }
    format!("{}-{}-{}", &d[..3], &d[3..5], &d[5..])
}

/// `***-**-XXXX` (last 4 visible) if 9 ASCII digits, else `s` unchanged.
pub fn ssn_mask(s: &str) -> String {
    let d = ascii_digits(s);
    if d.len() != 9 {
        return s.to_string();
    }
    format!("***-**-{}", &d[5..])
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn formats_and_masks_nine_digits() {
        assert_eq!(ssn_format("123456789"), "123-45-6789");
        assert_eq!(ssn_format("123-45-6789"), "123-45-6789");
        assert_eq!(ssn_format(" 123 45 6789 "), "123-45-6789");
        assert_eq!(ssn_mask("123456789"), "***-**-6789");
        assert_eq!(ssn_mask("123-45-6789"), "***-**-6789");
    }

    #[test]
    fn preserves_non_nine_digit() {
        assert_eq!(ssn_format("12345"), "12345");
        assert_eq!(ssn_mask("not an ssn"), "not an ssn");
        assert_eq!(ssn_format(""), "");
    }
}
