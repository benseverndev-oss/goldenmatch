//! `goldenmatch-documents-core` -- pyo3-free document-ingest kernels. Single source
//! of truth for schema validation, response parsing, prompt text, and record
//! normalization. No I/O, no pyo3. String-in / string-out at the boundary.
pub mod classify;
pub mod extract_structured;
pub mod normalize;
pub mod parse;
pub mod prompt;
pub mod schema;
pub mod templates;

#[cfg(test)]
mod tests {
    #[test]
    fn crate_builds() {
        assert_eq!(2 + 2, 4);
    }
}
