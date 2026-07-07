//! Native CSV transform — Phase 2 of the Polars eviction. The whole
//! file->transform->file pipeline in ONE Rust call: read the CSV into Rust-owned
//! Arrow string columns, apply the owned fused chain to the configured columns,
//! write the CSV back — **no `pl.DataFrame`, no Polars, no pyarrow, one FFI
//! crossing.** This is the shape where native BEATS Polars: there is no rival
//! frame to tie against (1b/1c only reached parity because the caller still owned
//! a `pl.DataFrame`).
//!
//! Semantics (documented, opt-in only via `GOLDENFLOW_ENGINE=columnar`): every
//! column is read as a string (no type inference); an empty field maps to null
//! (matches Polars' default null-on-empty). Strings-in/strings-out is what the
//! owned transforms want, and it avoids Polars' lossy float reformatting.

use arrow::array::{Array, LargeStringArray, LargeStringBuilder};
use pyo3::exceptions::{PyIOError, PyValueError};
use pyo3::prelude::*;

use goldenflow_core::chain::{apply_chain, Kernel};

/// One per-op audit record: `(op, affected_rows, total_rows, sample_before,
/// sample_after)`. Samples are the null-preserving first 3 values, mirroring the
/// Python columnar engine's `series.head(3).cast(Utf8).to_list()`.
type OpRecord = (String, u64, u64, Vec<Option<String>>, Vec<Option<String>>);
/// Manifest for one transformed column: `(column, [OpRecord])`.
type ColumnManifest = (String, Vec<OpRecord>);

/// Transform spec: which ops (each `(name, params)`) to apply to which column.
type ColumnSpec = (String, Vec<(String, Vec<String>)>);

fn sample3(arr: &LargeStringArray) -> Vec<Option<String>> {
    let n = arr.len().min(3);
    (0..n)
        .map(|i| {
            if arr.is_null(i) {
                None
            } else {
                Some(arr.value(i).to_string())
            }
        })
        .collect()
}

/// Read a CSV file into `(column_names, columns)` — every field a string, empty
/// -> null. Column order follows the header.
fn read_csv(path: &str) -> Result<(Vec<String>, Vec<LargeStringArray>), String> {
    let mut reader = csv::ReaderBuilder::new()
        .has_headers(true)
        .from_path(path)
        .map_err(|e| format!("open {path}: {e}"))?;
    let names: Vec<String> = reader
        .headers()
        .map_err(|e| format!("read header: {e}"))?
        .iter()
        .map(str::to_string)
        .collect();
    let ncols = names.len();
    // Build each column's Arrow buffer directly — no per-field String, no
    // intermediate Vec, and a single reused StringRecord (avoids a per-row alloc).
    let mut builders: Vec<LargeStringBuilder> =
        (0..ncols).map(|_| LargeStringBuilder::new()).collect();
    let mut rec = csv::StringRecord::new();
    while reader
        .read_record(&mut rec)
        .map_err(|e| format!("read row: {e}"))?
    {
        for (i, builder) in builders.iter_mut().enumerate() {
            let field = rec.get(i).unwrap_or("");
            if field.is_empty() {
                builder.append_null(); // empty field -> null (matches Polars)
            } else {
                builder.append_value(field);
            }
        }
    }
    let arrays = builders.iter_mut().map(|b| b.finish()).collect();
    Ok((names, arrays))
}

/// Write `(column_names, columns)` back to a CSV file — null -> empty field,
/// RFC4180 quoting via the `csv` crate.
fn write_csv(path: &str, names: &[String], columns: &[LargeStringArray]) -> Result<(), String> {
    let mut writer = csv::WriterBuilder::new()
        .from_path(path)
        .map_err(|e| format!("create {path}: {e}"))?;
    writer
        .write_record(names)
        .map_err(|e| format!("write header: {e}"))?;
    let nrows = columns.first().map_or(0, |c| c.len());
    for row in 0..nrows {
        // Field-by-field (no per-row Vec alloc); empty terminator ends the record.
        for c in columns {
            let field = if c.is_null(row) { "" } else { c.value(row) };
            writer
                .write_field(field)
                .map_err(|e| format!("write row {row}: {e}"))?;
        }
        writer
            .write_record(std::iter::empty::<&[u8]>())
            .map_err(|e| format!("end row {row}: {e}"))?;
    }
    writer.flush().map_err(|e| format!("flush {path}: {e}"))?;
    Ok(())
}

/// Read `in_path`, apply each spec's owned string chain (op-by-op, so the manifest
/// carries per-op affected counts + before/after samples exactly like the Python
/// columnar engine), write to `out_path`, and return the per-column manifest.
/// Polars-free, pyarrow-free, one FFI crossing.
#[pyfunction]
pub fn transform_csv(
    py: Python,
    in_path: &str,
    out_path: &str,
    specs: Vec<ColumnSpec>,
) -> PyResult<Vec<ColumnManifest>> {
    py.detach(|| -> PyResult<Vec<ColumnManifest>> {
        let (names, mut columns) =
            read_csv(in_path).map_err(|e| PyIOError::new_err(format!("read CSV: {e}")))?;
        let idx_of = |name: &str| names.iter().position(|n| n == name);

        let mut manifest: Vec<ColumnManifest> = Vec::with_capacity(specs.len());
        for (col_name, ops) in &specs {
            let Some(idx) = idx_of(col_name) else {
                continue; // column not in file — mirrors the Python `if col in df.columns`
            };
            let total = columns[idx].len() as u64;
            let mut cur = columns[idx].clone();
            let mut records: Vec<OpRecord> = Vec::with_capacity(ops.len());
            for (op, params) in ops {
                let refs: Vec<&str> = params.iter().map(String::as_str).collect();
                let kernel = Kernel::from_op(op, &refs).ok_or_else(|| {
                    PyValueError::new_err(format!("not a fusable chain kernel: {op}"))
                })?;
                let before = sample3(&cur);
                let res = apply_chain(&cur, &[kernel]);
                let after = sample3(&res.array);
                records.push((op.clone(), res.changed[0], total, before, after));
                cur = res.array;
            }
            columns[idx] = cur;
            manifest.push((col_name.clone(), records));
        }

        write_csv(out_path, &names, &columns)
            .map_err(|e| PyIOError::new_err(format!("write CSV: {e}")))?;
        Ok(manifest)
    })
}
