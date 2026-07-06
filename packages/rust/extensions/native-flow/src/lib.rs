//! `goldenflow._native` / `goldenflow_native._native` — native acceleration
//! kernels (PyO3 extension module) for GoldenFlow.
//!
//! Scope: the international phone family. GoldenFlow's pure-Python transforms
//! resolve the common case with vectorized Polars expressions; these kernels
//! accelerate the *residual* (numbers the Polars fast path can't normalize —
//! international formats, non-NANP regions) that would otherwise hit the
//! `phonenumbers` library one row at a time. Each kernel returns null for rows
//! it can't resolve, so the Python reference settles those and the native path
//! is never worse. Mirrors packages/rust/extensions/native (goldenmatch).

use pyo3::prelude::*;

mod address;
mod autocorrect;
mod categorical;
mod email;
mod identifiers;
mod names;
mod numeric;
mod phone;
mod phonetic;
mod text;
mod url;
mod util;

#[pymodule]
fn _native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    m.add_function(wrap_pyfunction!(email::email_lowercase_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(email::email_normalize_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(email::email_extract_domain_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(email::email_validate_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(phone::phone_e164_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(phone::phone_national_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(phone::phone_country_code_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(phone::phone_valid_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(phonetic::soundex_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(identifiers::cc_validate_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(identifiers::cc_format_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(identifiers::cc_mask_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(identifiers::iban_validate_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(identifiers::iban_format_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(identifiers::isbn_validate_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(identifiers::isbn_normalize_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(identifiers::ean_validate_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(identifiers::swift_validate_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(identifiers::swift_format_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(identifiers::vat_validate_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(identifiers::vat_format_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(identifiers::aba_validate_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(identifiers::imei_validate_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(names::name_transliterate_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(names::name_script_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(names::strip_titles_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(names::strip_suffixes_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(names::name_proper_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(names::nickname_standardize_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(names::has_initial_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(names::split_name_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(names::split_name_reverse_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(names::merge_name_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(address::address_standardize_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(address::address_expand_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(address::state_abbreviate_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(address::state_expand_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(address::zip_normalize_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(address::country_standardize_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(address::unit_normalize_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(address::split_address_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(autocorrect::build_canonical_map_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(text::strip_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(text::collapse_whitespace_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(text::normalize_quotes_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(text::normalize_line_endings_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(text::remove_html_tags_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(text::remove_urls_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(text::remove_digits_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(text::remove_punctuation_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(text::remove_emojis_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(text::extract_numbers_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(text::truncate_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(text::pad_left_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(text::pad_right_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(text::lowercase_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(text::uppercase_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(text::title_case_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(text::normalize_unicode_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(text::fix_mojibake_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(url::url_normalize_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(url::url_extract_domain_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(numeric::currency_strip_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(numeric::percentage_normalize_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(numeric::to_integer_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(numeric::comma_decimal_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(numeric::scientific_to_decimal_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(numeric::round_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(numeric::clamp_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(numeric::abs_value_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(numeric::fill_zero_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(categorical::boolean_normalize_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(categorical::gender_standardize_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(categorical::null_standardize_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(
        categorical::category_normalize_key_arrow,
        m
    )?)?;
    Ok(())
}
