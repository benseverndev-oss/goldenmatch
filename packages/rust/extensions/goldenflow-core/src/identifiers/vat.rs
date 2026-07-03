use super::strip_sep;

// CHECKSUM COVERAGE: DE, IT (structural-only: all other supported prefixes)
//
// Bounded scope (Wave 0b, Task 5): every EU member-state VAT prefix below is
// STRUCTURALLY validated (country prefix + length + per-position charset),
// but only Germany (DE, ISO 7064 mod 11,10) and Italy (IT, partita IVA Luhn)
// additionally run a checksum. The other 25 supported prefixes pass on
// structure alone -- checksum coverage may grow later without changing this
// contract. Unsupported/unknown prefixes (including a bare "GR" -- Greece's
// VAT prefix is the well-known quirk "EL") -> false.

/// Per-character class for a structural rule.
#[derive(Clone, Copy)]
enum Pos {
    /// ASCII digit.
    Digit,
    /// ASCII letter.
    Alpha,
    /// ASCII letter or digit.
    Alnum,
    /// The exact literal character (e.g. NL's "B" separator, AT's "U").
    Literal(char),
}

fn pos_ok(pos: Pos, c: char) -> bool {
    match pos {
        Pos::Digit => c.is_ascii_digit(),
        Pos::Alpha => c.is_ascii_alphabetic(),
        Pos::Alnum => c.is_ascii_alphanumeric(),
        Pos::Literal(l) => c == l,
    }
}

fn fixed_ok(pattern: &[Pos], suffix: &[char]) -> bool {
    suffix.len() == pattern.len()
        && suffix
            .iter()
            .zip(pattern.iter())
            .all(|(&c, &p)| pos_ok(p, c))
}

/// Structural rule for one EU-member VAT prefix (the part after the 2-letter
/// country code).
enum Rule {
    /// Exactly one fixed per-position pattern.
    Fixed(&'static [Pos]),
    /// Any one of several fixed per-position patterns (format variants).
    OneOf(&'static [&'static [Pos]]),
    /// All-digit, length in `[min, max]` (Romania is the one variable-length
    /// EU VAT format: 2-10 digits).
    Digits { min: usize, max: usize },
}

const D: Pos = Pos::Digit;
const A: Pos = Pos::Alpha;
const N: Pos = Pos::Alnum;

const AT: [Pos; 9] = [Pos::Literal('U'), D, D, D, D, D, D, D, D];
const BE: [Pos; 10] = [D, D, D, D, D, D, D, D, D, D];
const CY: [Pos; 9] = [D, D, D, D, D, D, D, D, A];
const DE: [Pos; 9] = [D, D, D, D, D, D, D, D, D];
const DK: [Pos; 8] = [D, D, D, D, D, D, D, D];
const EE: [Pos; 9] = [D, D, D, D, D, D, D, D, D];
const EL: [Pos; 9] = [D, D, D, D, D, D, D, D, D];
const ES: [Pos; 9] = [N, D, D, D, D, D, D, D, N];
const FI: [Pos; 8] = [D, D, D, D, D, D, D, D];
const FR: [Pos; 11] = [N, N, D, D, D, D, D, D, D, D, D];
const HR: [Pos; 11] = [D, D, D, D, D, D, D, D, D, D, D];
const HU: [Pos; 8] = [D, D, D, D, D, D, D, D];
const IE_8: [Pos; 8] = [D, N, D, D, D, D, D, A];
const IE_9: [Pos; 9] = [D, N, D, D, D, D, D, A, A];
const IT: [Pos; 11] = [D, D, D, D, D, D, D, D, D, D, D];
const LT_9: [Pos; 9] = [D, D, D, D, D, D, D, D, D];
const LT_12: [Pos; 12] = [D, D, D, D, D, D, D, D, D, D, D, D];
const LU: [Pos; 8] = [D, D, D, D, D, D, D, D];
const LV: [Pos; 11] = [D, D, D, D, D, D, D, D, D, D, D];
const MT: [Pos; 8] = [D, D, D, D, D, D, D, D];
const NL: [Pos; 12] = [D, D, D, D, D, D, D, D, D, Pos::Literal('B'), D, D];
const PL: [Pos; 10] = [D, D, D, D, D, D, D, D, D, D];
const PT: [Pos; 9] = [D, D, D, D, D, D, D, D, D];
const SE: [Pos; 12] = [D, D, D, D, D, D, D, D, D, D, D, D];
const SI: [Pos; 8] = [D, D, D, D, D, D, D, D];
const SK: [Pos; 10] = [D, D, D, D, D, D, D, D, D, D];

/// The 27 supported EU member-state VAT prefixes, encoding each one's
/// standard printed structural format. `EL` is the well-known Greek quirk
/// (Greece's ISO country code is `GR`, but its VAT prefix is `EL`).
fn structural_rule(prefix: &str) -> Option<Rule> {
    Some(match prefix {
        "AT" => Rule::Fixed(&AT),
        "BE" => Rule::Fixed(&BE),
        "BG" => Rule::Digits { min: 9, max: 10 },
        "CY" => Rule::Fixed(&CY),
        "CZ" => Rule::Digits { min: 8, max: 10 },
        "DE" => Rule::Fixed(&DE),
        "DK" => Rule::Fixed(&DK),
        "EE" => Rule::Fixed(&EE),
        "EL" => Rule::Fixed(&EL),
        "ES" => Rule::Fixed(&ES),
        "FI" => Rule::Fixed(&FI),
        "FR" => Rule::Fixed(&FR),
        "HR" => Rule::Fixed(&HR),
        "HU" => Rule::Fixed(&HU),
        "IE" => Rule::OneOf(&[&IE_8, &IE_9]),
        "IT" => Rule::Fixed(&IT),
        "LT" => Rule::OneOf(&[&LT_9, &LT_12]),
        "LU" => Rule::Fixed(&LU),
        "LV" => Rule::Fixed(&LV),
        "MT" => Rule::Fixed(&MT),
        "NL" => Rule::Fixed(&NL),
        "PL" => Rule::Fixed(&PL),
        "PT" => Rule::Fixed(&PT),
        "RO" => Rule::Digits { min: 2, max: 10 },
        "SE" => Rule::Fixed(&SE),
        "SI" => Rule::Fixed(&SI),
        "SK" => Rule::Fixed(&SK),
        _ => return None,
    })
}

fn structural_ok(rule: &Rule, suffix: &[char]) -> bool {
    match rule {
        Rule::Fixed(pattern) => fixed_ok(pattern, suffix),
        Rule::OneOf(patterns) => patterns.iter().any(|p| fixed_ok(p, suffix)),
        Rule::Digits { min, max } => {
            (*min..=*max).contains(&suffix.len()) && suffix.iter().all(|c| c.is_ascii_digit())
        }
    }
}

/// German checksum (ISO 7064 MOD 11,10) over the 9 digits.
fn de_checksum_ok(digits: &[u32]) -> bool {
    if digits.len() != 9 {
        return false;
    }
    let mut p: u32 = 10;
    for &d in &digits[0..8] {
        let mut m = (d + p) % 10;
        if m == 0 {
            m = 10;
        }
        p = (2 * m) % 11;
    }
    let mut check = 11 - p;
    if check == 10 {
        check = 0;
    }
    check == digits[8]
}

/// Italian checksum (partita IVA, Luhn) over the 11 digits.
fn it_checksum_ok(digits: &[u32]) -> bool {
    if digits.len() != 11 {
        return false;
    }
    let mut sum: u32 = 0;
    for (i, &d) in digits[0..10].iter().enumerate() {
        if i % 2 == 0 {
            sum += d;
        } else {
            let x = d * 2;
            sum += if x > 9 { x - 9 } else { x };
        }
    }
    let check = (10 - (sum % 10)) % 10;
    check == digits[10]
}

fn digits_of(suffix: &[char]) -> Vec<u32> {
    suffix.iter().map(|c| c.to_digit(10).unwrap_or(0)).collect()
}

/// Uppercase + strip separators; split into a 2-letter country prefix and the
/// rest, or `None` if too short / prefix isn't two letters.
fn split_prefix(s: &str) -> Option<(String, Vec<char>)> {
    let t = strip_sep(s).to_ascii_uppercase();
    let chars: Vec<char> = t.chars().collect();
    if chars.len() < 3 {
        return None;
    }
    if !chars[0].is_ascii_alphabetic() || !chars[1].is_ascii_alphabetic() {
        return None;
    }
    let prefix: String = chars[0..2].iter().collect();
    Some((prefix, chars[2..].to_vec()))
}

/// True if `s` is a structurally valid EU VAT number for one of the 27
/// supported member-state prefixes, with an additional checksum for DE and IT
/// (see the module-level CHECKSUM COVERAGE comment for the bound).
pub fn vat_validate(s: &str) -> bool {
    let Some((prefix, suffix)) = split_prefix(s) else {
        return false;
    };
    let Some(rule) = structural_rule(&prefix) else {
        return false;
    };
    if !structural_ok(&rule, &suffix) {
        return false;
    }
    match prefix.as_str() {
        "DE" => de_checksum_ok(&digits_of(&suffix)),
        "IT" => it_checksum_ok(&digits_of(&suffix)),
        _ => true,
    }
}

/// Normalize a valid EU VAT number to its compact uppercase form (prefix kept,
/// separators stripped); `None` if structurally invalid (or checksum-invalid
/// for DE/IT).
pub fn vat_format(s: &str) -> Option<String> {
    if !vat_validate(s) {
        return None;
    }
    Some(strip_sep(s).to_ascii_uppercase())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn valid_checksummed() {
        assert!(vat_validate("DE136695976")); // real DE, checksum ok
        assert!(vat_validate("IT00743110157")); // real IT, checksum ok
    }

    #[test]
    fn valid_structural_only() {
        assert!(vat_validate("NL004495445B01"));
        assert!(vat_validate("ATU13585627"));
    }

    #[test]
    fn invalid_checksum() {
        assert!(!vat_validate("DE136695970")); // bad DE checksum
    }

    #[test]
    fn invalid_prefix_and_length() {
        assert!(!vat_validate("ZZ123")); // unknown prefix
        assert!(!vat_validate("DE12345")); // bad length
        assert!(!vat_validate("")); // empty
    }

    #[test]
    fn format_valid_and_invalid() {
        assert_eq!(vat_format("de 136 695 976").as_deref(), Some("DE136695976"));
        assert_eq!(vat_format("DE136695970"), None);
        assert_eq!(vat_format("ZZ123"), None);
    }
}
