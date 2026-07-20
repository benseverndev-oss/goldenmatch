//! Data conversion utilities for the GoldenMatch bridge.
//!
//! Two strategies available:
//! - Arrow: Uses Polars' native Arrow IPC for efficient bulk data transfer (preferred)
//! - JSON: Fallback for small data or when Arrow deps unavailable

use crate::error::BridgeError;
use pyo3::prelude::*;
use pyo3::types::PyDict;

/// A single column's typed values read from a Postgres table, NULL-aware.
///
/// Only the column types whose columnar `pa.array` build is **byte-identical to
/// `pa.Table.from_pylist`** (the JSON path) are represented here — int/float
/// widths are widened to `i64`/`f64` to match `row_to_json` → Python
/// `int`/`float` → arrow `int64`/`double`, and a fully-NULL column becomes
/// [`ColumnData::Null`] to match `from_pylist`'s `null`-type inference. Any
/// other Postgres type forces the whole-table JSON fallback in
/// `spi::read_table`, so [`columns_to_arrow_df`] is provably schema-parity with
/// [`json_to_arrow_df`].
pub enum ColumnData {
    Text(Vec<Option<String>>),
    Int(Vec<Option<i64>>),
    Float(Vec<Option<f64>>),
    Bool(Vec<Option<bool>>),
    /// A column with zero non-NULL values → pyarrow `null` type (length n),
    /// matching `from_pylist`'s inference for an all-`None` column.
    Null(usize),
}

/// Columnar table data: parallel `names` / `columns` (same length, same order).
pub struct TableColumns {
    pub names: Vec<String>,
    pub columns: Vec<ColumnData>,
}

/// Table rows handed from the Postgres SPI layer to the arrow builder: either a
/// JSON records string (the legacy `row_to_json` path / the exotic-type
/// fallback) or typed [`TableColumns`] (the P4 Arrow-native path). Both funnel
/// through [`table_to_arrow_df`] and produce a byte-identical `pa.Table`, so
/// every table-op consumer stays parity-safe regardless of which the SPI layer
/// chose.
pub enum TableData {
    Json(String),
    Columns(TableColumns),
}

/// Build a `pa.Table` from typed columns without a JSON round-trip.
///
/// Each column becomes a `pa.array(values, type)` (or `pa.nulls(n)` for an
/// all-NULL column) and the table is assembled via `pa.Table.from_pydict`. The
/// per-type mapping is chosen in `spi::read_table` so the resulting schema +
/// data match `json_to_arrow_df` exactly (proven in `tests::columnar_matches_json`).
pub fn columns_to_arrow_df(py: Python<'_>, cols: &TableColumns) -> Result<PyObject, BridgeError> {
    let pa = py.import("pyarrow")?;
    let dict = PyDict::new(py);
    for (name, col) in cols.names.iter().zip(cols.columns.iter()) {
        let arr = match col {
            ColumnData::Null(n) => pa.call_method1("nulls", (*n,))?,
            ColumnData::Text(v) => {
                let ty = pa.call_method0("string")?;
                pa.call_method1("array", (v.clone(), ty))?
            }
            ColumnData::Int(v) => {
                let ty = pa.call_method0("int64")?;
                pa.call_method1("array", (v.clone(), ty))?
            }
            ColumnData::Float(v) => {
                let ty = pa.call_method0("float64")?;
                pa.call_method1("array", (v.clone(), ty))?
            }
            ColumnData::Bool(v) => {
                let ty = pa.call_method0("bool_")?;
                pa.call_method1("array", (v.clone(), ty))?
            }
        };
        dict.set_item(name, arr)?;
    }
    let table = pa.getattr("Table")?.call_method1("from_pydict", (dict,))?;
    Ok(table.into_pyobject(py).unwrap().unbind())
}

/// Dispatch table rows to a `pa.Table`: JSON via [`json_to_arrow_df`], columns
/// via [`columns_to_arrow_df`]. The single choke point every table-op bridge fn
/// funnels through, so parity is anchored in one place.
pub fn table_to_arrow_df(py: Python<'_>, data: &TableData) -> Result<PyObject, BridgeError> {
    match data {
        TableData::Json(json) => json_to_arrow_df(py, json),
        TableData::Columns(cols) => columns_to_arrow_df(py, cols),
    }
}

/// Convert a JSON string of records into a Python **pyarrow Table**.
///
/// Arrow-native (no polars): `json.loads` -> `pyarrow.Table.from_pylist`. The
/// goldenmatch core-API surface (dedupe / auto_configure / match + the aux
/// validate/autofix/anomaly/profile/preflight/postflight functions) is
/// arrow-native, so a `pa.Table` feeds every call site directly. This is what
/// lets the bridge lanes run without the `[polars]` extra (D6 zero-polars).
///
/// For table data from Postgres SPI (JSON via `row_to_json`), `from_pylist`
/// infers the schema the same way `pl.read_json` did.
pub fn json_to_arrow_df(py: Python<'_>, json_records: &str) -> Result<PyObject, BridgeError> {
    let json_mod = py.import("json")?;
    let pa = py.import("pyarrow")?;

    let records = json_mod.call_method1("loads", (json_records,))?;
    let table = pa
        .getattr("Table")?
        .call_method1("from_pylist", (records,))?;

    Ok(table.into_pyobject(py).unwrap().unbind())
}

/// Convert a pyarrow Table (or a genuine Polars DataFrame) to a JSON string of records.
///
/// Arrow-native (no polars): a `pa.Table` goes `to_pylist` -> `json.dumps`. A
/// genuine polars frame (which still has `write_json`, e.g. when a caller passes
/// one on the polars lane) passes through `write_json` unchanged for
/// byte-identical output. `DedupeResult.golden` / `MatchResult.matched` are
/// pyarrow Tables since v3.0.0, so the arrow branch is the default.
pub fn arrow_df_to_json(py: Python<'_>, df: &PyObject) -> Result<String, BridgeError> {
    let bound = df.bind(py);
    if bound.hasattr("write_json")? {
        // Genuine polars frame -> byte-identical legacy path (polars present).
        let json_bytes = bound.call_method0("write_json")?;
        let json_str: String = json_bytes.extract()?;
        return Ok(json_str);
    }
    // pa.Table -> list[dict] -> JSON array of record objects.
    let json_mod = py.import("json")?;
    let records = bound.call_method0("to_pylist")?;
    let json_str: String = json_mod.call_method1("dumps", (records,))?.extract()?;
    Ok(json_str)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_json_roundtrip() {
        pyo3::prepare_freethreaded_python();

        Python::with_gil(|py| {
            // Arrow-native path: needs pyarrow (a hard goldenmatch dep), NOT polars.
            if py.import("pyarrow").is_err() {
                eprintln!("Skipping test (pyarrow not installed)");
                return;
            }

            let json = r#"[{"name": "John", "email": "j@x.com"}, {"name": "Jane", "email": "jane@y.com"}]"#;
            let df = json_to_arrow_df(py, json).unwrap();
            // Proof the frame is arrow, not polars.
            let ty: String = df.bind(py).get_type().name().unwrap().extract().unwrap();
            assert_eq!(ty, "Table", "json_to_arrow_df must return a pyarrow Table");

            let back = arrow_df_to_json(py, &df).unwrap();
            assert!(back.contains("John"));
            assert!(back.contains("Jane"));
        });
    }

    /// The load-bearing P4 guarantee: the columnar builder produces a `pa.Table`
    /// byte-identical (schema + data) to the JSON path for every column type the
    /// columnar path claims. If this holds, rerouting a table op from JSON to
    /// columns cannot change goldenmatch's output. Covers the two inference
    /// traps: all-NULL column (→ `null` type, not the column's declared type)
    /// and int/float width (→ `int64`/`double`, matching `row_to_json` →
    /// Python scalar → arrow).
    #[test]
    fn columnar_matches_json() {
        pyo3::prepare_freethreaded_python();
        Python::with_gil(|py| {
            if py.import("pyarrow").is_err() {
                eprintln!("Skipping test (pyarrow not installed)");
                return;
            }
            // Same table two ways: as row_to_json records, and as typed columns.
            let json = r#"[
                {"name":"Al","age":30,"score":1.5,"ok":true,"note":null},
                {"name":null,"age":null,"score":null,"ok":null,"note":null},
                {"name":"Bo","age":40,"score":2.0,"ok":false,"note":null}
            ]"#;
            let cols = TableColumns {
                names: vec![
                    "name".into(),
                    "age".into(),
                    "score".into(),
                    "ok".into(),
                    "note".into(),
                ],
                columns: vec![
                    ColumnData::Text(vec![Some("Al".into()), None, Some("Bo".into())]),
                    ColumnData::Int(vec![Some(30), None, Some(40)]),
                    ColumnData::Float(vec![Some(1.5), None, Some(2.0)]),
                    ColumnData::Bool(vec![Some(true), None, Some(false)]),
                    ColumnData::Null(3), // all-NULL column → pa.null type
                ],
            };

            let from_json = json_to_arrow_df(py, json).unwrap();
            let from_cols = columns_to_arrow_df(py, &cols).unwrap();

            // pa.Table.equals compares schema (types + names) AND data.
            let equal: bool = from_json
                .bind(py)
                .call_method1("equals", (from_cols.bind(py),))
                .unwrap()
                .extract()
                .unwrap();
            assert!(
                equal,
                "columnar pa.Table must equal the from_pylist(JSON) table (schema + data)"
            );
        });
    }
}
