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

/// Read the sole VARCHAR argument of a chunk as `(FlatVector, &[duckdb_string_t])`.
/// The macros below share this; nulls are checked per-row via `in_vec.row_is_null`.
macro_rules! read_str_input {
    ($input:expr) => {{
        let in_vec = $input.flat_vector(0);
        let rows =
            unsafe { in_vec.as_slice_with_len::<duckdb_string_t>($input.len()) };
        (in_vec, rows)
    }};
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
                    let (in_vec, rows) = read_str_input!(input);
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
                    let (in_vec, rows) = read_str_input!(input);
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
}
