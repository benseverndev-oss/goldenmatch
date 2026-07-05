//! GoldenFlow DuckDB extension -- a zero-Python SQL surface over the owned
//! reference kernels.
//!
//! Each SQL scalar function is a thin [`VScalar`] that runs the corresponding
//! `goldenflow-core` kernel (the byte-parity oracle) element-wise over a DuckDB
//! data chunk. Because the kernel *is* the cross-surface reference, SQL output
//! is byte-identical to the Python / TS / wasm surfaces by construction -- and
//! there is no CPython interpreter anywhere in the process.
//!
//! Slice 2 exposes the full single-argument `VARCHAR -> VARCHAR` catalogue
//! (address, email, names, text, categorical), both the total (`-> String`) and
//! nullable (`-> Option<String>`) shapes. Typed outputs (BOOLEAN / DOUBLE /
//! BIGINT: validators + numeric parsers) and the identifier family are Slice 2b;
//! multi-argument / multi-output kernels (phone, `split_*`, `truncate`, `pad_*`,
//! `merge_name`, `auto_correct`) are later slices.

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
}
