//! International phone kernel — a Rust port of libphonenumber (`phonenumber`
//! crate). Pure functions over `&str`; the Arrow marshaling lives in the
//! native-flow shim (`util.rs`). Each fn returns `None` for a row it cannot
//! resolve, so the caller's Python fallback settles that row (never worse).
//!
//! `nanp_only`: emit a result ONLY for country-calling-code-1 numbers and
//! `None` otherwise — the parity-safe mode the gated default uses. The Rust
//! port is byte-identical to Python `phonenumbers` on NANP, but with a
//! mismatched default region ("US") it mis-strips a leading national "1" on
//! some `+CC` international numbers; restricting native to code-1 sidesteps that.
use phonenumber::{country, Mode, PhoneNumber};

pub fn region_of(region: &str) -> Option<country::Id> {
    region.parse::<country::Id>().ok()
}

fn parse(region: Option<country::Id>, s: &str) -> Option<PhoneNumber> {
    phonenumber::parse(region, s).ok()
}

fn parse_gated(region: Option<country::Id>, s: &str, nanp_only: bool) -> Option<PhoneNumber> {
    let n = parse(region, s)?;
    if nanp_only && n.country().code() != 1 {
        return None;
    }
    Some(n)
}

pub fn e164(region: Option<country::Id>, s: &str, nanp_only: bool) -> Option<String> {
    parse_gated(region, s, nanp_only).map(|n| n.format().mode(Mode::E164).to_string())
}

pub fn national(region: Option<country::Id>, s: &str, nanp_only: bool) -> Option<String> {
    parse_gated(region, s, nanp_only).map(|n| n.format().mode(Mode::National).to_string())
}

pub fn country_code(region: Option<country::Id>, s: &str, nanp_only: bool) -> Option<i64> {
    parse_gated(region, s, nanp_only).map(|n| i64::from(n.country().code()))
}

/// Keep only ASCII digits — the `phone_digits` column transform. Byte-identical
/// to the pure-Polars `str.replace_all(r"\D", "")` on ASCII phone data (the
/// pinned parity contract; a Unicode digit would be kept by Python's `\d` but is
/// dropped here).
pub fn phone_digits(s: &str) -> String {
    s.chars().filter(|c| c.is_ascii_digit()).collect()
}

pub fn valid(region: Option<country::Id>, s: &str, nanp_only: bool) -> Option<bool> {
    // Same semantics as the current `phone_valid_arrow`: parsed-and-invalid ->
    // Some(false), parse failure -> None (Python decides). NOTE this is the
    // `is_valid` spec — which is exactly why `phone_validate` is held on
    // `_FALLBACK_ONLY` in the loader (the product spec is `is_possible`).
    parse_gated(region, s, nanp_only).map(|n| phonenumber::is_valid(&n))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn e164_nanp_alpha() {
        // 1-800-FLOWERS is canonical NANP; nanp_only keeps it.
        let reg = region_of("US");
        assert_eq!(
            e164(reg, "1-800-356-9377", true).as_deref(),
            Some("+18003569377")
        );
    }

    #[test]
    fn e164_intl_dropped_under_nanp_only() {
        let reg = region_of("US");
        assert_eq!(e164(reg, "+33142685300", true), None);
    }

    #[test]
    fn country_code_and_valid() {
        let reg = region_of("US");
        assert_eq!(country_code(reg, "1-800-356-9377", true), Some(1));
        assert_eq!(valid(reg, "1-800-356-9377", true), Some(true));
    }
}
