//! wasm-bindgen wrapper over `goldenflow-core::identifiers`. The TS analogue
//! of a future native-flow pyo3 crate: thin shims delegating to
//! `goldenflow-core` so identifier validation/formatting is byte-identical
//! across Python, native, and TS WASM. All logic lives in `goldenflow-core`
//! (the reference implementation); this crate only marshals strings across
//! the JS<->WASM boundary.
//!
//! `wasm-bindgen` is a wasm32-only dependency (see Cargo.toml), so the actual
//! `#[wasm_bindgen]` exports live in a `cfg(target_arch = "wasm32")`-gated
//! module — this keeps a plain host `cargo build`/`cargo test` (no wasm
//! target) compiling clean, matching `score-wasm`'s shape.

#[cfg(target_arch = "wasm32")]
mod wasm {
    use goldenflow_core::categorical;
    use goldenflow_core::email;
    use goldenflow_core::identifiers::{aba, ean, iban, imei, isbn, luhn, swift, vat};
    use goldenflow_core::names;
    use goldenflow_core::numeric;
    use goldenflow_core::url;
    use wasm_bindgen::prelude::*;

    #[wasm_bindgen]
    pub fn email_lowercase(s: &str) -> String {
        email::email_lowercase(s)
    }

    #[wasm_bindgen]
    pub fn email_normalize(s: &str) -> String {
        email::email_normalize(s)
    }

    #[wasm_bindgen]
    pub fn email_extract_domain(s: &str) -> Option<String> {
        email::email_extract_domain(s)
    }

    #[wasm_bindgen]
    pub fn email_validate(s: &str) -> Option<bool> {
        email::email_validate(s)
    }

    #[wasm_bindgen]
    pub fn cc_validate(s: &str) -> bool {
        luhn::cc_validate(s)
    }

    #[wasm_bindgen]
    pub fn cc_format(s: &str) -> Option<String> {
        luhn::cc_format(s)
    }

    #[wasm_bindgen]
    pub fn cc_mask(s: &str) -> Option<String> {
        luhn::cc_mask(s)
    }

    #[wasm_bindgen]
    pub fn iban_validate(s: &str) -> bool {
        iban::iban_validate(s)
    }

    #[wasm_bindgen]
    pub fn iban_format(s: &str) -> Option<String> {
        iban::iban_format(s)
    }

    #[wasm_bindgen]
    pub fn isbn_validate(s: &str) -> bool {
        isbn::isbn_validate(s)
    }

    #[wasm_bindgen]
    pub fn isbn_normalize(s: &str) -> Option<String> {
        isbn::isbn_normalize(s)
    }

    #[wasm_bindgen]
    pub fn ean_validate(s: &str) -> bool {
        ean::ean_validate(s)
    }

    #[wasm_bindgen]
    pub fn swift_validate(s: &str) -> bool {
        swift::swift_validate(s)
    }

    #[wasm_bindgen]
    pub fn swift_format(s: &str) -> Option<String> {
        swift::swift_format(s)
    }

    #[wasm_bindgen]
    pub fn aba_validate(s: &str) -> bool {
        aba::aba_validate(s)
    }

    #[wasm_bindgen]
    pub fn imei_validate(s: &str) -> bool {
        imei::imei_validate(s)
    }

    #[wasm_bindgen]
    pub fn vat_validate(s: &str) -> bool {
        vat::vat_validate(s)
    }

    #[wasm_bindgen]
    pub fn vat_format(s: &str) -> Option<String> {
        vat::vat_format(s)
    }

    #[wasm_bindgen]
    pub fn name_transliterate(s: &str) -> String {
        names::name_transliterate(s)
    }

    #[wasm_bindgen]
    pub fn name_script(s: &str) -> String {
        names::name_script(s)
    }

    #[wasm_bindgen]
    pub fn strip_titles(s: &str) -> String {
        names::strip_titles(s)
    }

    #[wasm_bindgen]
    pub fn strip_suffixes(s: &str) -> String {
        names::strip_suffixes(s)
    }

    #[wasm_bindgen]
    pub fn name_proper(s: &str) -> String {
        names::name_proper(s)
    }

    #[wasm_bindgen]
    pub fn nickname_standardize(s: &str) -> String {
        names::nickname_standardize(s)
    }

    #[wasm_bindgen]
    pub fn has_initial(s: &str) -> bool {
        names::has_initial(s)
    }

    /// `"First Last"` -> `[first, last]` (a 2-element JS string array).
    #[wasm_bindgen]
    pub fn split_name(s: &str) -> Vec<String> {
        let (first, last) = names::split_name(s);
        vec![first, last]
    }

    /// `"Last, First"` -> `[first, last]` (a 2-element JS string array).
    #[wasm_bindgen]
    pub fn split_name_reverse(s: &str) -> Vec<String> {
        let (first, last) = names::split_name_reverse(s);
        vec![first, last]
    }

    /// `(first, last)` -> `full_name`; `None` when both parts are absent/blank.
    #[wasm_bindgen]
    pub fn merge_name(first: Option<String>, last: Option<String>) -> Option<String> {
        names::merge_name(first.as_deref(), last.as_deref())
    }

    #[wasm_bindgen]
    pub fn url_normalize(s: &str) -> Option<String> {
        url::url_normalize(s)
    }

    #[wasm_bindgen]
    pub fn url_extract_domain(s: &str) -> Option<String> {
        url::url_extract_domain(s)
    }

    #[wasm_bindgen]
    pub fn currency_strip(s: &str) -> Option<f64> {
        numeric::currency_strip(s)
    }

    #[wasm_bindgen]
    pub fn percentage_normalize(s: &str) -> Option<f64> {
        numeric::percentage_normalize(s)
    }

    #[wasm_bindgen]
    pub fn to_integer(s: &str) -> Option<i64> {
        numeric::to_integer(s)
    }

    #[wasm_bindgen]
    pub fn comma_decimal(s: &str) -> Option<f64> {
        numeric::comma_decimal(s)
    }

    #[wasm_bindgen]
    pub fn scientific_to_decimal(s: &str) -> Option<f64> {
        numeric::scientific_to_decimal(s)
    }

    #[wasm_bindgen]
    pub fn round_value(x: f64, n: i32) -> f64 {
        numeric::round_f64(x, n)
    }

    #[wasm_bindgen]
    pub fn clamp_value(x: f64, min_val: f64, max_val: f64) -> f64 {
        numeric::clamp_f64(x, min_val, max_val)
    }

    #[wasm_bindgen]
    pub fn abs_value(x: f64) -> f64 {
        numeric::abs_f64(x)
    }

    #[wasm_bindgen]
    pub fn fill_zero(x: Option<f64>) -> f64 {
        numeric::fill_zero(x)
    }

    #[wasm_bindgen]
    pub fn boolean_normalize(s: &str) -> Option<bool> {
        categorical::boolean_normalize(s)
    }

    #[wasm_bindgen]
    pub fn gender_standardize(s: &str) -> String {
        categorical::gender_standardize(s)
    }

    #[wasm_bindgen]
    pub fn null_standardize(s: &str) -> Option<String> {
        categorical::null_standardize(s)
    }

    #[wasm_bindgen]
    pub fn category_normalize_key(s: &str) -> String {
        categorical::category_normalize_key(s)
    }
}
