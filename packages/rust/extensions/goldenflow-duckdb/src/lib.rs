//! GoldenFlow DuckDB extension -- a zero-Python SQL surface over the owned
//! reference kernels.
//!
//! Each SQL scalar function is a thin [`VScalar`] that runs the corresponding
//! `goldenflow-core` kernel (the byte-parity oracle) element-wise over a DuckDB
//! data chunk. Because the kernel *is* the cross-surface reference, SQL output
//! is byte-identical to the Python / TS / wasm surfaces by construction -- and
//! there is no CPython interpreter anywhere in the process.
//!
//! Spike scope (Slice 1): two VARCHAR->VARCHAR transforms + a hermetic
//! in-process parity test. Breadth (the full byte-parity catalogue) and the
//! per-platform `.duckdb_extension` distribution build are later slices.

use duckdb::{
    core::{DataChunkHandle, Inserter, LogicalTypeId},
    ffi::duckdb_string_t,
    types::DuckString,
    vscalar::{ScalarFunctionSignature, VScalar},
    vtab::arrow::WritableVector,
    Connection, Result,
};
use std::error::Error;

/// Define a `VARCHAR -> VARCHAR` [`VScalar`] that applies a
/// `fn(&str) -> String` goldenflow-core kernel to every non-null row of the
/// chunk, preserving SQL NULLs.
macro_rules! str_scalar {
    ($ty:ident, $kernel:path) => {
        struct $ty;
        impl VScalar for $ty {
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
    };
}

str_scalar!(EmailNormalize, goldenflow_core::email::email_normalize);
str_scalar!(NameProper, goldenflow_core::names::name_proper);

/// Register every GoldenFlow scalar UDF on a connection. Single source of truth
/// for both the loadable entrypoint and the in-process test harness, so the two
/// can never drift on names.
fn register_all(con: &Connection) -> Result<(), Box<dyn Error>> {
    con.register_scalar_function::<EmailNormalize>("goldenflow_normalize_email")?;
    con.register_scalar_function::<NameProper>("goldenflow_normalize_name_proper")?;
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

    /// The SQL surface must return exactly what the goldenflow-core reference
    /// kernel returns -- that is the cross-surface parity contract, asserted
    /// here through a real in-process DuckDB.
    #[test]
    fn sql_output_matches_reference_kernel() {
        let con = Connection::open_in_memory().expect("open duckdb");
        register_all(&con).expect("register udfs");

        let email_cases = ["  Foo.Bar@Example.COM ", "no-at-sign", "A@B@C.com", ""];
        for input in email_cases {
            let got: String = con
                .query_row("SELECT goldenflow_normalize_email(?)", [input], |r| r.get(0))
                .expect("query email");
            assert_eq!(
                got,
                goldenflow_core::email::email_normalize(input),
                "email_normalize parity on {input:?}",
            );
        }

        let name_cases = ["mcdonald o'brien", "JANE  SMITH", "de la cruz", ""];
        for input in name_cases {
            let got: String = con
                .query_row("SELECT goldenflow_normalize_name_proper(?)", [input], |r| r.get(0))
                .expect("query name");
            assert_eq!(
                got,
                goldenflow_core::names::name_proper(input),
                "name_proper parity on {input:?}",
            );
        }
    }

    /// SQL NULL in -> SQL NULL out (not the empty string, not a panic).
    #[test]
    fn preserves_sql_null() {
        let con = Connection::open_in_memory().expect("open duckdb");
        register_all(&con).expect("register udfs");
        let got: Option<String> = con
            .query_row("SELECT goldenflow_normalize_email(NULL)", [], |r| r.get(0))
            .expect("query null");
        assert_eq!(got, None);
    }
}
