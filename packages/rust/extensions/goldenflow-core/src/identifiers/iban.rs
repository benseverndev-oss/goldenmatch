use super::strip_sep;

/// True if `s` is a structurally valid IBAN (length 15-34, country code +
/// check digits + alphanumeric BBAN) that passes the ISO 7064 mod-97 check.
pub fn iban_validate(s: &str) -> bool {
    let t = strip_sep(s).to_ascii_uppercase();
    let chars: Vec<char> = t.chars().collect();
    let len = chars.len();
    if !(15..=34).contains(&len) {
        return false;
    }
    if !chars[0].is_ascii_alphabetic() || !chars[1].is_ascii_alphabetic() {
        return false;
    }
    if !chars[2].is_ascii_digit() || !chars[3].is_ascii_digit() {
        return false;
    }
    if !chars[4..].iter().all(|c| c.is_ascii_alphanumeric()) {
        return false;
    }
    mod97_check(&chars)
}

/// ISO 7064 mod-97 check: move the first 4 chars to the end, expand each
/// letter to its two-digit value (A=10 .. Z=35), fold the resulting decimal
/// string mod 97 digit-by-digit (avoids bigints), and require remainder 1.
fn mod97_check(chars: &[char]) -> bool {
    let mut acc: u32 = 0;
    let rearranged = chars[4..].iter().chain(chars[0..4].iter());
    for &c in rearranged {
        if c.is_ascii_digit() {
            let d = c as u32 - '0' as u32;
            acc = (acc * 10 + d) % 97;
        } else {
            let v = (c as u32 - 'A' as u32) + 10;
            acc = (acc * 100 + v) % 97;
        }
    }
    acc == 1
}

/// Group a valid IBAN into 4-char blocks separated by single spaces (the
/// conventional printed IBAN format); `None` if invalid.
pub fn iban_format(s: &str) -> Option<String> {
    if !iban_validate(s) {
        return None;
    }
    let t = strip_sep(s).to_ascii_uppercase();
    Some(group4(&t))
}

fn group4(s: &str) -> String {
    let chars: Vec<char> = s.chars().collect();
    let mut out = Vec::new();
    let mut i = 0;
    while i < chars.len() {
        let end = (i + 4).min(chars.len());
        out.push(chars[i..end].iter().collect::<String>());
        i = end;
    }
    out.join(" ")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn valid_ibans() {
        assert!(iban_validate("GB82 WEST 1234 5698 7654 32"));
        assert!(iban_validate("DE89370400440532013000"));
        assert!(iban_validate("FR1420041010050500013M02606"));
    }

    #[test]
    fn invalid_ibans() {
        assert!(!iban_validate("GB82WEST12345698765433")); // bad check digits
        assert!(!iban_validate("XX00")); // too short
        assert!(!iban_validate("")); // empty
    }

    #[test]
    fn format_valid_and_invalid() {
        assert_eq!(
            iban_format("DE89370400440532013000").as_deref(),
            Some("DE89 3704 0044 0532 0130 00")
        );
        assert_eq!(
            iban_format("GB82 WEST 1234 5698 7654 32").as_deref(),
            Some("GB82 WEST 1234 5698 7654 32")
        );
        assert_eq!(iban_format("GB82WEST12345698765433"), None);
        assert_eq!(iban_format("XX00"), None);
        assert_eq!(iban_format(""), None);
    }
}
