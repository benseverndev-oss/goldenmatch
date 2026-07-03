//! Owned checksummed-identifier kernels (pyo3-free). validate -> bool,
//! canonicalize -> Option<String>. These are the reference implementations;
//! the Python/TS fallbacks must reproduce their bytes exactly (byte-parity harness).
pub mod iban;
pub mod isbn;
pub mod luhn;
// (ean, vat added in later tasks)

/// Remove ASCII spaces, '-' and '.' — the separators identifiers tolerate.
pub(crate) fn strip_sep(s: &str) -> String {
    s.chars()
        .filter(|c| !matches!(c, ' ' | '-' | '.'))
        .collect()
}
