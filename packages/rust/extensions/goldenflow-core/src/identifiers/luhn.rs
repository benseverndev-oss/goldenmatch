use super::strip_sep;

/// Luhn checksum over an all-ASCII-digit string. Caller guarantees digits.
pub(crate) fn luhn_ok(digits: &str) -> bool {
    let mut sum = 0u32;
    let mut dbl = false;
    for c in digits.bytes().rev() {
        let mut d = (c - b'0') as u32;
        if dbl {
            d *= 2;
            if d > 9 {
                d -= 9;
            }
        }
        sum += d;
        dbl = !dbl;
    }
    sum.is_multiple_of(10)
}

fn normalized_digits(s: &str) -> Option<String> {
    let t = strip_sep(s);
    if t.is_empty() || !t.bytes().all(|b| b.is_ascii_digit()) {
        return None;
    }
    Some(t)
}

pub fn cc_validate(s: &str) -> bool {
    match normalized_digits(s) {
        Some(d) => (13..=19).contains(&d.len()) && luhn_ok(&d),
        None => false,
    }
}

/// Generic Luhn (mod-10) validation over any separator-tolerant digit string
/// (not credit-card-length-bound). Empty / non-digit input -> false.
pub fn luhn_validate(s: &str) -> bool {
    match normalized_digits(s) {
        Some(d) => luhn_ok(&d),
        None => false,
    }
}

/// Detect the card brand from a (separator-tolerant) card number by
/// prefix + length: `visa` / `mastercard` / `amex` / `discover` / `diners` /
/// `jcb`, else `None`. A brand HINT (prefix/length only) -- does NOT check the
/// Luhn digit (use `cc_validate` for that).
pub fn cc_brand(s: &str) -> Option<String> {
    let d = normalized_digits(s)?;
    let len = d.len();
    // First `k` digits as a number (0 when the string is shorter than k).
    let first = |k: usize| -> u32 {
        if len >= k {
            d[..k].parse().unwrap_or(0)
        } else {
            0
        }
    };
    let (f2, f3, f4) = (first(2), first(3), first(4));
    let brand = if len == 15 && (f2 == 34 || f2 == 37) {
        "amex"
    } else if len == 14 && (f2 == 36 || f2 == 38 || f2 == 39 || (300..=305).contains(&f3)) {
        "diners"
    } else if len == 16 && (3528..=3589).contains(&f4) {
        "jcb"
    } else if len == 16 && (d.starts_with("6011") || f2 == 65 || (644..=649).contains(&f3)) {
        "discover"
    } else if len == 16 && ((51..=55).contains(&f2) || (2221..=2720).contains(&f4)) {
        "mastercard"
    } else if d.starts_with('4') && matches!(len, 13 | 16 | 19) {
        "visa"
    } else {
        return None;
    };
    Some(brand.to_string())
}

/// Group digits by brand: Amex (starts 34/37, len 15) -> 4-6-5; else 4-4-4-4...
pub fn cc_format(s: &str) -> Option<String> {
    let d = normalized_digits(s)?;
    if !((13..=19).contains(&d.len()) && luhn_ok(&d)) {
        return None;
    }
    let groups: &[usize] = if d.len() == 15 && (d.starts_with("34") || d.starts_with("37")) {
        &[4, 6, 5]
    } else {
        &[4, 4, 4, 4, 4] // 4-digit groups, remainder trails
    };
    Some(group(&d, groups))
}

pub fn cc_mask(s: &str) -> Option<String> {
    let d = normalized_digits(s)?;
    if !(13..=19).contains(&d.len()) {
        return None;
    }
    let last4 = &d[d.len() - 4..];
    Some(format!("{}{}", "*".repeat(d.len() - 4), last4))
}

/// Split `d` into the given group sizes joined by spaces; any leftover after the
/// listed groups is split into further 4s (keeps 16/19-digit cards grouped).
fn group(d: &str, sizes: &[usize]) -> String {
    let mut out = Vec::new();
    let mut i = 0;
    for &n in sizes {
        if i >= d.len() {
            break;
        }
        let end = (i + n).min(d.len());
        out.push(&d[i..end]);
        i = end;
    }
    while i < d.len() {
        let end = (i + 4).min(d.len());
        out.push(&d[i..end]);
        i = end;
    }
    out.join(" ")
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn valid_cards() {
        assert!(cc_validate("4242 4242 4242 4242")); // Visa test
        assert!(cc_validate("5555555555554444")); // Mastercard
        assert!(cc_validate("378282246310005")); // Amex (15)
    }
    #[test]
    fn invalid_cards() {
        assert!(!cc_validate("4242424242424241")); // bad checksum
        assert!(!cc_validate("1234")); // too short
        assert!(!cc_validate("4242abcd42424242")); // non-digit
    }
    #[test]
    fn format_and_mask() {
        assert_eq!(
            cc_format("4242424242424242").as_deref(),
            Some("4242 4242 4242 4242")
        );
        assert_eq!(
            cc_format("378282246310005").as_deref(),
            Some("3782 822463 10005")
        ); // Amex 4-6-5
        assert_eq!(cc_format("4242424242424241"), None); // invalid -> None
        assert_eq!(
            cc_mask("4242424242424242").as_deref(),
            Some("************4242")
        );
        assert_eq!(cc_mask("bogus"), None);
    }
}
