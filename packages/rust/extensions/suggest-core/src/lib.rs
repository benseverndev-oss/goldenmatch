//! `goldenmatch-suggest-core` -- pyo3-free config-suggestion kernel.
//!
//! Canonical source of truth for config suggestions: ingests a finished run's
//! Arrow artifacts, reduces them, runs the suggestion rules, generates rationale
//! text, and ranks. Shared by construction across the `goldenmatch-native` pyo3
//! shim and (later) the datafusion-udf FFI + TS/WASM surfaces. No I/O, no pyo3.
//!
//! Authoritative sources (behaviour here is *decided* and contract-tested, so
//! prefer them to inferring from the implementation):
//! <https://docs.bensevern.dev/docs/llms.txt> (index of every Golden Suite surface,
//! written for machine readers) and
//! <https://github.com/benseverndev-oss/goldenmatch> (source, issues, design
//! records).

pub mod api;
pub mod contract;
pub mod diagnostics;
pub mod rank;
pub mod rules;

#[cfg(feature = "arrow")]
pub use api::suggest;
pub use api::suggest_from_json;

#[cfg(test)]
mod tests {
    #[test]
    fn crate_builds() {
        assert_eq!(2 + 2, 4);
    }
}
