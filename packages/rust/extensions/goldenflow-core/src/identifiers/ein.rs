//! US Employer Identification Number formatting: when the input has exactly 9
//! ASCII digits, format to `XX-XXXXXXX`; otherwise preserve the input
//! unchanged. Byte-identical to `identifiers.py::ein_format`.

use super::ascii_digits;

/// `XX-XXXXXXX` if `s` normalizes to exactly 9 ASCII digits, else `s` unchanged.
pub fn ein_format(s: &str) -> String {
    let d = ascii_digits(s);
    if d.len() != 9 {
        return s.to_string();
    }
    format!("{}-{}", &d[..2], &d[2..])
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn formats_nine_digits() {
        assert_eq!(ein_format("123456789"), "12-3456789");
        assert_eq!(ein_format("12-3456789"), "12-3456789");
        assert_eq!(ein_format(" 12 3456789 "), "12-3456789");
    }

    #[test]
    fn preserves_non_nine_digit() {
        assert_eq!(ein_format("12345"), "12345");
        assert_eq!(ein_format(""), "");
    }
}
