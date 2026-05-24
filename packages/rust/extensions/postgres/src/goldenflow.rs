//! GoldenFlow transform functions for the goldenmatch Postgres extension.
//!
//! Mirrors the 8 DuckDB `goldenflow_*` UDFs in
//! `packages/rust/extensions/duckdb/goldenmatch_duckdb/goldenflow.py` so the
//! Postgres and DuckDB SQL surfaces expose the same goldenflow transforms with
//! identical semantics -- closing the last DuckDB <-> Postgres parity gap.
//!
//! Each function is a scalar `text -> text` wrapper over the single generic
//! bridge fn `goldenmatch_bridge::api::goldenflow_transform`, passing its fixed
//! goldenflow registry key (e.g. `email_normalize`). The mapping of pg_extern
//! name -> transform key matches the DuckDB `_UDF_REGISTRY` exactly:
//!
//! | pg_extern                          | goldenflow transform   |
//! |------------------------------------|------------------------|
//! | `goldenflow_normalize_email`       | `email_normalize`      |
//! | `goldenflow_normalize_phone`       | `phone_e164`           |
//! | `goldenflow_normalize_date`        | `date_iso8601`         |
//! | `goldenflow_normalize_name_proper` | `name_proper`          |
//! | `goldenflow_canonicalize_url`      | `url_normalize`        |
//! | `goldenflow_canonicalize_address`  | `address_standardize`  |
//! | `goldenflow_strip`                 | `strip`                |
//! | `goldenflow_whitespace_normalize`  | `collapse_whitespace`  |
//!
//! ## Fail-open contract
//! The bridge fn passes the input through unchanged when goldenflow isn't
//! importable, the transform is missing, or the transform errors -- it never
//! raises for those. A genuine `BridgeError` (e.g. goldenmatch/CPython init
//! failure) still surfaces via `pgrx::error!`. The SQL functions are `STRICT`
//! (NULL input -> NULL output) so these wrappers always receive a real string.

use pgrx::prelude::*;

/// Apply one named goldenflow transform to a single value via the bridge.
/// Centralises the `Result` handling so each `#[pg_extern]` stays a one-liner.
fn apply(transform_name: &str, value: String) -> String {
    match goldenmatch_bridge::api::goldenflow_transform(transform_name, &value) {
        Ok(out) => out,
        Err(e) => pgrx::error!("goldenmatch: {}", e),
    }
}

/// Normalize an email address (lowercase, trim, provider canonicalisation).
/// Wraps the goldenflow `email_normalize` transform.
///
/// ```sql
/// SELECT goldenflow_normalize_email('  John.Doe@Example.COM ');
/// ```
#[pg_extern]
pub fn goldenflow_normalize_email(value: String) -> String {
    apply("email_normalize", value)
}

/// Normalize a phone number to E.164 form.
/// Wraps the goldenflow `phone_e164` transform.
///
/// ```sql
/// SELECT goldenflow_normalize_phone('(555) 123-4567');
/// ```
#[pg_extern]
pub fn goldenflow_normalize_phone(value: String) -> String {
    apply("phone_e164", value)
}

/// Normalize a date to ISO-8601 (`YYYY-MM-DD`).
/// Wraps the goldenflow `date_iso8601` transform.
///
/// ```sql
/// SELECT goldenflow_normalize_date('03/14/2025');
/// ```
#[pg_extern]
pub fn goldenflow_normalize_date(value: String) -> String {
    apply("date_iso8601", value)
}

/// Proper-case a personal name.
/// Wraps the goldenflow `name_proper` transform.
///
/// ```sql
/// SELECT goldenflow_normalize_name_proper('JOHN MCDONALD');
/// ```
#[pg_extern]
pub fn goldenflow_normalize_name_proper(value: String) -> String {
    apply("name_proper", value)
}

/// Canonicalize a URL (scheme/host lowercasing, tracking-param stripping).
/// Wraps the goldenflow `url_normalize` transform.
///
/// ```sql
/// SELECT goldenflow_canonicalize_url('HTTP://Example.com/Path/');
/// ```
#[pg_extern]
pub fn goldenflow_canonicalize_url(value: String) -> String {
    apply("url_normalize", value)
}

/// Standardize a postal address.
/// Wraps the goldenflow `address_standardize` transform.
///
/// ```sql
/// SELECT goldenflow_canonicalize_address('123 main st. apt 4');
/// ```
#[pg_extern]
pub fn goldenflow_canonicalize_address(value: String) -> String {
    apply("address_standardize", value)
}

/// Strip leading/trailing whitespace.
/// Wraps the goldenflow `strip` transform.
///
/// ```sql
/// SELECT goldenflow_strip('  hello  ');
/// ```
#[pg_extern]
pub fn goldenflow_strip(value: String) -> String {
    apply("strip", value)
}

/// Collapse internal runs of whitespace to a single space.
/// Wraps the goldenflow `collapse_whitespace` transform.
///
/// ```sql
/// SELECT goldenflow_whitespace_normalize('a    b   c');
/// ```
#[pg_extern]
pub fn goldenflow_whitespace_normalize(value: String) -> String {
    apply("collapse_whitespace", value)
}
