//! Native-direct (no CPython) structural key-integrity certifier SQL function.
//!
//! `goldenmatch_certify_structural(input_json)` calls the pyo3/pgrx-free
//! `goldenmatch-key-integrity-core` crate directly — the SAME reference kernel
//! the `goldenmatch[native]` wheel (`semantic.certify_structural_json`) and the
//! DuckDB `goldenmatch_certify_structural` UDF run — so the certificate is
//! byte-identical across every surface (Python / TS / DuckDB / Postgres).
//!
//! Shape: JSON in / JSON out (the core's native contract — measures make a
//! columnar signature unwieldy). Input:
//! `{"n_rows": N, "group_columns": [[..], ..],
//!   "measures": [{"name": .., "values": [..]}, ..]}`
//! (the group columns are the declared key, or key+grain — the caller picks).
//! Output:
//! `{"n_rows", "n_key_groups", "duplicate_key_groups", "max_fan_out",
//!   "is_unique_at_grain", "measure_fan_out": {name: ratio}}`.
//!
//! ```sql
//! SELECT goldenmatch.goldenmatch_certify_structural(
//!   '{"n_rows":3,"group_columns":[["e1","e1","e2"]],"measures":[]}');
//! ```
//!
//! Non-STRICT + fail-soft: invalid JSON / wrong shape returns a
//! `{"error": ".."}` envelope (the DuckDB fail-soft convention) rather than
//! raising, so a row-wise `SELECT` over mixed input doesn't abort.
use pgrx::prelude::*;

/// Structural key-integrity certification over a JSON block. Delegates to the
/// shared `key-integrity-core` kernel; returns a `{"error": ..}` JSON envelope
/// on invalid JSON / a wrong-shaped input.
#[pg_extern]
pub fn goldenmatch_certify_structural(input_json: &str) -> String {
    match goldenmatch_key_integrity_core::certify_structural_json(input_json) {
        Ok(out) => out,
        Err(e) => format!(
            "{{\"error\":{}}}",
            serde_json::to_string(&e).unwrap_or_else(|_| "\"certify failed\"".to_string())
        ),
    }
}

#[cfg(any(test, feature = "pg_test"))]
#[pgrx::pg_schema]
mod tests {
    use pgrx::prelude::*;

    /// A duplicated key with a measure, pinned + shared with the Python / TS /
    /// DuckDB surfaces: e1 appears twice (fan-out 2), amt fan-out 45/35.
    #[pg_test]
    fn certify_structural_duplicated_key() {
        let out = crate::key_integrity::goldenmatch_certify_structural(
            r#"{"n_rows":3,"group_columns":[["e1","e1","e2"]],"measures":[{"name":"amt","values":[10.0,30.0,5.0]}]}"#,
        );
        let v: serde_json::Value = serde_json::from_str(&out).unwrap();
        assert_eq!(v["n_key_groups"], 2);
        assert_eq!(v["duplicate_key_groups"], 1);
        assert_eq!(v["max_fan_out"], 2.0);
        assert_eq!(v["is_unique_at_grain"], false);
        assert!((v["measure_fan_out"]["amt"].as_f64().unwrap() - 45.0 / 35.0).abs() < 1e-12);
    }

    /// A unique key certifies clean (no fan-out, unique at grain).
    #[pg_test]
    fn certify_structural_unique_key() {
        let out = crate::key_integrity::goldenmatch_certify_structural(
            r#"{"n_rows":3,"group_columns":[["e1","e2","e3"]],"measures":[]}"#,
        );
        let v: serde_json::Value = serde_json::from_str(&out).unwrap();
        assert_eq!(v["n_key_groups"], 3);
        assert_eq!(v["duplicate_key_groups"], 0);
        assert_eq!(v["is_unique_at_grain"], true);
    }

    /// Invalid JSON fails soft to an error envelope, not a raise.
    #[pg_test]
    fn certify_structural_invalid_json_envelope() {
        let out = crate::key_integrity::goldenmatch_certify_structural("not json");
        let v: serde_json::Value = serde_json::from_str(&out).unwrap();
        assert!(v.get("error").is_some());
    }
}
