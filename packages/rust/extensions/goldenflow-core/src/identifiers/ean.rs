use super::strip_sep;

/// True if `s` is a structurally valid EAN-8, UPC-A (12), or EAN-13 (GTIN
/// mod-10 checksum verified). Separators tolerated.
pub fn ean_validate(s: &str) -> bool {
    let t = strip_sep(s);
    match t.len() {
        8 | 12 | 13 => gtin_checksum_ok(&t),
        _ => false,
    }
}

/// GTIN mod-10 check: digits must be all-ASCII-digit. Walking from the
/// rightmost DATA digit (i.e. excluding the final check digit) leftward,
/// apply weights alternating 3, 1, 3, 1, ...; the check digit must equal
/// `(10 - (weighted_sum % 10)) % 10`.
fn gtin_checksum_ok(t: &str) -> bool {
    let chars: Vec<char> = t.chars().collect();
    if !chars.iter().all(|c| c.is_ascii_digit()) {
        return false;
    }
    let (data, check) = chars.split_at(chars.len() - 1);
    let check_digit = check[0] as u32 - '0' as u32;
    let mut sum: u32 = 0;
    for (i, c) in data.iter().rev().enumerate() {
        let d = *c as u32 - '0' as u32;
        let weight = if i % 2 == 0 { 3 } else { 1 };
        sum += d * weight;
    }
    let computed = (10 - (sum % 10)) % 10;
    computed == check_digit
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn valid_ean_upc() {
        assert!(ean_validate("4006381333931")); // EAN-13
        assert!(ean_validate("73513537")); // EAN-8
        assert!(ean_validate("036000291452")); // UPC-A
    }

    #[test]
    fn invalid_ean_upc() {
        assert!(!ean_validate("4006381333930")); // bad check digit
        assert!(!ean_validate("12345")); // wrong length
        assert!(!ean_validate("40063813339a1")); // non-digit
        assert!(!ean_validate("")); // empty
    }
}
