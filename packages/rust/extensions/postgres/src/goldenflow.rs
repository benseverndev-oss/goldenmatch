//! GoldenFlow transform functions for the goldenmatch Postgres extension.
//!
//! Mirrors the 8 DuckDB `goldenflow_*` UDFs in
//! `packages/rust/extensions/duckdb/goldenmatch_duckdb/goldenflow.py` so the
//! Postgres and DuckDB SQL surfaces expose the same goldenflow transforms with
//! identical semantics -- closing the last DuckDB <-> Postgres parity gap.
//!
//! Each function corresponds to a goldenflow transform (the pg_extern -> key
//! mapping matches the DuckDB `_UDF_REGISTRY` exactly). Two dispatch modes:
//! **native-direct** functions run a `goldenflow-core` kernel (no per-row embedded
//! CPython), byte-parity-proven against the polars transform by a golden corpus;
//! **bridged** functions still call the generic
//! `goldenmatch_bridge::api::goldenflow_transform` because their kernel is absent
//! or deliberately not native.
//!
//! | pg_extern                          | goldenflow transform   | dispatch     |
//! |------------------------------------|------------------------|--------------|
//! | `goldenflow_normalize_email`       | `email_normalize`      | native (P9)  |
//! | `goldenflow_canonicalize_url`      | `url_normalize`        | native (P9)  |
//! | `goldenflow_canonicalize_address`  | `address_standardize`  | native (P9)  |
//! | `goldenflow_normalize_name_proper` | `name_proper`          | native (P9)  |
//! | `goldenflow_strip`                 | `strip`                | native (P9)  |
//! | `goldenflow_whitespace_normalize`  | `collapse_whitespace`  | native (P9)  |
//! | `goldenflow_normalize_phone`       | `phone_e164`           | bridged*     |
//! | `goldenflow_normalize_date`        | `date_iso8601`         | bridged*     |
//!
//! *bridged rationale: `phone`'s core kernel is NANP-only (not a drop-in) and
//! `date` is deliberately not native (polars vectorizes it; per-row chrono is
//! slower + a 2-digit-year hazard). These are the only two goldenflow SQL
//! externs still on the CPython bridge; every transform with a drop-in
//! `goldenflow-core` kernel is now native-direct.
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

/// Normalize an email address (lowercase, trim, +tag strip, gmail-dot strip).
/// **De-bridged (P9):** runs native-direct over `goldenflow-core::email::
/// email_normalize` (no embedded CPython per row), byte-identical to the
/// goldenflow polars `email_normalize` transform — proven against a corpus in
/// `goldenflow-core/tests/email_url_address_golden.rs`. The reference returns a
/// string on every non-NULL input (invalid values are preserved verbatim), and
/// the extern is `STRICT`, so there is no null-boundary. Same signature + output,
/// so no SQL/version change.
///
/// ```sql
/// SELECT goldenflow_normalize_email('  John.Doe@Example.COM ');
/// ```
#[pg_extern]
pub fn goldenflow_normalize_email(value: String) -> String {
    goldenflow_core::email::email_normalize(&value)
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

/// Proper-case a personal name (title-case + Mc/O' fixups).
/// **De-bridged (P9):** runs native-direct over `goldenflow-core::names::
/// name_proper` (no embedded CPython per row), byte-identical to the goldenflow
/// polars `name_proper` transform — including Python `str.title()`'s quirks (e.g.
/// `don't -> Don'T`) — proven against a corpus in
/// `goldenflow-core/tests/email_url_address_golden.rs`. Returns a string on every
/// non-NULL input (the extern is `STRICT`), so no null-boundary. Same signature +
/// output, so no SQL/version change.
///
/// ```sql
/// SELECT goldenflow_normalize_name_proper('JOHN MCDONALD');
/// ```
#[pg_extern]
pub fn goldenflow_normalize_name_proper(value: String) -> String {
    goldenflow_core::names::name_proper(&value)
}

/// Canonicalize a URL (ensure scheme, lowercase domain, strip trailing slash).
/// **De-bridged (P9):** runs native-direct over `goldenflow-core::url::
/// url_normalize` (no embedded CPython per row), byte-identical to the goldenflow
/// polars `url_normalize` transform — proven against a corpus in
/// `goldenflow-core/tests/email_url_address_golden.rs`. That kernel returns
/// `Option<String>` (empty/whitespace input -> `None`); the reference transform
/// maps `None` to a NULL cell, which the bridge turned back into input-passthrough
/// (`unwrap_or(value)`) — replicated here so the SQL output is unchanged.
///
/// ```sql
/// SELECT goldenflow_canonicalize_url('HTTP://Example.com/Path/');
/// ```
#[pg_extern]
pub fn goldenflow_canonicalize_url(value: String) -> String {
    goldenflow_core::url::url_normalize(&value).unwrap_or(value)
}

/// Standardize a postal address.
/// **De-bridged (P9):** runs native-direct over `goldenflow-core::address::
/// address_standardize` (no embedded CPython per row), byte-identical to the
/// goldenflow polars `address_standardize` transform — proven against a corpus in
/// `goldenflow-core/tests/email_url_address_golden.rs`. Returns a string on every
/// non-NULL input (the extern is `STRICT`), so no null-boundary. Same signature +
/// output, so no SQL/version change.
///
/// ```sql
/// SELECT goldenflow_canonicalize_address('123 main st. apt 4');
/// ```
#[pg_extern]
pub fn goldenflow_canonicalize_address(value: String) -> String {
    goldenflow_core::address::address_standardize(&value)
}

/// Strip leading/trailing whitespace. **De-bridged (P9):** runs native-direct
/// over `goldenflow-core::text::strip` (no embedded CPython per row), which is
/// byte-identical to the goldenflow polars `strip` transform — proven against a
/// Unicode corpus in `goldenflow-core/tests/text_golden.rs`. Same signature +
/// output, so no SQL/version change.
///
/// ```sql
/// SELECT goldenflow_strip('  hello  ');
/// ```
#[pg_extern]
pub fn goldenflow_strip(value: String) -> String {
    goldenflow_core::text::strip(&value).to_string()
}

/// Collapse internal runs of whitespace to a single space. **De-bridged (P9):**
/// runs native-direct over `goldenflow-core::text::collapse_whitespace` (no
/// embedded CPython per row), byte-identical to the goldenflow polars
/// `collapse_whitespace` transform (`\s{2,}` -> a single space over the Unicode
/// `White_Space` set) — proven in `goldenflow-core/tests/text_golden.rs`. Same
/// signature + output, so no SQL/version change.
///
/// ```sql
/// SELECT goldenflow_whitespace_normalize('a    b   c');
/// ```
#[pg_extern]
pub fn goldenflow_whitespace_normalize(value: String) -> String {
    goldenflow_core::text::collapse_whitespace(&value)
}
