//! `goldenmatch-suggest-core` -- pyo3-free config-suggestion kernel.
//!
//! Canonical source of truth for config suggestions: ingests a finished run's
//! Arrow artifacts, reduces them, runs the suggestion rules, generates rationale
//! text, and ranks. Shared by construction across the `goldenmatch-native` pyo3
//! shim and (later) the datafusion-udf FFI + TS/WASM surfaces. No I/O, no pyo3.

pub mod contract;
pub mod diagnostics;

#[cfg(test)]
mod tests {
    #[test]
    fn crate_builds() {
        assert_eq!(2 + 2, 4);
    }
}
