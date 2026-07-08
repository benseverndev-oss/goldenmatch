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
use arrow::compute::concat;
use pyo3::exceptions::PyIOError;
use pyo3::prelude::*;

use goldenflow_core::chain::{apply_chain, apply_chain_nullable, ChainResult};

use crate::chain::{resolve_chain, ChainOps};

/// Below this data-region size the CSV is parsed sequentially (parallel setup +
/// concat isn't worth it). Override with `GOLDENFLOW_NATIVE_CSV_PARALLEL_MIN_BYTES`
/// (`0` = always parallel, huge = always sequential).
const DEFAULT_PARALLEL_MIN_BYTES: usize = 512 * 1024;

/// One per-op audit record: `(op, affected_rows, total_rows, sample_before,
/// sample_after)`. Samples are the null-preserving first 3 values, mirroring the
/// Python columnar engine's `series.head(3).cast(Utf8).to_list()`.
pub type OpRecord = (String, u64, u64, Vec<Option<String>>, Vec<Option<String>>);
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

/// Parse one **headerless** byte region (must start at a record boundary and end
/// right after one) into `ncols` Arrow string columns — empty field -> null.
fn parse_region(region: &[u8], ncols: usize) -> Result<Vec<LargeStringArray>, String> {
    let mut reader = csv::ReaderBuilder::new()
        .has_headers(false)
        .from_reader(region);
    let mut builders: Vec<LargeStringBuilder> =
        (0..ncols).map(|_| LargeStringBuilder::new()).collect();
    let mut rec = csv::ByteRecord::new();
    while reader
        .read_byte_record(&mut rec)
        .map_err(|e| format!("read row: {e}"))?
    {
        for (i, builder) in builders.iter_mut().enumerate() {
            let field = rec.get(i).unwrap_or(b"");
            if field.is_empty() {
                builder.append_null(); // empty field -> null (matches Polars)
            } else {
                // Regions are cut only at record boundaries, so fields are whole;
                // CSV is UTF-8 by contract here (utf8-lossy is a Polars read detail).
                let s = std::str::from_utf8(field)
                    .map_err(|e| format!("invalid utf-8 in field: {e}"))?;
                builder.append_value(s);
            }
        }
    }
    Ok(builders.iter_mut().map(|b| b.finish()).collect())
}

/// Find the byte offset just past the first record-boundary newline (even
/// quote-count) — i.e. the end of the header line and the start of the data.
/// Returns `data.len()` if there is no newline (header-only file).
fn first_data_offset(data: &[u8]) -> usize {
    let mut in_quote = false;
    for (i, &b) in data.iter().enumerate() {
        match b {
            b'"' => in_quote = !in_quote,
            b'\n' if !in_quote => return i + 1,
            _ => {}
        }
    }
    data.len()
}

/// Split the data region into up to `k` contiguous `[start, end)` ranges, cutting
/// ONLY at record-boundary newlines (a `\n` seen with an even running quote count —
/// the RFC4180 invariant that a newline outside a quoted field ends a record).
/// So every range holds whole records and parses identically to the sequential
/// path. Returns `(ranges, boundary_rows)` where `boundary_rows` counts record
/// boundaries seen (a cheap safety check against a mis-split).
fn record_ranges(data: &[u8], k: usize) -> (Vec<(usize, usize)>, usize) {
    let n = data.len();
    let target = (n / k).max(1);
    let mut starts = vec![0usize];
    let mut next_target = target;
    let mut in_quote = false;
    let mut boundary_rows = 0usize;
    for (i, &b) in data.iter().enumerate() {
        match b {
            b'"' => in_quote = !in_quote,
            b'\n' if !in_quote => {
                boundary_rows += 1;
                if i + 1 < n && i + 1 >= next_target && starts.len() < k {
                    starts.push(i + 1);
                    next_target += target;
                }
            }
            _ => {}
        }
    }
    // A trailing record without a final newline still counts as a row.
    if n > 0 && data[n - 1] != b'\n' {
        boundary_rows += 1;
    }
    let ranges = starts
        .iter()
        .enumerate()
        .map(|(j, &s)| (s, *starts.get(j + 1).unwrap_or(&n)))
        .collect();
    (ranges, boundary_rows)
}

fn parallel_min_bytes() -> usize {
    std::env::var("GOLDENFLOW_NATIVE_CSV_PARALLEL_MIN_BYTES")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(DEFAULT_PARALLEL_MIN_BYTES)
}

/// Read a CSV file into `(column_names, columns)` — every field a string, empty
/// -> null. Column order follows the header. Large files are parsed in parallel
/// across record-boundary-aligned chunks (`std::thread::scope`, no rayon global
/// pool — sidesteps the #688 `LockLatch` class); small files stay sequential.
/// Any chunk error or a row-count mismatch falls back to a sequential re-parse,
/// so the parallel split can never silently corrupt output.
fn read_csv(path: &str) -> Result<(Vec<String>, Vec<LargeStringArray>), String> {
    let bytes = std::fs::read(path).map_err(|e| format!("open {path}: {e}"))?;
    let data_start = first_data_offset(&bytes);
    let header = &bytes[..data_start];
    let names: Vec<String> = csv::ReaderBuilder::new()
        .has_headers(false)
        .from_reader(header)
        .headers()
        .map_err(|e| format!("read header: {e}"))?
        .iter()
        .map(str::to_string)
        .collect();
    let ncols = names.len();
    let data = &bytes[data_start..];

    let nthreads = std::thread::available_parallelism().map_or(1, |n| n.get());
    if ncols == 0 || nthreads <= 1 || data.len() < parallel_min_bytes() {
        return Ok((names, parse_region(data, ncols)?));
    }

    let (ranges, expected_rows) = record_ranges(data, nthreads);
    let parallel = std::thread::scope(|scope| -> Result<Vec<Vec<LargeStringArray>>, String> {
        let handles: Vec<_> = ranges
            .iter()
            .map(|&(s, e)| scope.spawn(move || parse_region(&data[s..e], ncols)))
            .collect();
        handles
            .into_iter()
            .map(|h| {
                h.join()
                    .map_err(|_| "csv parse thread panicked".to_string())?
            })
            .collect()
    });

    // Fall back to sequential on any chunk error or a row-count mismatch (a
    // mis-split would change the total row count) — never corrupt silently.
    let partials = match parallel {
        Ok(p) if p.iter().map(|c| c[0].len()).sum::<usize>() == expected_rows => p,
        _ => return Ok((names, parse_region(data, ncols)?)),
    };

    let columns = (0..ncols)
        .map(|i| {
            let refs: Vec<&dyn Array> = partials.iter().map(|c| &c[i] as &dyn Array).collect();
            let merged = concat(&refs).map_err(|e| format!("concat column {i}: {e}"))?;
            merged
                .as_any()
                .downcast_ref::<LargeStringArray>()
                .cloned()
                .ok_or_else(|| "concat produced a non-LargeUtf8 column".to_string())
        })
        .collect::<Result<Vec<_>, String>>()?;
    Ok((names, columns))
}

/// Append one CSV field to `out`, matching Polars' `write_csv` quoting EXACTLY:
/// a null cell is an empty field (no quotes); a NON-null cell is quoted iff it is
/// the empty string (so `""` round-trips as `""`, distinct from a null empty field)
/// OR it contains a delimiter / quote / newline, with `"` escaped as `""`.
fn write_csv_field(out: &mut Vec<u8>, field: Option<&str>) {
    let Some(s) = field else { return }; // null -> empty field
    let needs_quote = s.is_empty() || s.bytes().any(|b| matches!(b, b',' | b'"' | b'\n' | b'\r'));
    if needs_quote {
        out.push(b'"');
        for b in s.bytes() {
            if b == b'"' {
                out.push(b'"');
            }
            out.push(b);
        }
        out.push(b'"');
    } else {
        out.extend_from_slice(s.as_bytes());
    }
}

/// Serialize rows `[start, end)` of `columns` to an in-memory CSV byte buffer (no
/// header) — Polars-matching quoting, `\n` terminator.
fn write_region(columns: &[LargeStringArray], start: usize, end: usize) -> Result<Vec<u8>, String> {
    let mut out = Vec::new();
    for row in start..end {
        for (i, c) in columns.iter().enumerate() {
            if i > 0 {
                out.push(b',');
            }
            write_csv_field(&mut out, (!c.is_null(row)).then(|| c.value(row)));
        }
        out.push(b'\n');
    }
    Ok(out)
}

/// Write `(column_names, columns)` back to a CSV file — null -> empty field,
/// RFC4180 quoting. Large outputs format their row ranges in parallel
/// (`std::thread::scope`, no rayon global pool) into per-chunk byte buffers, then
/// concatenate them in order — write order is deterministic. Small outputs write
/// sequentially.
fn write_csv(path: &str, names: &[String], columns: &[LargeStringArray]) -> Result<(), String> {
    use std::io::Write;
    let nrows = columns.first().map_or(0, |c| c.len());
    let value_bytes: usize = columns.iter().map(|c| c.values().len()).sum();

    // Header (same quoting rules as the data).
    let mut header = Vec::new();
    for (i, n) in names.iter().enumerate() {
        if i > 0 {
            header.push(b',');
        }
        write_csv_field(&mut header, Some(n));
    }
    header.push(b'\n');

    let nthreads = std::thread::available_parallelism().map_or(1, |n| n.get());
    let bufs: Vec<Vec<u8>> = if nthreads <= 1 || value_bytes < parallel_min_bytes() {
        vec![write_region(columns, 0, nrows)?]
    } else {
        let per = nrows.div_ceil(nthreads);
        let ranges: Vec<(usize, usize)> = (0..nrows)
            .step_by(per.max(1))
            .map(|s| (s, (s + per).min(nrows)))
            .collect();
        std::thread::scope(|scope| -> Result<Vec<Vec<u8>>, String> {
            let handles: Vec<_> = ranges
                .iter()
                .map(|&(s, e)| scope.spawn(move || write_region(columns, s, e)))
                .collect();
            handles
                .into_iter()
                .map(|h| {
                    h.join()
                        .map_err(|_| "csv write thread panicked".to_string())?
                })
                .collect()
        })?
    };

    let mut file = std::io::BufWriter::new(
        std::fs::File::create(path).map_err(|e| format!("create {path}: {e}"))?,
    );
    file.write_all(&header)
        .map_err(|e| format!("write header: {e}"))?;
    for buf in &bufs {
        file.write_all(buf)
            .map_err(|e| format!("write {path}: {e}"))?;
    }
    file.flush().map_err(|e| format!("flush {path}: {e}"))?;
    Ok(())
}

/// Run a resolved chain over `col` and build the per-op manifest: ONE fused pass
/// (`run_all` → the final array + per-op `changed` counts) plus a cheap 3-row
/// replay (`run_one` applies just kernel `i`) for the before/after samples —
/// byte-identical to running each op over the full column, at a fraction of the
/// cost (N ops ⇒ 1 full pass, not N). Generic over the chain kind (total /
/// nullable), which both return the same `ChainResult<i64>`.
fn build_manifest<FAll, FOne>(
    col: &LargeStringArray,
    ops: &[(String, Vec<String>)],
    total: u64,
    run_all: FAll,
    run_one: FOne,
) -> (LargeStringArray, Vec<OpRecord>)
where
    FAll: FnOnce(&LargeStringArray) -> ChainResult<i64>,
    FOne: Fn(&LargeStringArray, usize) -> ChainResult<i64>,
{
    let fused = run_all(col);
    let mut head = LargeStringArray::from_iter(sample3(col));
    let mut records: Vec<OpRecord> = Vec::with_capacity(ops.len());
    for (i, (op, _)) in ops.iter().enumerate() {
        let before = sample3(&head);
        let replay = run_one(&head, i);
        let after = sample3(&replay.array);
        records.push((op.clone(), fused.changed[i], total, before, after));
        head = replay.array;
    }
    (fused.array, records)
}

/// Read `in_path`, apply each spec's owned string chain (auto-routed total /
/// nullable, so the manifest carries per-op affected counts + before/after samples
/// exactly like the Python columnar engine), write to `out_path`, and return the
/// per-column manifest. Polars-free, pyarrow-free, one FFI crossing.
#[pyfunction]
pub fn transform_csv(
    py: Python,
    in_path: &str,
    out_path: &str,
    specs: Vec<ColumnSpec>,
) -> PyResult<Vec<ColumnManifest>> {
    py.detach(|| -> PyResult<Vec<ColumnManifest>> {
        let (mut names, mut columns) =
            read_csv(in_path).map_err(|e| PyIOError::new_err(format!("read CSV: {e}")))?;

        let mut manifest: Vec<ColumnManifest> = Vec::with_capacity(specs.len());
        // Split outputs (fixed-name columns to add) accumulate here and are appended
        // AFTER all specs, so they land after the original columns (as Polars does).
        let mut extra: Vec<(String, LargeStringArray)> = Vec::new();
        for (col_name, ops) in &specs {
            let Some(idx) = names.iter().position(|n| n == col_name) else {
                continue; // column not in file — mirrors the Python `if col in df.columns`
            };
            let total = columns[idx].len() as u64;
            // Split shape (`string* splitter`) transforms the source in place and ADDS
            // the fixed-name output columns (source itself unchanged by the split).
            if let Some(plan) = crate::split_columnar::resolve_split(ops) {
                let (src, new_cols, records) =
                    crate::split_columnar::run_split_column(&columns[idx], &plan);
                columns[idx] = src;
                extra.extend(new_cols);
                manifest.push((col_name.clone(), records));
                continue;
            }
            // Numeric shape (`string* parser f64*`) runs the string->f64->string
            // path (formatting via the Polars-matching float formatter); otherwise
            // auto-route the string run total vs nullable.
            if let Some(plan) = crate::numeric_columnar::resolve_numeric(ops) {
                let (numcol, records) =
                    crate::numeric_columnar::run_numeric_column(&columns[idx], &plan);
                // CSV output is text: format the numeric result (f64 via the
                // Polars-matching formatter, i64 as a plain integer).
                columns[idx] = LargeStringArray::from_iter(numcol.fmt());
                manifest.push((col_name.clone(), records));
                continue;
            }
            // Auto-route the run: all-total takes the zero-alloc fast chain; any
            // Option-returning kernel takes the nullable chain. Both return the same
            // ChainResult, so the fused-pass + 3-row-replay manifest logic is shared.
            let (new_array, records) = match resolve_chain(ops)? {
                ChainOps::Total(ks) => build_manifest(
                    &columns[idx],
                    ops,
                    total,
                    |c| apply_chain(c, &ks),
                    |c, i| apply_chain(c, std::slice::from_ref(&ks[i])),
                ),
                ChainOps::Nullable(ks) => build_manifest(
                    &columns[idx],
                    ops,
                    total,
                    |c| apply_chain_nullable(c, &ks),
                    |c, i| apply_chain_nullable(c, std::slice::from_ref(&ks[i])),
                ),
            };
            columns[idx] = new_array;
            manifest.push((col_name.clone(), records));
        }

        // Append the split output columns (or replace an existing same-name column,
        // matching Polars' `with_columns` semantics).
        for (name, arr) in extra {
            if let Some(pos) = names.iter().position(|n| *n == name) {
                columns[pos] = arr;
            } else {
                names.push(name);
                columns.push(arr);
            }
        }

        write_csv(out_path, &names, &columns)
            .map_err(|e| PyIOError::new_err(format!("write CSV: {e}")))?;
        Ok(manifest)
    })
}
