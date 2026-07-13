//! Native auto-detect profiling shim — exposes `goldenflow_core::profile` to
//! Python. Two entry points: the list path (`infer_type_list_arrow`, here) and
//! the columnar path (`Column.profile()`, in `column.rs`). Both delegate to the
//! owned `goldenflow_core::profile` kernel so every surface makes the identical
//! type-inference decision.

use goldenflow_core::profile::{hint_from_str, infer_type};
use pyo3::prelude::*;

/// Path 2a: infer the type of a plain Python list of `Option<str>` (already
/// stringified by the caller via `str(v)`). Returns just the type string — the
/// list path computes null/unique/samples in Python over RAW values (dodging the
/// `[1, "1"]` stringify-collision).
#[pyfunction]
pub fn infer_type_list_arrow(values: Vec<Option<String>>, hint: &str) -> String {
    let view: Vec<Option<&str>> = values.iter().map(|o| o.as_deref()).collect();
    infer_type(&view, hint_from_str(hint))
}

#[cfg(test)]
mod tests {
    use super::infer_type_list_arrow;

    #[test]
    fn hint_and_infer() {
        // Numeric hint short-circuits regardless of values.
        assert_eq!(
            infer_type_list_arrow(vec![Some("x".into())], "numeric"),
            "numeric"
        );
        // Utf8 hint (any non-{numeric,boolean,date} string) runs the matchers.
        let emails = vec![
            Some("a@b.co".into()),
            Some("x@y.io".into()),
            Some("p@q.net".into()),
        ];
        assert_eq!(infer_type_list_arrow(emails, "string"), "email");
    }
}
