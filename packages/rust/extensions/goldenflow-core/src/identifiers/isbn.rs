use super::strip_sep;

/// True if `s` is a structurally valid ISBN-10 or ISBN-13 (separators
/// tolerated, checksum verified).
pub fn isbn_validate(s: &str) -> bool {
    let t = normalize_case(s);
    match t.len() {
        10 => isbn10_checksum_ok(&t),
        13 => isbn13_checksum_ok(&t),
        _ => false,
    }
}

/// Canonicalize a valid ISBN-10 or ISBN-13 to its 13-digit form (no
/// separators); `None` if invalid. An ISBN-10 is converted by prefixing its
/// first 9 digits with "978" and recomputing the ISBN-13 check digit.
pub fn isbn_normalize(s: &str) -> Option<String> {
    let t = normalize_case(s);
    match t.len() {
        10 => {
            if !isbn10_checksum_ok(&t) {
                return None;
            }
            let prefix9 = &t[0..9];
            let twelve = format!("978{prefix9}");
            let check = isbn13_check_digit(&twelve);
            Some(format!("{twelve}{check}"))
        }
        13 => {
            if !isbn13_checksum_ok(&t) {
                return None;
            }
            Some(t)
        }
        _ => None,
    }
}

/// Strip separators; uppercase a trailing 'x' (the only non-digit char an
/// ISBN-10 check digit may carry).
fn normalize_case(s: &str) -> String {
    let t = strip_sep(s);
    let mut chars: Vec<char> = t.chars().collect();
    if let Some(last) = chars.last_mut() {
        if *last == 'x' {
            *last = 'X';
        }
    }
    chars.into_iter().collect()
}

fn isbn10_checksum_ok(t: &str) -> bool {
    let chars: Vec<char> = t.chars().collect();
    if chars.len() != 10 {
        return false;
    }
    if !chars[0..9].iter().all(|c| c.is_ascii_digit()) {
        return false;
    }
    let last = chars[9];
    if !(last.is_ascii_digit() || last == 'X') {
        return false;
    }
    let mut sum: u32 = 0;
    for (i, &c) in chars.iter().enumerate() {
        let d = if c == 'X' { 10 } else { c as u32 - '0' as u32 };
        sum += d * (10 - i as u32);
    }
    sum % 11 == 0
}

fn isbn13_checksum_ok(t: &str) -> bool {
    let chars: Vec<char> = t.chars().collect();
    if chars.len() != 13 || !chars.iter().all(|c| c.is_ascii_digit()) {
        return false;
    }
    let mut sum: u32 = 0;
    for (i, &c) in chars.iter().enumerate() {
        let d = c as u32 - '0' as u32;
        let weight = if i % 2 == 0 { 1 } else { 3 };
        sum += d * weight;
    }
    sum % 10 == 0
}

/// Compute the ISBN-13 check digit for a 12-digit prefix (ASCII digits).
fn isbn13_check_digit(twelve: &str) -> char {
    let mut sum: u32 = 0;
    for (i, c) in twelve.chars().enumerate() {
        let d = c as u32 - '0' as u32;
        let weight = if i % 2 == 0 { 1 } else { 3 };
        sum += d * weight;
    }
    let check = (10 - (sum % 10)) % 10;
    char::from_digit(check, 10).unwrap()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn valid_isbns() {
        assert!(isbn_validate("0-306-40615-2")); // ISBN-10
        assert!(isbn_validate("978-0-306-40615-7")); // ISBN-13
        assert!(isbn_validate("0-19-852663-6")); // ISBN-10, checked
    }

    #[test]
    fn invalid_isbns() {
        assert!(!isbn_validate("0306406153")); // bad check digit
        assert!(!isbn_validate("")); // empty
        assert!(!isbn_validate("12345")); // wrong length
    }

    #[test]
    fn normalize_to_isbn13() {
        assert_eq!(
            isbn_normalize("0306406152").as_deref(),
            Some("9780306406157")
        );
        assert_eq!(
            isbn_normalize("978-0-306-40615-7").as_deref(),
            Some("9780306406157")
        );
        assert_eq!(isbn_normalize("0306406153"), None);
        assert_eq!(isbn_normalize(""), None);
    }
}
