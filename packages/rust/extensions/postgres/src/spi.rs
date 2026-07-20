//! SPI-based table reading for GoldenMatch.
//!
//! Uses Postgres Server Programming Interface to read table data
//! and convert it to JSON for the Python bridge.

use goldenmatch_bridge::convert::{ColumnData, TableColumns, TableData};
use pgrx::pg_sys::{BuiltinOid, PgOid};
use pgrx::prelude::*;

/// Read all rows from a table and return as a JSON array string.
///
/// Uses SPI to execute `SELECT * FROM <table>` and converts each row
/// to a JSON object. Column types are mapped to JSON types:
/// - TEXT/VARCHAR -> JSON string
/// - INT/BIGINT -> JSON number
/// - FLOAT/DOUBLE -> JSON number
/// - BOOL -> JSON boolean
/// - NULL -> JSON null
/// - Everything else -> JSON string via ::text cast
pub fn read_table_as_json(table_name: &str) -> Result<String, String> {
    // Validate table name (allow only alphanumeric, underscore, dot for schema.table)
    if !table_name
        .chars()
        .all(|c| c.is_alphanumeric() || c == '_' || c == '.')
    {
        return Err(format!("Invalid table name: {}", table_name));
    }

    let query = format!(
        "SELECT row_to_json(t)::text FROM (SELECT * FROM {}) t",
        table_name
    );

    Spi::connect(|client| {
        let result = client.select(&query, None, None);

        match result {
            Ok(table) => {
                let mut rows: Vec<String> = Vec::new();
                for row in table {
                    if let Ok(Some(json_str)) = row.get::<String>(1) {
                        rows.push(json_str);
                    }
                }
                Ok(format!("[{}]", rows.join(",")))
            }
            Err(e) => Err(format!("SPI error reading table '{}': {}", table_name, e)),
        }
    })
}

/// The reader-typed dispatch for a single column: the Rust `row.get::<T>()` type
/// and the arrow bucket it widens into. Only the types whose columnar build is
/// byte-identical to `row_to_json` → `from_pylist` are represented; anything
/// else aborts the columnar read and falls back to JSON (see [`read_table`]).
enum ColKind {
    Text,
    Int16,
    Int32,
    Int64,
    Float32,
    Float64,
    Bool,
}

/// Read a table for the Python bridge, preferring an **Arrow-native columnar
/// handoff** over the `row_to_json` JSON pass.
///
/// When every column is one of the parity-safe built-in types
/// (text/varchar/char, int2/4/8, float4/8, bool) and the table has ≥1 row, the
/// columns are read typed (NULL-aware) into [`TableColumns`] — the bridge then
/// assembles a `pa.Table` directly (`convert::columns_to_arrow_df`), skipping
/// both Postgres' per-row `row_to_json` and Python's `from_pylist` row→columnar
/// transpose. Any other column type (numeric/date/json/array/domain/…) or a
/// 0-row table falls back to [`read_table_as_json`], which is byte-identical to
/// the pre-P4 behavior. Either way the resulting `pa.Table` is the same
/// (proven in `convert::tests::columnar_matches_json`), so callers are
/// parity-safe regardless of the branch taken.
pub fn read_table(table_name: &str) -> Result<TableData, String> {
    match try_read_columns(table_name)? {
        Some(cols) => Ok(TableData::Columns(cols)),
        None => Ok(TableData::Json(read_table_as_json(table_name)?)),
    }
}

/// Try the columnar read; `Ok(None)` signals "fall back to JSON" (an unsupported
/// column type or a 0-row table), `Err` is a real SPI failure.
fn try_read_columns(table_name: &str) -> Result<Option<TableColumns>, String> {
    if !table_name
        .chars()
        .all(|c| c.is_alphanumeric() || c == '_' || c == '.')
    {
        return Err(format!("Invalid table name: {}", table_name));
    }
    let query = format!("SELECT * FROM {}", table_name);

    Spi::connect(|client| {
        let table = match client.select(&query, None, None) {
            Ok(t) => t,
            Err(e) => return Err(format!("SPI error reading table '{}': {}", table_name, e)),
        };

        // 1. Introspect column names + types. Bail to JSON on any unsupported type.
        let ncols = match table.columns() {
            Ok(n) => n,
            Err(e) => return Err(format!("SPI columns() on '{}': {}", table_name, e)),
        };
        if ncols == 0 {
            return Ok(None);
        }
        let mut names: Vec<String> = Vec::with_capacity(ncols);
        let mut kinds: Vec<ColKind> = Vec::with_capacity(ncols);
        for i in 1..=ncols {
            let oid = table
                .column_type_oid(i)
                .map_err(|e| format!("SPI column_type_oid({}) on '{}': {}", i, table_name, e))?;
            let kind = match oid {
                PgOid::BuiltIn(BuiltinOid::TEXTOID)
                | PgOid::BuiltIn(BuiltinOid::VARCHAROID)
                | PgOid::BuiltIn(BuiltinOid::BPCHAROID) => ColKind::Text,
                PgOid::BuiltIn(BuiltinOid::INT2OID) => ColKind::Int16,
                PgOid::BuiltIn(BuiltinOid::INT4OID) => ColKind::Int32,
                PgOid::BuiltIn(BuiltinOid::INT8OID) => ColKind::Int64,
                PgOid::BuiltIn(BuiltinOid::FLOAT4OID) => ColKind::Float32,
                PgOid::BuiltIn(BuiltinOid::FLOAT8OID) => ColKind::Float64,
                PgOid::BuiltIn(BuiltinOid::BOOLOID) => ColKind::Bool,
                // numeric/date/timestamp/json/uuid/array/domain/enum/… → row_to_json
                // formats these in ways the typed columnar path can't reproduce
                // byte-for-byte, so fall back to JSON for the whole table.
                _ => return Ok(None),
            };
            let name = table
                .column_name(i)
                .map_err(|e| format!("SPI column_name({}) on '{}': {}", i, table_name, e))?;
            names.push(name);
            kinds.push(kind);
        }

        // 2. Read rows into per-column typed accumulators (NULL → None). Widen
        //    int/float to i64/f64 to match row_to_json → Python int/float → arrow.
        enum Acc {
            Text(Vec<Option<String>>),
            Int(Vec<Option<i64>>),
            Float(Vec<Option<f64>>),
            Bool(Vec<Option<bool>>),
        }
        let mut accs: Vec<Acc> = kinds
            .iter()
            .map(|k| match k {
                ColKind::Text => Acc::Text(Vec::new()),
                ColKind::Int16 | ColKind::Int32 | ColKind::Int64 => Acc::Int(Vec::new()),
                ColKind::Float32 | ColKind::Float64 => Acc::Float(Vec::new()),
                ColKind::Bool => Acc::Bool(Vec::new()),
            })
            .collect();

        let mut nrows: usize = 0;
        for row in table {
            nrows += 1;
            for (idx, kind) in kinds.iter().enumerate() {
                let ord = idx + 1;
                match (kind, &mut accs[idx]) {
                    (ColKind::Text, Acc::Text(v)) => v.push(row.get::<String>(ord).ok().flatten()),
                    (ColKind::Int16, Acc::Int(v)) => {
                        v.push(row.get::<i16>(ord).ok().flatten().map(|x| x as i64))
                    }
                    (ColKind::Int32, Acc::Int(v)) => {
                        v.push(row.get::<i32>(ord).ok().flatten().map(|x| x as i64))
                    }
                    (ColKind::Int64, Acc::Int(v)) => v.push(row.get::<i64>(ord).ok().flatten()),
                    (ColKind::Float32, Acc::Float(v)) => {
                        v.push(row.get::<f32>(ord).ok().flatten().map(|x| x as f64))
                    }
                    (ColKind::Float64, Acc::Float(v)) => v.push(row.get::<f64>(ord).ok().flatten()),
                    (ColKind::Bool, Acc::Bool(v)) => v.push(row.get::<bool>(ord).ok().flatten()),
                    _ => unreachable!("acc kind mismatch"),
                }
            }
        }

        // 0-row table: `from_pylist([])` yields a 0-column table, which the
        // typed columnar path can't reproduce — fall back to JSON to stay
        // byte-identical on empty tables.
        if nrows == 0 {
            return Ok(None);
        }

        // 3. Finalize: an all-NULL column → pyarrow `null` type, matching
        //    `from_pylist`'s inference.
        let columns: Vec<ColumnData> = accs
            .into_iter()
            .map(|acc| match acc {
                Acc::Text(v) => {
                    if v.iter().all(Option::is_none) {
                        ColumnData::Null(v.len())
                    } else {
                        ColumnData::Text(v)
                    }
                }
                Acc::Int(v) => {
                    if v.iter().all(Option::is_none) {
                        ColumnData::Null(v.len())
                    } else {
                        ColumnData::Int(v)
                    }
                }
                Acc::Float(v) => {
                    if v.iter().all(Option::is_none) {
                        ColumnData::Null(v.len())
                    } else {
                        ColumnData::Float(v)
                    }
                }
                Acc::Bool(v) => {
                    if v.iter().all(Option::is_none) {
                        ColumnData::Null(v.len())
                    } else {
                        ColumnData::Bool(v)
                    }
                }
            })
            .collect();

        Ok(Some(TableColumns { names, columns }))
    })
}
