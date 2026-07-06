//! Owned checksummed-identifier kernels (pyo3-free). validate -> bool,
//! canonicalize -> Option<String>. These are the reference implementations;
//! the Python/TS fallbacks must reproduce their bytes exactly (byte-parity harness).
pub mod aba;
pub mod cusip;
pub mod ean;
pub mod ein;
pub mod iban;
pub mod imei;
pub mod isbn;
pub mod isin;
pub mod luhn;
pub mod npi;
pub mod ssn;
pub mod swift;
pub mod vat;

/// Remove ASCII spaces, '-' and '.' — the separators identifiers tolerate.
pub(crate) fn strip_sep(s: &str) -> String {
    s.chars()
        .filter(|c| !matches!(c, ' ' | '-' | '.'))
        .collect()
}

/// Keep only ASCII digits — the byte-exact equivalent of Python's
/// `re.sub(r"\D", "", s)` on ASCII input (the parity contract for the
/// digit-format identifiers). A Unicode digit would be kept by Python's `\d`
/// but dropped here; ASCII input is the pinned contract, as with `phone_digits`.
pub(crate) fn ascii_digits(s: &str) -> String {
    s.chars().filter(|c| c.is_ascii_digit()).collect()
}
