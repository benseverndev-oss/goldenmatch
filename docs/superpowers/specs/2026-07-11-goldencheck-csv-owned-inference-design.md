# GoldenCheck CSV owned-inference wave — design (polars-free CSV, Arrow-native)

Date: 2026-07-11
Status: wave design (Arrow fused-scan program). Pending spec review + user approval.
Program: `2026-07-11-goldencheck-arrow-fused-scan-engine-program-design.md` (this is the "CSV" wave — an early, independently-valuable, additive step toward complete Polars eviction).
Base: fresh `origin/main` (has W0-land: arrow-in-core kernels + parity-oracle harness).

## Goal

Read CSV into typed columns **without Polars**, via goldencheck's OWN documented type inference — so a polars-absent install can scan CSVs (today it raises `ImportError`). Rust is the source of truth (arrow-native, WASM/SQL-able); a Python reference implements the same contract as the parity fallback. Lands **additively** (shadow discipline): polars-present behaviour is UNCHANGED; the owned path is used only where Polars is absent (previously an error), and becomes the default for everyone only at the program's Flip.

CSV inference cannot be byte-identical to `pl.read_csv` (that was the P4a wall). So this is an **owned contract** — deliberately different on documented cases (chiefly leading-zero numerics stay strings = zip-code-safe). Byte-identity is NOT a goal; a differential test documents the deltas vs Polars.

## The owned inference contract (the load-bearing product decision)

Per column, over ALL non-empty cell values (deterministic full-column scan, not a sampled `infer_schema_length`). Precedence **int -> float -> bool -> str**:

1. **null** — an empty string `""` is null in every column (matches `pl.read_csv` + `read_file`'s existing behaviour).
2. **int (Int64)** — every non-empty value matches `^-?[0-9]+$`, fits `i64`, **and is NOT a leading-zero multi-digit** (`^-?0[0-9]+$`, e.g. `"01234"`, `"-007"`). The leading-zero exclusion is the **deliberate zip-safe divergence** from Polars (Polars parses `"01234"` as int `1234`, losing the zero + mistyping a zip/ID column; goldencheck keeps it a string). Documented.
3. **float (Float64)** — not all-int, but every non-empty value parses as a finite decimal/scientific number (`^-?[0-9]*\.?[0-9]+([eE][-+]?[0-9]+)?$` style). **Exclude `inf`/`nan`/`infinity`** (case-insensitive) — they stay `str` (a column literally containing "nan" is far more likely a data value than a float NaN; avoids a surprise typing).
4. **bool (Boolean)** — every non-empty value in `{true, false}` case-insensitive (`true/True/TRUE/false/False/FALSE`). NOT `0/1` (those are int), NOT `yes/no/t/f/y/n`.
5. **str (Utf8)** — anything else (the default).

No **date** inference — dates stay `str` (matches the current `pl.read_csv(try_parse_dates=False)`; the temporal profiler parses `%Y-%m-%d` itself via `str_to_date`). This keeps the contract simple and avoids the date-parsing wall.

This contract is documented in the reader docstring + a `docs-site` note at the Flip. It is deliberately conservative (prefers `str` on ambiguity) — safer for a data-quality tool (a mistyped numeric column silently disables the string/format/pattern checks; keeping ambiguous columns as `str` runs MORE checks, not fewer).

## Architecture

### Rust kernel (`goldencheck-core`, source of truth)
- **`csv_infer_columns(csv_bytes, delimiter, ...) -> RecordBatch`** (or a columns struct). Two stages:
  1. **Owned schema inference** (this crate owns it): parse rows with the `csv` crate (RFC 4180 quoting / embedded newlines / configurable delimiter), scan each column's values, apply the contract above -> an Arrow `Schema` (Int64/Float64/Boolean/Utf8 per column).
  2. **Typed parse**: re-parse into Arrow arrays for the inferred schema (either via `arrow-csv`'s `Reader` given the inferred schema, or directly building arrays in the same pass — decide at plan time; `arrow-csv`-with-explicit-schema reuses robust parsing but re-reads; a single-pass build is faster but more code). Output an Arrow `RecordBatch` with null bitmaps.
- Add `csv` (+ maybe `arrow-csv`) to `goldencheck-core/Cargo.toml`. WASM-compatible (both pure Rust).
- Native shim: `PyArrowType` out (RecordBatch -> pyarrow Table) OR a plain dict; the reader adapts to `dict[str, list]` for the current `scan_columns` path.

### Python reference / fallback
- A pure-Python implementation of the SAME contract (stdlib `csv` + the inference rules), used as (a) the parity-oracle reference (Rust == Python on a corpus), and (b) the fallback when the native kernel isn't built. Registered as a component in the parity harness (W0-land's harness), **empty divergence registry** (Rust and Python implement the identical contract, so they must agree).

### Reader integration (additive / shadow)
- New `_read_csv_columns_owned(path) -> dict[str, list]` (Rust kernel when native present, Python reference otherwise).
- `read_columns` CSV branch: **when Polars is importable -> unchanged (`_read_csv_columns` / `pl.read_csv`)**; **when Polars is absent -> use `_read_csv_columns_owned`** (was `ImportError`). So polars-present output is identical to 2.0.0; polars-absent gains CSV scanning (owned contract). `read_file` (the full-scan Polars reader) is untouched. The default flips to owned-for-everyone only at the program Flip.
- The two install modes now differ on CSV typing (owned vs Polars) — documented; this is the pre-Flip shadow state.

## Testing
- **Rust** (`cargo test -p goldencheck-core`): the inference contract — int, leading-zero->str, float, inf/nan->str, bool, mixed->str, empty->null, quoted fields, embedded newlines, alt delimiter.
- **Python reference** unit tests: same contract cases.
- **Parity** (W0-land harness): Rust `csv_infer_columns` == Python reference on random + adversarial CSV fixtures; empty divergence registry.
- **Differential vs Polars** (documentation test, Polars present): run owned inference AND `pl.read_csv` on a corpus; assert + DOCUMENT the deltas (the leading-zero column is `str` owned vs `int` Polars; inf/nan `str` vs `float`; everything else matches). This test SNAPSHOTS the owned-vs-Polars divergence so it's explicit, not silent.
- **Integration**: `read_columns(csv)` with Polars absent (subprocess meta_path block) returns the owned-typed dict + `"polars" not in sys.modules`; with Polars present returns the unchanged `pl.read_csv` dict; `scan_columns(read_columns(csv))` runs the covered checks on the owned-typed CSV.
- Existing reader/scanner tests pass UNEDITED (polars-present path unchanged); `import goldencheck` loads zero polars.

## Byte-identity / risks
- **Owned != Polars by design** — the differential test documents every delta; no byte-identity claim.
- **`csv`-crate parsing vs Polars parsing** on exotic quoting/escaping could differ on malformed input — the contract targets well-formed RFC-4180 CSV; malformed-input behaviour is best-effort + documented, not matched to Polars.
- **Encoding** — `read_file`/`_read_csv_columns` have a latin-1 fallback; the owned reader should mirror it (try utf-8, fall back to latin-1) for parity of *which files parse*.
- **Rust `csv`+`arrow-csv` deps** add build weight to core; pure Rust so WASM-safe; pin compatible versions.
- **Shadow correctness** — the polars-present path must be byte-identical to 2.0.0 (it IS — unchanged `_read_csv_columns`); only the polars-absent path is new. Verified by existing tests passing unedited.

## Non-goals
- No date inference (dates stay str). No matching `pl.read_csv` (owned contract). No change to `read_file` or the polars-present CSV path. No wiring CSV into the full `scan_file` (that's the Flip; this feeds `scan_columns`/`scan_file_columns`). No configurable-inference-rules surface yet (the contract is fixed; a `schema=` override already exists at the scan layer for users who need it).
