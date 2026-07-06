//! GoldenFlow DuckDB extension -- a zero-Python SQL surface over the owned
//! reference kernels.
//!
//! Each SQL scalar function is a thin [`VScalar`] that runs the corresponding
//! `goldenflow-core` kernel (the byte-parity oracle) element-wise over a DuckDB
//! data chunk. Because the kernel *is* the cross-surface reference, SQL output
//! is byte-identical to the Python / TS / wasm surfaces by construction -- and
//! there is no CPython interpreter anywhere in the process.
//!
//! Coverage: the full single-argument catalogue (Slice 2/2b) across VARCHAR /
//! nullable VARCHAR / BOOLEAN / DOUBLE / BIGINT, the identifier family, plus the
//! multi-argument (phone-with-region, `truncate`, `pad_*`, `merge_name`) and
//! multi-output (`split_name`, `split_name_reverse`, `split_address`) kernels.
//! Still out: `category_auto_correct` -- a column-wide/aggregate transform, not
//! a stateless scalar, so it needs a DuckDB aggregate/table-function surface.

use duckdb::{
    core::{DataChunkHandle, Inserter, LogicalTypeId},
    ffi::duckdb_string_t,
    types::DuckString,
    vscalar::{ScalarFunctionSignature, VScalar},
    vtab::arrow::WritableVector,
    Connection, Result,
};
use std::error::Error;

/// `goldenflow-core`'s `strip` returns a borrowed `&str`; give it an owned-String
/// shape so it slots into the `VARCHAR -> VARCHAR` registration table.
fn strip_owned(s: &str) -> String {
    goldenflow_core::text::strip(s).to_string()
}

/// Register a batch of `VARCHAR -> VARCHAR` UDFs whose kernel is `fn(&str) -> String`.
/// Each entry generates a block-local zero-sized [`VScalar`] and registers it, so
/// adding a transform is a single `"name" => kernel` line.
macro_rules! register_str {
    ($con:expr, $($name:literal => $kernel:path),+ $(,)?) => {{
        $({
            struct S;
            impl VScalar for S {
                type State = ();
                fn invoke(
                    _state: &Self::State,
                    input: &mut DataChunkHandle,
                    output: &mut dyn WritableVector,
                ) -> std::result::Result<(), Box<dyn Error>> {
                    let in_vec = input.flat_vector(0);
                    let rows =
                        unsafe { in_vec.as_slice_with_len::<duckdb_string_t>(input.len()) };
                    let mut out = output.flat_vector();
                    for (i, row) in rows.iter().enumerate() {
                        if in_vec.row_is_null(i as u64) {
                            out.set_null(i);
                            continue;
                        }
                        let mut row = *row;
                        let s = DuckString::new(&mut row).as_str();
                        out.insert(i, $kernel(s.as_ref()).as_str());
                    }
                    Ok(())
                }
                fn signatures() -> Vec<ScalarFunctionSignature> {
                    vec![ScalarFunctionSignature::exact(
                        vec![LogicalTypeId::Varchar.into()],
                        LogicalTypeId::Varchar.into(),
                    )]
                }
            }
            $con.register_scalar_function::<S>($name)?;
        })+
    }};
}

/// Register a batch of nullable `VARCHAR -> VARCHAR` UDFs whose kernel is
/// `fn(&str) -> Option<String>`: `None` becomes SQL `NULL`.
macro_rules! register_opt_str {
    ($con:expr, $($name:literal => $kernel:path),+ $(,)?) => {{
        $({
            struct S;
            impl VScalar for S {
                type State = ();
                fn invoke(
                    _state: &Self::State,
                    input: &mut DataChunkHandle,
                    output: &mut dyn WritableVector,
                ) -> std::result::Result<(), Box<dyn Error>> {
                    let in_vec = input.flat_vector(0);
                    let rows =
                        unsafe { in_vec.as_slice_with_len::<duckdb_string_t>(input.len()) };
                    let mut out = output.flat_vector();
                    for (i, row) in rows.iter().enumerate() {
                        if in_vec.row_is_null(i as u64) {
                            out.set_null(i);
                            continue;
                        }
                        let mut row = *row;
                        let s = DuckString::new(&mut row).as_str();
                        match $kernel(s.as_ref()) {
                            Some(v) => out.insert(i, v.as_str()),
                            None => out.set_null(i),
                        }
                    }
                    Ok(())
                }
                fn signatures() -> Vec<ScalarFunctionSignature> {
                    vec![ScalarFunctionSignature::exact(
                        vec![LogicalTypeId::Varchar.into()],
                        LogicalTypeId::Varchar.into(),
                    )]
                }
            }
            $con.register_scalar_function::<S>($name)?;
        })+
    }};
}

/// Register a batch of `VARCHAR -> <primitive>` UDFs whose kernel is
/// `fn(&str) -> Option<$rust_ty>` (`None` -> SQL `NULL`). `$logical` is the
/// output [`LogicalTypeId`] variant; `$rust_ty` is the matching Rust cell type
/// written through `as_mut_slice`.
macro_rules! register_prim_opt {
    ($con:expr, $rust_ty:ty, $logical:ident, $($name:literal => $kernel:path),+ $(,)?) => {{
        $({
            struct S;
            impl VScalar for S {
                type State = ();
                fn invoke(
                    _state: &Self::State,
                    input: &mut DataChunkHandle,
                    output: &mut dyn WritableVector,
                ) -> std::result::Result<(), Box<dyn Error>> {
                    let in_vec = input.flat_vector(0);
                    let rows =
                        unsafe { in_vec.as_slice_with_len::<duckdb_string_t>(input.len()) };
                    let mut out = output.flat_vector();
                    for (i, row) in rows.iter().enumerate() {
                        if in_vec.row_is_null(i as u64) {
                            out.set_null(i);
                            continue;
                        }
                        let mut row = *row;
                        let s = DuckString::new(&mut row).as_str();
                        match $kernel(s.as_ref()) {
                            Some(v) => unsafe { out.as_mut_slice::<$rust_ty>()[i] = v },
                            None => out.set_null(i),
                        }
                    }
                    Ok(())
                }
                fn signatures() -> Vec<ScalarFunctionSignature> {
                    vec![ScalarFunctionSignature::exact(
                        vec![LogicalTypeId::Varchar.into()],
                        LogicalTypeId::$logical.into(),
                    )]
                }
            }
            $con.register_scalar_function::<S>($name)?;
        })+
    }};
}

/// Like [`register_prim_opt`] but for total kernels `fn(&str) -> $rust_ty`
/// (never null on a non-null input).
macro_rules! register_prim_total {
    ($con:expr, $rust_ty:ty, $logical:ident, $($name:literal => $kernel:path),+ $(,)?) => {{
        $({
            struct S;
            impl VScalar for S {
                type State = ();
                fn invoke(
                    _state: &Self::State,
                    input: &mut DataChunkHandle,
                    output: &mut dyn WritableVector,
                ) -> std::result::Result<(), Box<dyn Error>> {
                    let in_vec = input.flat_vector(0);
                    let rows =
                        unsafe { in_vec.as_slice_with_len::<duckdb_string_t>(input.len()) };
                    let mut out = output.flat_vector();
                    for (i, row) in rows.iter().enumerate() {
                        if in_vec.row_is_null(i as u64) {
                            out.set_null(i);
                            continue;
                        }
                        let mut row = *row;
                        let s = DuckString::new(&mut row).as_str();
                        let v = $kernel(s.as_ref());
                        unsafe { out.as_mut_slice::<$rust_ty>()[i] = v };
                    }
                    Ok(())
                }
                fn signatures() -> Vec<ScalarFunctionSignature> {
                    vec![ScalarFunctionSignature::exact(
                        vec![LogicalTypeId::Varchar.into()],
                        LogicalTypeId::$logical.into(),
                    )]
                }
            }
            $con.register_scalar_function::<S>($name)?;
        })+
    }};
}

// ---------------------------------------------------------------------------
// Multi-OUTPUT kernels: expose each tuple component as its own single-arg UDF,
// so they reuse the proven VARCHAR register_str! / register_opt_str! path.
// ---------------------------------------------------------------------------
mod split {
    use goldenflow_core as gf;
    pub fn name_first(s: &str) -> String { gf::names::split_name(s).0 }
    pub fn name_last(s: &str) -> String { gf::names::split_name(s).1 }
    pub fn rev_first(s: &str) -> String { gf::names::split_name_reverse(s).0 }
    pub fn rev_last(s: &str) -> String { gf::names::split_name_reverse(s).1 }
    pub fn addr_street(s: &str) -> String { gf::address::split_address(s).0 }
    pub fn addr_city(s: &str) -> Option<String> { gf::address::split_address(s).1 }
    pub fn addr_state(s: &str) -> Option<String> { gf::address::split_address(s).2 }
    pub fn addr_zip(s: &str) -> Option<String> { gf::address::split_address(s).3 }
}

// ---------------------------------------------------------------------------
// Multi-ARG kernels. Read each argument column into an owned Vec first (one
// flat_vector borrow at a time), then compute -- avoids holding two chunk
// vectors borrowed at once.
// ---------------------------------------------------------------------------
fn collect_str(input: &mut DataChunkHandle, col: usize) -> Vec<Option<String>> {
    let n = input.len();
    let v = input.flat_vector(col);
    let rows = unsafe { v.as_slice_with_len::<duckdb_string_t>(n) };
    (0..n)
        .map(|i| {
            if v.row_is_null(i as u64) {
                None
            } else {
                let mut r = rows[i];
                Some(DuckString::new(&mut r).as_str().as_ref().to_string())
            }
        })
        .collect()
}

fn collect_i64(input: &mut DataChunkHandle, col: usize) -> Vec<Option<i64>> {
    let n = input.len();
    let v = input.flat_vector(col);
    let rows = unsafe { v.as_slice_with_len::<i64>(n) };
    (0..n)
        .map(|i| if v.row_is_null(i as u64) { None } else { Some(rows[i]) })
        .collect()
}

/// A `(VARCHAR phone, VARCHAR region) -> VARCHAR` phone UDF. `nanp_only=true`
/// is the parity-safe mode (the Rust port is byte-identical to Python
/// `phonenumbers` on country-code-1; it mis-strips some `+CC` numbers with a
/// mismatched default region, so non-NANP rows return NULL rather than a wrong
/// value -- exactly how native-flow gates it).
macro_rules! phone_str_udf {
    ($ty:ident, $kernel:path) => {
        struct $ty;
        impl VScalar for $ty {
            type State = ();
            fn invoke(
                _s: &Self::State,
                input: &mut DataChunkHandle,
                output: &mut dyn WritableVector,
            ) -> std::result::Result<(), Box<dyn Error>> {
                let phones = collect_str(input, 0);
                let regions = collect_str(input, 1);
                let mut out = output.flat_vector();
                for i in 0..input.len() {
                    match &phones[i] {
                        None => out.set_null(i),
                        Some(p) => {
                            let region = regions[i]
                                .as_deref()
                                .and_then(goldenflow_core::phone::region_of);
                            match $kernel(region, p, true) {
                                Some(v) => out.insert(i, v.as_str()),
                                None => out.set_null(i),
                            }
                        }
                    }
                }
                Ok(())
            }
            fn signatures() -> Vec<ScalarFunctionSignature> {
                vec![ScalarFunctionSignature::exact(
                    vec![LogicalTypeId::Varchar.into(), LogicalTypeId::Varchar.into()],
                    LogicalTypeId::Varchar.into(),
                )]
            }
        }
    };
}
phone_str_udf!(PhoneE164, goldenflow_core::phone::e164);
phone_str_udf!(PhoneNational, goldenflow_core::phone::national);

/// `(VARCHAR phone, VARCHAR region) -> BIGINT` (country calling code).
struct PhoneCountryCode;
impl VScalar for PhoneCountryCode {
    type State = ();
    fn invoke(
        _s: &Self::State,
        input: &mut DataChunkHandle,
        output: &mut dyn WritableVector,
    ) -> std::result::Result<(), Box<dyn Error>> {
        let phones = collect_str(input, 0);
        let regions = collect_str(input, 1);
        let mut out = output.flat_vector();
        for i in 0..input.len() {
            let cc = phones[i].as_deref().and_then(|p| {
                let region = regions[i].as_deref().and_then(goldenflow_core::phone::region_of);
                goldenflow_core::phone::country_code(region, p, true)
            });
            match cc {
                Some(v) => unsafe { out.as_mut_slice::<i64>()[i] = v },
                None => out.set_null(i),
            }
        }
        Ok(())
    }
    fn signatures() -> Vec<ScalarFunctionSignature> {
        vec![ScalarFunctionSignature::exact(
            vec![LogicalTypeId::Varchar.into(), LogicalTypeId::Varchar.into()],
            LogicalTypeId::Bigint.into(),
        )]
    }
}

/// `(VARCHAR phone, VARCHAR region) -> BOOLEAN` (is_valid; parity-safe NANP gate).
struct PhoneValid;
impl VScalar for PhoneValid {
    type State = ();
    fn invoke(
        _s: &Self::State,
        input: &mut DataChunkHandle,
        output: &mut dyn WritableVector,
    ) -> std::result::Result<(), Box<dyn Error>> {
        let phones = collect_str(input, 0);
        let regions = collect_str(input, 1);
        let mut out = output.flat_vector();
        for i in 0..input.len() {
            let v = phones[i].as_deref().and_then(|p| {
                let region = regions[i].as_deref().and_then(goldenflow_core::phone::region_of);
                goldenflow_core::phone::valid(region, p, true)
            });
            match v {
                Some(b) => unsafe { out.as_mut_slice::<bool>()[i] = b },
                None => out.set_null(i),
            }
        }
        Ok(())
    }
    fn signatures() -> Vec<ScalarFunctionSignature> {
        vec![ScalarFunctionSignature::exact(
            vec![LogicalTypeId::Varchar.into(), LogicalTypeId::Varchar.into()],
            LogicalTypeId::Boolean.into(),
        )]
    }
}

/// `(VARCHAR s, BIGINT n) -> VARCHAR` first-n-chars truncate. A NULL/negative n
/// yields NULL (there is no sensible truncation length).
struct Truncate;
impl VScalar for Truncate {
    type State = ();
    fn invoke(
        _s: &Self::State,
        input: &mut DataChunkHandle,
        output: &mut dyn WritableVector,
    ) -> std::result::Result<(), Box<dyn Error>> {
        let strs = collect_str(input, 0);
        let ns = collect_i64(input, 1);
        let mut out = output.flat_vector();
        for i in 0..input.len() {
            match (&strs[i], ns[i]) {
                (Some(s), Some(n)) if n >= 0 => {
                    out.insert(i, goldenflow_core::text::truncate(s, n as usize).as_str())
                }
                _ => out.set_null(i),
            }
        }
        Ok(())
    }
    fn signatures() -> Vec<ScalarFunctionSignature> {
        vec![ScalarFunctionSignature::exact(
            vec![LogicalTypeId::Varchar.into(), LogicalTypeId::Bigint.into()],
            LogicalTypeId::Varchar.into(),
        )]
    }
}

/// `(VARCHAR s, BIGINT width, VARCHAR pad) -> VARCHAR`. `pad` uses its first
/// char (space if empty), matching the polars `pad` semantics.
macro_rules! pad_udf {
    ($ty:ident, $kernel:path) => {
        struct $ty;
        impl VScalar for $ty {
            type State = ();
            fn invoke(
                _s: &Self::State,
                input: &mut DataChunkHandle,
                output: &mut dyn WritableVector,
            ) -> std::result::Result<(), Box<dyn Error>> {
                let strs = collect_str(input, 0);
                let widths = collect_i64(input, 1);
                let pads = collect_str(input, 2);
                let mut out = output.flat_vector();
                for i in 0..input.len() {
                    match (&strs[i], widths[i], &pads[i]) {
                        (Some(s), Some(w), Some(pad)) if w >= 0 => {
                            let ch = pad.chars().next().unwrap_or(' ');
                            out.insert(i, $kernel(s, w as usize, ch).as_str())
                        }
                        _ => out.set_null(i),
                    }
                }
                Ok(())
            }
            fn signatures() -> Vec<ScalarFunctionSignature> {
                vec![ScalarFunctionSignature::exact(
                    vec![
                        LogicalTypeId::Varchar.into(),
                        LogicalTypeId::Bigint.into(),
                        LogicalTypeId::Varchar.into(),
                    ],
                    LogicalTypeId::Varchar.into(),
                )]
            }
        }
    };
}
pad_udf!(PadLeft, goldenflow_core::text::pad_left);
pad_udf!(PadRight, goldenflow_core::text::pad_right);

/// `(VARCHAR first, VARCHAR last) -> VARCHAR`. Joins the present non-blank
/// parts. NOTE: DuckDB scalar UDFs propagate NULL (any NULL arg -> NULL, the
/// function is never invoked), so `merge_name(NULL, 'Smith')` is SQL NULL rather
/// than the kernel's coalesced `'Smith'`. With non-NULL inputs it is byte-
/// identical; the `None` branch below is only reachable if a future DuckDB
/// grants special null handling.
struct MergeName;
impl VScalar for MergeName {
    type State = ();
    fn invoke(
        _s: &Self::State,
        input: &mut DataChunkHandle,
        output: &mut dyn WritableVector,
    ) -> std::result::Result<(), Box<dyn Error>> {
        let firsts = collect_str(input, 0);
        let lasts = collect_str(input, 1);
        let mut out = output.flat_vector();
        for i in 0..input.len() {
            match goldenflow_core::names::merge_name(firsts[i].as_deref(), lasts[i].as_deref()) {
                Some(v) => out.insert(i, v.as_str()),
                None => out.set_null(i),
            }
        }
        Ok(())
    }
    fn signatures() -> Vec<ScalarFunctionSignature> {
        vec![ScalarFunctionSignature::exact(
            vec![LogicalTypeId::Varchar.into(), LogicalTypeId::Varchar.into()],
            LogicalTypeId::Varchar.into(),
        )]
    }
}

/// Register every GoldenFlow scalar UDF on a connection. Single source of truth
/// for both the loadable entrypoint and the in-process test harness, so the two
/// can never drift on names. UDF names are `goldenflow_<kernel>` -- predictable
/// 1:1 with the reference function.
fn register_all(con: &Connection) -> Result<(), Box<dyn Error>> {
    // VARCHAR -> VARCHAR (total).
    register_str!(con,
        // address
        "goldenflow_address_standardize"  => goldenflow_core::address::address_standardize,
        "goldenflow_address_expand"       => goldenflow_core::address::address_expand,
        "goldenflow_state_abbreviate"     => goldenflow_core::address::state_abbreviate,
        "goldenflow_state_expand"         => goldenflow_core::address::state_expand,
        "goldenflow_zip_normalize"        => goldenflow_core::address::zip_normalize,
        "goldenflow_country_standardize"  => goldenflow_core::address::country_standardize,
        "goldenflow_unit_normalize"       => goldenflow_core::address::unit_normalize,
        // categorical
        "goldenflow_category_normalize_key" => goldenflow_core::categorical::category_normalize_key,
        "goldenflow_gender_standardize"     => goldenflow_core::categorical::gender_standardize,
        // email
        "goldenflow_email_lowercase"      => goldenflow_core::email::email_lowercase,
        "goldenflow_email_normalize"      => goldenflow_core::email::email_normalize,
        // names
        "goldenflow_name_transliterate"   => goldenflow_core::names::name_transliterate,
        "goldenflow_name_script"          => goldenflow_core::names::name_script,
        "goldenflow_strip_titles"         => goldenflow_core::names::strip_titles,
        "goldenflow_strip_suffixes"       => goldenflow_core::names::strip_suffixes,
        "goldenflow_name_proper"          => goldenflow_core::names::name_proper,
        "goldenflow_nickname_standardize" => goldenflow_core::names::nickname_standardize,
        // text
        "goldenflow_strip"                => strip_owned,
        "goldenflow_collapse_whitespace"  => goldenflow_core::text::collapse_whitespace,
        "goldenflow_normalize_quotes"     => goldenflow_core::text::normalize_quotes,
        "goldenflow_normalize_line_endings" => goldenflow_core::text::normalize_line_endings,
        "goldenflow_remove_html_tags"     => goldenflow_core::text::remove_html_tags,
        "goldenflow_remove_urls"          => goldenflow_core::text::remove_urls,
        "goldenflow_remove_digits"        => goldenflow_core::text::remove_digits,
        "goldenflow_remove_punctuation"   => goldenflow_core::text::remove_punctuation,
        "goldenflow_extract_numbers"      => goldenflow_core::text::extract_numbers,
        "goldenflow_remove_emojis"        => goldenflow_core::text::remove_emojis,
        "goldenflow_lowercase"            => goldenflow_core::text::lowercase,
        "goldenflow_uppercase"            => goldenflow_core::text::uppercase,
        "goldenflow_title_case"           => goldenflow_core::text::title_case,
        "goldenflow_fix_mojibake"         => goldenflow_core::text::fix_mojibake,
        "goldenflow_normalize_unicode"    => goldenflow_core::text::normalize_unicode,
        // phonetic keys (blocking/match-key encoders)
        "goldenflow_soundex"                  => goldenflow_core::phonetic::soundex,
        "goldenflow_double_metaphone_primary" => goldenflow_core::phonetic::double_metaphone_primary,
        "goldenflow_double_metaphone_alt"     => goldenflow_core::phonetic::double_metaphone_alt,
    );

    // VARCHAR -> VARCHAR (nullable; None -> SQL NULL).
    register_opt_str!(con,
        "goldenflow_null_standardize"     => goldenflow_core::categorical::null_standardize,
        "goldenflow_email_extract_domain" => goldenflow_core::email::email_extract_domain,
        "goldenflow_url_normalize"        => goldenflow_core::url::url_normalize,
        "goldenflow_url_extract_domain"   => goldenflow_core::url::url_extract_domain,
        // identifier formatters/normalizers
        "goldenflow_cc_format"            => goldenflow_core::identifiers::luhn::cc_format,
        "goldenflow_cc_mask"              => goldenflow_core::identifiers::luhn::cc_mask,
        "goldenflow_iban_format"          => goldenflow_core::identifiers::iban::iban_format,
        "goldenflow_isbn_normalize"       => goldenflow_core::identifiers::isbn::isbn_normalize,
        "goldenflow_swift_format"         => goldenflow_core::identifiers::swift::swift_format,
        "goldenflow_vat_format"           => goldenflow_core::identifiers::vat::vat_format,
    );

    // VARCHAR -> BOOLEAN.
    register_prim_total!(con, bool, Boolean,
        "goldenflow_has_initial"   => goldenflow_core::names::has_initial,
        // identifier validators
        "goldenflow_cc_validate"   => goldenflow_core::identifiers::luhn::cc_validate,
        "goldenflow_iban_validate" => goldenflow_core::identifiers::iban::iban_validate,
        "goldenflow_isbn_validate" => goldenflow_core::identifiers::isbn::isbn_validate,
        "goldenflow_ean_validate"  => goldenflow_core::identifiers::ean::ean_validate,
        "goldenflow_swift_validate" => goldenflow_core::identifiers::swift::swift_validate,
        "goldenflow_vat_validate"  => goldenflow_core::identifiers::vat::vat_validate,
        "goldenflow_aba_validate"  => goldenflow_core::identifiers::aba::aba_validate,
        "goldenflow_imei_validate" => goldenflow_core::identifiers::imei::imei_validate,
    );
    register_prim_opt!(con, bool, Boolean,
        "goldenflow_boolean_normalize" => goldenflow_core::categorical::boolean_normalize,
        "goldenflow_email_validate"    => goldenflow_core::email::email_validate,
    );

    // VARCHAR -> DOUBLE (nullable numeric parsers).
    register_prim_opt!(con, f64, Double,
        "goldenflow_currency_strip"        => goldenflow_core::numeric::currency_strip,
        "goldenflow_percentage_normalize"  => goldenflow_core::numeric::percentage_normalize,
        "goldenflow_comma_decimal"         => goldenflow_core::numeric::comma_decimal,
        "goldenflow_scientific_to_decimal" => goldenflow_core::numeric::scientific_to_decimal,
    );

    // VARCHAR -> BIGINT.
    register_prim_opt!(con, i64, Bigint,
        "goldenflow_to_integer" => goldenflow_core::numeric::to_integer,
    );

    // Multi-output splits, exposed component-by-component (single-arg).
    register_str!(con,
        "goldenflow_split_name_first"         => split::name_first,
        "goldenflow_split_name_last"          => split::name_last,
        "goldenflow_split_name_reverse_first" => split::rev_first,
        "goldenflow_split_name_reverse_last"  => split::rev_last,
        "goldenflow_split_address_street"     => split::addr_street,
    );
    register_opt_str!(con,
        "goldenflow_split_address_city"  => split::addr_city,
        "goldenflow_split_address_state" => split::addr_state,
        "goldenflow_split_address_zip"   => split::addr_zip,
    );

    // Multi-argument kernels.
    con.register_scalar_function::<PhoneE164>("goldenflow_phone_e164")?;
    con.register_scalar_function::<PhoneNational>("goldenflow_phone_national")?;
    con.register_scalar_function::<PhoneCountryCode>("goldenflow_phone_country_code")?;
    con.register_scalar_function::<PhoneValid>("goldenflow_phone_valid")?;
    con.register_scalar_function::<Truncate>("goldenflow_truncate")?;
    con.register_scalar_function::<PadLeft>("goldenflow_pad_left")?;
    con.register_scalar_function::<PadRight>("goldenflow_pad_right")?;
    con.register_scalar_function::<MergeName>("goldenflow_merge_name")?;

    Ok(())
}

/// The DuckDB C Extension API entrypoint. Compiled only for the shippable
/// artifact (`loadable` feature); the `test-bundled` build omits it because it
/// links DuckDB in-process instead.
#[cfg(feature = "loadable")]
mod entry {
    use super::register_all;
    use duckdb::{duckdb_entrypoint_c_api, Connection, Result};
    use std::error::Error;

    #[duckdb_entrypoint_c_api]
    pub unsafe fn goldenflow_duckdb_init(con: Connection) -> Result<(), Box<dyn Error>> {
        register_all(&con)?;
        Ok(())
    }
}

#[cfg(all(test, feature = "test-bundled"))]
mod tests {
    use super::register_all;
    use duckdb::Connection;

    fn conn() -> Connection {
        let con = Connection::open_in_memory().expect("open duckdb");
        register_all(&con).expect("register udfs");
        con
    }

    fn sql_str(con: &Connection, udf: &str, input: &str) -> Option<String> {
        con.query_row(&format!("SELECT {udf}(?)"), [input], |r| r.get(0))
            .expect("query")
    }

    /// The SQL surface must return exactly what the `goldenflow-core` reference
    /// kernel returns -- the cross-surface parity contract -- across a sample of
    /// every family, asserted through a real in-process DuckDB. (The kernels'
    /// own exhaustive byte-parity lives in goldenflow-core; here we prove the
    /// DuckDB marshaling reproduces them.)
    #[test]
    fn total_str_udfs_match_reference() {
        use goldenflow_core as gf;
        let con = conn();
        // (udf, input, expected-from-kernel)
        let cases: &[(&str, &str, String)] = &[
            ("goldenflow_email_normalize", "  Foo.Bar@Example.COM ", gf::email::email_normalize("  Foo.Bar@Example.COM ")),
            ("goldenflow_email_lowercase", "USER@Domain.COM", gf::email::email_lowercase("USER@Domain.COM")),
            ("goldenflow_name_proper", "mcdonald o'brien", gf::names::name_proper("mcdonald o'brien")),
            ("goldenflow_strip_titles", "Dr. Jane Smith", gf::names::strip_titles("Dr. Jane Smith")),
            ("goldenflow_nickname_standardize", "Bob", gf::names::nickname_standardize("Bob")),
            ("goldenflow_gender_standardize", "F", gf::categorical::gender_standardize("F")),
            ("goldenflow_state_abbreviate", "California", gf::address::state_abbreviate("California")),
            ("goldenflow_zip_normalize", "12345-6789", gf::address::zip_normalize("12345-6789")),
            ("goldenflow_address_standardize", "123 Main Street", gf::address::address_standardize("123 Main Street")),
            ("goldenflow_collapse_whitespace", "a   b\t c", gf::text::collapse_whitespace("a   b\t c")),
            ("goldenflow_remove_html_tags", "<b>hi</b>", gf::text::remove_html_tags("<b>hi</b>")),
            ("goldenflow_lowercase", "HeLLo", gf::text::lowercase("HeLLo")),
            ("goldenflow_uppercase", "HeLLo", gf::text::uppercase("HeLLo")),
            ("goldenflow_title_case", "the quick fox", gf::text::title_case("the quick fox")),
            ("goldenflow_normalize_quotes", "\u{201c}hi\u{201d}", gf::text::normalize_quotes("\u{201c}hi\u{201d}")),
            ("goldenflow_strip", "  padded  ", gf::text::strip("  padded  ").to_string()),
            ("goldenflow_soundex", "Ashcraft", gf::phonetic::soundex("Ashcraft")),
            ("goldenflow_soundex", "Robert", gf::phonetic::soundex("Robert")),
        ];
        for (udf, input, expected) in cases {
            assert_eq!(
                sql_str(&con, udf, input).as_deref(),
                Some(expected.as_str()),
                "UDF {udf} on {input:?}",
            );
        }
    }

    /// Nullable kernels: a value round-trips, an unparseable input yields SQL NULL.
    #[test]
    fn nullable_str_udfs_null_on_none() {
        use goldenflow_core as gf;
        let con = conn();

        // url_normalize returns Some on a real url, None on junk.
        let good = "HTTP://Example.com/Path";
        assert_eq!(
            sql_str(&con, "goldenflow_url_normalize", good).as_deref(),
            gf::url::url_normalize(good).as_deref(),
        );
        assert_eq!(sql_str(&con, "goldenflow_url_normalize", "").as_deref(), None);

        // email_extract_domain: domain on a real address, NULL otherwise.
        assert_eq!(
            sql_str(&con, "goldenflow_email_extract_domain", "a@b.com").as_deref(),
            gf::email::email_extract_domain("a@b.com").as_deref(),
        );
        assert_eq!(
            sql_str(&con, "goldenflow_email_extract_domain", "no-domain").as_deref(),
            None,
        );
    }

    /// SQL NULL in -> SQL NULL out for both macro families (not "", not a panic).
    #[test]
    fn preserves_sql_null() {
        let con = conn();
        let total: Option<String> = con
            .query_row("SELECT goldenflow_email_normalize(NULL)", [], |r| r.get(0))
            .expect("query null total");
        assert_eq!(total, None);
        let nullable: Option<String> = con
            .query_row("SELECT goldenflow_url_normalize(NULL)", [], |r| r.get(0))
            .expect("query null nullable");
        assert_eq!(nullable, None);
    }

    /// Typed outputs (Slice 2b): BOOLEAN / DOUBLE / BIGINT marshaling through the
    /// primitive output-vector, each against the reference kernel.
    #[test]
    fn typed_outputs_marshal() {
        use goldenflow_core as gf;
        let con = conn();

        let valid: Option<bool> = con
            .query_row("SELECT goldenflow_cc_validate(?)", ["4242424242424242"], |r| r.get(0))
            .expect("bool udf");
        assert_eq!(valid, Some(gf::identifiers::luhn::cc_validate("4242424242424242")));
        assert_eq!(valid, Some(true));

        let dbl: Option<f64> = con
            .query_row("SELECT goldenflow_currency_strip(?)", ["$1,234.56"], |r| r.get(0))
            .expect("double udf");
        assert_eq!(dbl, gf::numeric::currency_strip("$1,234.56"));

        let int: Option<i64> = con
            .query_row("SELECT goldenflow_to_integer(?)", ["42"], |r| r.get(0))
            .expect("bigint udf");
        assert_eq!(int, gf::numeric::to_integer("42"));

        // Unparseable numeric -> SQL NULL.
        let is_null: bool = con
            .query_row("SELECT goldenflow_currency_strip('not money') IS NULL", [], |r| r.get(0))
            .expect("null double");
        assert!(is_null);
    }

    /// Thread the shared `identifiers_corpus.jsonl` -- the exact cross-surface
    /// oracle the Python + TS parity gates assert against -- through the compiled
    /// SQL surface. Every registered transform, every row: SQL output must equal
    /// the recorded expected value. End-to-end proof that the DuckDB surface is
    /// byte-identical to Python / TS / wasm.
    #[test]
    fn full_corpus_matches_through_sql() {
        use std::io::BufRead;
        let con = conn();
        let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../../python/goldenflow/tests/parity/identifiers_corpus.jsonl");
        let file = std::fs::File::open(&path)
            .unwrap_or_else(|e| panic!("open corpus {}: {e}", path.display()));

        let mut n = 0usize;
        for line in std::io::BufReader::new(file).lines() {
            let line = line.expect("read line");
            if line.trim().is_empty() {
                continue;
            }
            let row: serde_json::Value = serde_json::from_str(&line).expect("json");
            let transform = row["transform"].as_str().expect("transform");
            let udf = format!("goldenflow_{transform}");
            let expected = &row["expected"];
            let input = &row["input"];

            // Null input -> every UDF yields SQL NULL.
            if input.is_null() {
                let is_null: bool = con
                    .query_row(&format!("SELECT {udf}(NULL) IS NULL"), [], |r| r.get(0))
                    .unwrap_or_else(|e| panic!("{udf}(NULL): {e}"));
                assert!(is_null, "{transform} on NULL should be NULL");
                n += 1;
                continue;
            }
            let input = input.as_str().expect("string input");

            if expected.is_null() {
                let is_null: bool = con
                    .query_row(&format!("SELECT {udf}(?) IS NULL"), [input], |r| r.get(0))
                    .unwrap_or_else(|e| panic!("{udf}({input:?}): {e}"));
                assert!(is_null, "{transform} on {input:?} should be NULL");
            } else if let Some(b) = expected.as_bool() {
                let got: Option<bool> = con
                    .query_row(&format!("SELECT {udf}(?)"), [input], |r| r.get(0))
                    .unwrap_or_else(|e| panic!("{udf}({input:?}): {e}"));
                assert_eq!(got, Some(b), "{transform} on {input:?}");
            } else if transform == "to_integer" {
                let got: Option<i64> = con
                    .query_row(&format!("SELECT {udf}(?)"), [input], |r| r.get(0))
                    .unwrap_or_else(|e| panic!("{udf}({input:?}): {e}"));
                assert_eq!(got, expected.as_i64(), "{transform} on {input:?}");
            } else if let Some(f) = expected.as_f64() {
                let got: Option<f64> = con
                    .query_row(&format!("SELECT {udf}(?)"), [input], |r| r.get(0))
                    .unwrap_or_else(|e| panic!("{udf}({input:?}): {e}"));
                let got = got.expect("non-null double");
                assert!(
                    (got - f).abs() <= 1e-9 * (1.0 + f.abs()),
                    "{transform} on {input:?}: {got} vs {f}",
                );
            } else {
                let s = expected.as_str().expect("string expected");
                let got: Option<String> = con
                    .query_row(&format!("SELECT {udf}(?)"), [input], |r| r.get(0))
                    .unwrap_or_else(|e| panic!("{udf}({input:?}): {e}"));
                assert_eq!(got.as_deref(), Some(s), "{transform} on {input:?}");
            }
            n += 1;
        }
        assert!(n > 400, "expected the full corpus, only saw {n} rows");
    }

    /// Multi-output splits (component UDFs) match the reference tuple.
    #[test]
    fn split_components_match_reference() {
        use goldenflow_core as gf;
        let con = conn();

        let name = "Jane Smith";
        let (f, l) = gf::names::split_name(name);
        assert_eq!(sql_str(&con, "goldenflow_split_name_first", name).as_deref(), Some(f.as_str()));
        assert_eq!(sql_str(&con, "goldenflow_split_name_last", name).as_deref(), Some(l.as_str()));

        let rev = "Smith, Jane";
        let (rf, rl) = gf::names::split_name_reverse(rev);
        assert_eq!(sql_str(&con, "goldenflow_split_name_reverse_first", rev).as_deref(), Some(rf.as_str()));
        assert_eq!(sql_str(&con, "goldenflow_split_name_reverse_last", rev).as_deref(), Some(rl.as_str()));

        let addr = "123 Main St, Springfield, IL 62704";
        let (street, city, state, zip) = gf::address::split_address(addr);
        assert_eq!(sql_str(&con, "goldenflow_split_address_street", addr).as_deref(), Some(street.as_str()));
        assert_eq!(sql_str(&con, "goldenflow_split_address_city", addr), city);
        assert_eq!(sql_str(&con, "goldenflow_split_address_state", addr), state);
        assert_eq!(sql_str(&con, "goldenflow_split_address_zip", addr), zip);
        // unparseable address -> street=original, the rest NULL
        assert_eq!(
            sql_str(&con, "goldenflow_split_address_city", "not an address"),
            gf::address::split_address("not an address").1,
        );
    }

    /// Multi-argument kernels: value + NULL behaviour, all vs the reference.
    #[test]
    fn multi_arg_kernels_match_reference() {
        use goldenflow_core as gf;
        let con = conn();

        // truncate(s, n)
        let got: Option<String> = con
            .query_row("SELECT goldenflow_truncate(?, ?)", duckdb::params!["hello world", 5i64], |r| r.get(0))
            .unwrap();
        assert_eq!(got.as_deref(), Some(gf::text::truncate("hello world", 5).as_str()));
        // NULL length -> NULL
        let n: bool = con
            .query_row("SELECT goldenflow_truncate('x', NULL) IS NULL", [], |r| r.get(0))
            .unwrap();
        assert!(n);

        // pad_left(s, width, pad)
        let got: Option<String> = con
            .query_row("SELECT goldenflow_pad_left(?, ?, ?)", duckdb::params!["7", 3i64, "0"], |r| r.get(0))
            .unwrap();
        assert_eq!(got.as_deref(), Some(gf::text::pad_left("7", 3, '0').as_str()));
        let got: Option<String> = con
            .query_row("SELECT goldenflow_pad_right(?, ?, ?)", duckdb::params!["7", 3i64, "."], |r| r.get(0))
            .unwrap();
        assert_eq!(got.as_deref(), Some(gf::text::pad_right("7", 3, '.').as_str()));

        // merge_name(first, last) -- both nullable
        let got: Option<String> = con
            .query_row("SELECT goldenflow_merge_name(?, ?)", duckdb::params!["Jane", "Smith"], |r| r.get(0))
            .unwrap();
        assert_eq!(got, gf::names::merge_name(Some("Jane"), Some("Smith")));
        let nn: bool = con
            .query_row("SELECT goldenflow_merge_name(NULL, NULL) IS NULL", [], |r| r.get(0))
            .unwrap();
        assert!(nn);
        // A NULL arg short-circuits to SQL NULL (DuckDB scalar UDFs propagate
        // NULL; duckdb-rs exposes no special-null-handling knob). This is the
        // one place the SQL surface differs from the pure kernel, which would
        // coalesce None -> Some("Smith"); with any non-NULL-only input it's
        // byte-identical.
        let one_null: bool = con
            .query_row("SELECT goldenflow_merge_name(NULL, ?) IS NULL", ["Smith"], |r| r.get(0))
            .unwrap();
        assert!(one_null);

        // phone (NANP, nanp_only=true parity-safe): value on code-1, NULL off it
        let reg = gf::phone::region_of("US");
        let got: Option<String> = con
            .query_row("SELECT goldenflow_phone_e164(?, ?)", duckdb::params!["1-800-356-9377", "US"], |r| r.get(0))
            .unwrap();
        assert_eq!(got, gf::phone::e164(reg, "1-800-356-9377", true));
        let cc: Option<i64> = con
            .query_row("SELECT goldenflow_phone_country_code(?, ?)", duckdb::params!["212-555-0100", "US"], |r| r.get(0))
            .unwrap();
        assert_eq!(cc, gf::phone::country_code(gf::phone::region_of("US"), "212-555-0100", true));
    }
}
