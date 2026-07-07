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
    use goldenflow_core::address;
    use goldenflow_core::autocorrect;
    use goldenflow_core::categorical;
    use goldenflow_core::company;
    use goldenflow_core::email;
    use goldenflow_core::identifiers::{
        aba, cusip, ean, ein, iban, imei, isbn, isin, luhn, npi, ssn, swift, vat,
    };
    use goldenflow_core::names;
    use goldenflow_core::numeric;
    use goldenflow_core::phone;
    use goldenflow_core::phonetic;
    use goldenflow_core::text;
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
    pub fn isin_validate(s: &str) -> bool {
        isin::isin_validate(s)
    }

    #[wasm_bindgen]
    pub fn cusip_validate(s: &str) -> bool {
        cusip::cusip_validate(s)
    }

    #[wasm_bindgen]
    pub fn npi_validate(s: &str) -> bool {
        npi::npi_validate(s)
    }

    #[wasm_bindgen]
    pub fn luhn_validate(s: &str) -> bool {
        luhn::luhn_validate(s)
    }

    #[wasm_bindgen]
    pub fn cc_brand(s: &str) -> Option<String> {
        luhn::cc_brand(s)
    }

    #[wasm_bindgen]
    pub fn name_initials(s: &str) -> String {
        names::name_initials(s)
    }

    #[wasm_bindgen]
    pub fn strip_middle(s: &str) -> String {
        names::strip_middle(s)
    }

    // Cast i64 -> f64 so JS receives a plain `number` (not a BigInt); the value
    // is exact for the 1..=3999 / small-ordinal ranges these parsers produce,
    // and the byte-parity harness compares numerics by VALUE.
    #[wasm_bindgen]
    pub fn roman_to_int(s: &str) -> Option<f64> {
        numeric::roman_to_int(s).map(|v| v as f64)
    }

    #[wasm_bindgen]
    pub fn ordinal_to_int(s: &str) -> Option<f64> {
        numeric::ordinal_to_int(s).map(|v| v as f64)
    }

    #[wasm_bindgen]
    pub fn fraction_to_decimal(s: &str) -> Option<f64> {
        numeric::fraction_to_decimal(s)
    }

    #[wasm_bindgen]
    pub fn ssn_format(s: &str) -> String {
        ssn::ssn_format(s)
    }

    #[wasm_bindgen]
    pub fn ssn_mask(s: &str) -> String {
        ssn::ssn_mask(s)
    }

    #[wasm_bindgen]
    pub fn ein_format(s: &str) -> String {
        ein::ein_format(s)
    }

    #[wasm_bindgen]
    pub fn phone_digits(s: &str) -> String {
        phone::phone_digits(s)
    }

    #[wasm_bindgen]
    pub fn company_normalize(s: &str) -> Option<String> {
        company::company_normalize(s)
    }

    #[wasm_bindgen]
    pub fn company_strip_legal(s: &str) -> Option<String> {
        company::company_strip_legal(s)
    }

    #[wasm_bindgen]
    pub fn company_extract_legal(s: &str) -> Option<String> {
        company::company_extract_legal(s)
    }

    #[wasm_bindgen]
    pub fn email_canonical(s: &str) -> String {
        email::email_canonical(s)
    }

    #[wasm_bindgen]
    pub fn email_mask(s: &str) -> Option<String> {
        email::email_mask(s)
    }

    #[wasm_bindgen]
    pub fn soundex(s: &str) -> String {
        phonetic::soundex(s)
    }

    #[wasm_bindgen]
    pub fn double_metaphone_primary(s: &str) -> String {
        phonetic::double_metaphone_primary(s)
    }

    #[wasm_bindgen]
    pub fn double_metaphone_alt(s: &str) -> String {
        phonetic::double_metaphone_alt(s)
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
    pub fn address_standardize(s: &str) -> String {
        address::address_standardize(s)
    }

    #[wasm_bindgen]
    pub fn address_expand(s: &str) -> String {
        address::address_expand(s)
    }

    #[wasm_bindgen]
    pub fn state_abbreviate(s: &str) -> String {
        address::state_abbreviate(s)
    }

    #[wasm_bindgen]
    pub fn state_expand(s: &str) -> String {
        address::state_expand(s)
    }

    #[wasm_bindgen]
    pub fn zip_normalize(s: &str) -> String {
        address::zip_normalize(s)
    }

    #[wasm_bindgen]
    pub fn country_standardize(s: &str) -> String {
        address::country_standardize(s)
    }

    #[wasm_bindgen]
    pub fn unit_normalize(s: &str) -> String {
        address::unit_normalize(s)
    }

    /// `"street, city, ST zip"` -> a 4-element JS array `[street, city, state,
    /// zip]`. `street` is always a string; `city`/`state`/`zip` are `null` on a
    /// no-match row (only the street parsed).
    #[wasm_bindgen]
    pub fn split_address(s: &str) -> Vec<JsValue> {
        let (street, city, state, zip) = address::split_address(s);
        let opt = |v: Option<String>| v.map_or(JsValue::NULL, |x| JsValue::from_str(&x));
        vec![JsValue::from_str(&street), opt(city), opt(state), opt(zip)]
    }

    #[wasm_bindgen]
    pub fn strip(s: &str) -> String {
        text::strip(s).to_string()
    }

    #[wasm_bindgen]
    pub fn collapse_whitespace(s: &str) -> String {
        text::collapse_whitespace(s)
    }

    #[wasm_bindgen]
    pub fn normalize_quotes(s: &str) -> String {
        text::normalize_quotes(s)
    }

    #[wasm_bindgen]
    pub fn normalize_line_endings(s: &str) -> String {
        text::normalize_line_endings(s)
    }

    #[wasm_bindgen]
    pub fn remove_html_tags(s: &str) -> String {
        text::remove_html_tags(s)
    }

    #[wasm_bindgen]
    pub fn remove_urls(s: &str) -> String {
        text::remove_urls(s)
    }

    #[wasm_bindgen]
    pub fn remove_digits(s: &str) -> String {
        text::remove_digits(s)
    }

    #[wasm_bindgen]
    pub fn remove_punctuation(s: &str) -> String {
        text::remove_punctuation(s)
    }

    #[wasm_bindgen]
    pub fn remove_emojis(s: &str) -> String {
        text::remove_emojis(s)
    }

    #[wasm_bindgen]
    pub fn extract_numbers(s: &str) -> String {
        text::extract_numbers(s)
    }

    #[wasm_bindgen]
    pub fn truncate(s: &str, n: u32) -> String {
        text::truncate(s, n as usize)
    }

    #[wasm_bindgen]
    pub fn pad_left(s: &str, width: u32, pad: char) -> String {
        text::pad_left(s, width as usize, pad)
    }

    #[wasm_bindgen]
    pub fn pad_right(s: &str, width: u32, pad: char) -> String {
        text::pad_right(s, width as usize, pad)
    }

    #[wasm_bindgen]
    pub fn lowercase(s: &str) -> String {
        text::lowercase(s)
    }

    #[wasm_bindgen]
    pub fn uppercase(s: &str) -> String {
        text::uppercase(s)
    }

    #[wasm_bindgen]
    pub fn title_case(s: &str) -> String {
        text::title_case(s)
    }

    #[wasm_bindgen]
    pub fn normalize_unicode(s: &str) -> String {
        text::normalize_unicode(s)
    }

    #[wasm_bindgen]
    pub fn fix_mojibake(s: &str) -> String {
        text::fix_mojibake(s)
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
    pub fn url_strip_tracking(s: &str) -> Option<String> {
        url::url_strip_tracking(s)
    }

    #[wasm_bindgen]
    pub fn url_strip_www(s: &str) -> Option<String> {
        url::url_strip_www(s)
    }

    #[wasm_bindgen]
    pub fn url_canonical(s: &str) -> Option<String> {
        url::url_canonical(s)
    }

    /// rapidfuzz `fuzz.ratio` (Indel/LCS similarity, 0-100).
    #[wasm_bindgen]
    pub fn fuzz_ratio(a: &str, b: &str) -> f64 {
        autocorrect::fuzz_ratio(a, b)
    }

    /// Category-autocorrect correction map from parallel `values` (non-null) +
    /// `counts` arrays. Returns a FLAT `[from0, to0, from1, to1, ...]` array of
    /// correction pairs (the caller unflattens). The caller must pass the pairs
    /// in value_counts order (count DESC) so the kernel's tie-breaking matches.
    #[wasm_bindgen]
    pub fn build_canonical_map(
        values: Vec<String>,
        counts: Vec<i32>,
        freq_threshold: f64,
        match_threshold: f64,
    ) -> Vec<String> {
        let refs: Vec<Option<&str>> = values.iter().map(|s| Some(s.as_str())).collect();
        let counts64: Vec<i64> = counts.iter().map(|&c| c as i64).collect();
        let pairs =
            autocorrect::build_canonical_map(&refs, &counts64, freq_threshold, match_threshold);
        let mut out = Vec::with_capacity(pairs.len() * 2);
        for (from, to) in pairs {
            out.push(from);
            out.push(to);
        }
        out
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

    // -- Fused columnar apply (Pillar-1 on the edge) -----------------------------

    /// Output of [`apply_chain`]: the transformed values plus the per-kernel affected
    /// count (rows the i-th kernel altered), so the TS engine can emit a per-op audit
    /// record without N boundary crossings — mirrors the native chain's `changed`.
    #[wasm_bindgen]
    pub struct ChainOut {
        values: Vec<String>,
        changed: Vec<u32>,
    }

    #[wasm_bindgen]
    impl ChainOut {
        #[wasm_bindgen(getter)]
        pub fn values(&self) -> Vec<String> {
            self.values.clone()
        }
        #[wasm_bindgen(getter)]
        pub fn changed(&self) -> Vec<u32> {
            self.changed.clone()
        }
    }

    /// Run a whole chain of owned no-arg string kernels over a column of NON-NULL
    /// values in ONE JS<->WASM crossing, instead of one crossing per value per
    /// transform (the TS WASM backend otherwise dispatches per value). Byte-identical
    /// to applying the kernels one at a time — same `goldenflow_core::chain` dispatch.
    /// Total kernels never null, so the TS caller passes only the non-null values and
    /// scatters the results back into their positions, leaving null cells untouched.
    #[wasm_bindgen]
    pub fn apply_chain(values: Vec<String>, names: Vec<String>) -> Result<ChainOut, JsError> {
        use goldenflow_core::chain::Kernel;
        let mut kernels = Vec::with_capacity(names.len());
        for n in &names {
            kernels.push(
                Kernel::from_name(n)
                    .ok_or_else(|| JsError::new(&format!("not a fusable chain kernel: {n}")))?,
            );
        }
        let refs: Vec<&str> = values.iter().map(String::as_str).collect();
        let (out, changed) = goldenflow_core::chain::apply_chain_str(&refs, &kernels);
        Ok(ChainOut {
            values: out,
            changed: changed.into_iter().map(|c| c as u32).collect(),
        })
    }

    /// The fusable no-arg kernel names the WASM chain supports — mirror of the TS
    /// `FUSABLE_KERNELS` set (coverage-guarded in the TS parity test) and of the
    /// native `fusable_kernel_names` no-arg subset.
    #[wasm_bindgen]
    pub fn fusable_kernel_names() -> Vec<String> {
        goldenflow_core::chain::Kernel::ALL_NAMES
            .iter()
            .map(|s| s.to_string())
            .collect()
    }
}
