# GoldenCheck Polars eviction — P4a (polars-free `read_columns` readers feeding `scan_columns`)

Date: 2026-07-11
Status: design (autonomous — /goal "remaining work is complete"; user chose Full P4 breaking flip). Self-reviewed via spec-document-reviewer.
Base: fresh `origin/main` (has S2.0–S2.3: `_polars_lazy`, Frame seam, `PyColumn`, `scan_columns`, native regex/date kernels).
Parent: goldencheck Polars-eviction. This is **P4a** — the additive, NON-breaking half of P4: polars-free file readers so a polars-absent install can scan Parquet/Excel via `scan_columns`. **P4b** (separate PR) does the breaking deps-flip (`polars` → `[polars]` extra, major 2.0.0, nopolars-required CI lane).

## Context

`import goldencheck` already survives polars-absent (the S2 lazy-import proxy). The polars-free SCAN path is `scan_columns(dict[str, list]) -> list[Finding]` (S2.1–S2.3: mechanical 3 + native-gated regex 3 + temporal). What's missing for a real polars-free install is a way to READ a file into `dict[str, list]` without Polars. This is that reader.

**The CSV wall (verified, from the shipped goldenflow precedent):** Polars' CSV dtype inference (numeric/date/bool sniffing) is not reproducible in stdlib, and goldencheck's profilers branch on `col.dtype` to decide which checks run — so a text-only CSV reader would silently change scan output. Therefore, per the program's "byte-identical or decline" philosophy, **CSV reading keeps requiring Polars** (clean decline when absent). Parquet and Excel CAN go polars-free byte-identically (typed schema readers).

## Scope

### In scope
1. **`read_columns(path) -> dict[str, list]`** in `engine/reader.py` — a polars-free typed read into columns:
   - **`.parquet`** → `pyarrow.parquet.read_table(str(path)).to_pydict()` (typed, value-identical to `pl.read_parquet`). Needs `pyarrow`; a clear `ImportError` → `pip install goldencheck[parquet]` when absent.
   - **`.xlsx` / `.xls`** → `openpyxl` typed-cell read into columns (openpyxl is already a base dep). Mirrors `read_file`'s `pl.read_excel(engine="openpyxl")` value-wise.
   - **`.csv`** → requires Polars (dtype inference not reproducible): when polars is importable, `pl.read_csv(path, infer_schema_length=10000).to_dict(as_series=False)` (byte-identical to `read_file`'s CSV path, then to columns); when polars is absent, a clear `ImportError` → `pip install goldencheck[polars]` (or use `read_file`/`scan_file`). Same latin-1 fallback as `read_file`.
   - Same path normalization + unsupported-ext / not-found / empty-file guards as `read_file`.
2. **`scan_file_columns(path) -> list[Finding]`** — a thin convenience = `scan_columns(read_columns(path))`; exported from `goldencheck/__init__.py`. The polars-free file-scan entry (Parquet/Excel without Polars; CSV needs `[polars]`).
3. **Byte-parity gate:** for a Parquet + an XLSX fixture, `scan_columns(read_columns(f))` equals the **covered-profiler subset** of the full `scan_file(f)` output — proving the polars-free read+scan matches the Polars path for the checks `scan_columns` runs.
4. **`[parquet]` extra** in `pyproject.toml` = `["pyarrow>=14"]` (same pin as `[native]`). (The dep-FLIP of `polars` itself is P4b, NOT here — `read_columns` is additive; `read_file` is unchanged.)

### Explicitly NOT in scope (P4b or later)
Flipping `polars` to an extra / the major version bump / the nopolars-required CI lane / clean-decline wrapping of `scan_dataframe`/`scan_file`/CLI (all P4b). Changing `read_file` (stays Polars, unchanged). A text-mode CSV reader (declined — would change scan output). JSON support (`read_file` doesn't support it either). Making the FULL scan (`scan_dataframe` → `DatasetProfile`, classification, denial, sampling, the R4-declined relation profilers) polars-free — that path is inherently Polars and stays `[polars]`-required.

### Success criteria
- `read_columns` reads Parquet (pyarrow) + Excel (openpyxl) into `dict[str, list]` with **zero Polars import** (verified by a subprocess with polars blocked); CSV cleanly errors when polars absent, works when present.
- `scan_columns(read_columns(parquet_or_xlsx))` is byte-identical to the covered-profiler subset of `scan_file(...)`.
- `read_file` + `scan_file` + the full scan path are UNCHANGED; existing tests pass unedited; `import goldencheck` loads zero Polars.

## Design

### `read_columns` (`engine/reader.py`)
```python
def read_columns(path: Path) -> dict[str, list]:
    """Polars-free typed read into columns for scan_columns(). Parquet (pyarrow)
    and Excel (openpyxl) read without Polars; CSV needs Polars (dtype inference is
    not reproducible) and raises a clear ImportError when it's absent. Returns
    {column_name: [values...]} with native Python scalars (int/float/str/date/
    datetime/bool/None) matching the corresponding pl.read_* values."""
    path = Path(path).resolve()
    ext = path.suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Unsupported file format: {ext}. Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}")
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path.name}")
    logger.info("Reading %s (%s) [columns]", path.name, ext)
    if path.stat().st_size == 0:
        raise ValueError("File has no data rows. Nothing to profile.")

    if ext == ".parquet":
        return _read_parquet_columns(path)
    if ext in (".xlsx", ".xls"):
        return _read_excel_columns(path)
    if ext == ".csv":
        return _read_csv_columns(path)
    raise ValueError(f"Unsupported file format: {ext}")
```
- **`_read_parquet_columns`**: `import pyarrow.parquet as pq` inside the function (clear `ImportError` → `goldencheck[parquet]`); `return pq.read_table(str(path)).to_pydict()`. `to_pydict()` yields native Python scalars per the typed schema — value-identical to `pl.read_parquet(path).to_dict(as_series=False)` for the scalar types goldencheck scans.
- **`_read_excel_columns`**: `from openpyxl import load_workbook`; read the active sheet, first row = header, remaining rows → per-column lists of typed cell values (openpyxl returns `int`/`float`/`datetime`/`str`/`None`); empty cell → `None`. Match `read_file`'s single-sheet behavior. (Openpyxl is a base dep, so this path is already available.)
- **`_read_csv_columns`**: defer to Polars (the only byte-identical option). Guard: `try: import polars` — if unimportable, raise `ImportError("Reading CSV requires Polars (its dtype inference is not reproducible without it). Install goldencheck[polars], or use a Parquet/Excel source.")`. When present: `pl.read_csv(path, infer_schema_length=10000)` (+ latin-1 fallback exactly like `read_file`), then `.to_dict(as_series=False)`.

### `scan_file_columns` (`engine/scanner.py`, exported)
```python
def scan_file_columns(path: Path) -> list[Finding]:
    """Polars-free file scan: read a file into columns (Parquet/Excel without Polars;
    CSV needs Polars) and run the covered structural checks via scan_columns(). For the
    full scan (classification, sampling, denial, the Polars-only relation checks) use
    scan_file(), which requires Polars."""
    return scan_columns(read_columns(path))
```
Export `scan_file_columns` + `read_columns` from `goldencheck/__init__.py` `__all__`.

## Byte-identity anchors / risks
- **Parquet `to_pydict()` vs `pl.read_parquet`** — both read the typed Parquet schema; scalar types (int/float/str/bool/date/datetime/None) round-trip identically. The parity test uses a fixture exercising these. Risk edges (Decimal, nested/list columns, categorical) are out of goldencheck's scanned scalar types — if a fixture surfaces one, narrow the fixture (document), don't loosen.
- **Excel openpyxl typing** — `read_file` reads Excel via `pl.read_excel(engine="openpyxl")`, i.e. openpyxl underneath; reading openpyxl cells directly should match value-wise, but Polars may re-infer a column's dtype from the openpyxl values (e.g. all-int → Int64). `PyColumn.dtype` infers from the Python values the same way (first non-null → int/float/str/…), so the covered-profiler dtype gate aligns. The parity test (scan_columns(read_columns(xlsx)) == covered subset of scan_file(xlsx)) is the guard; if a divergence appears (e.g. a mixed int/float column Polars floats but openpyxl keeps int), document + narrow, don't loosen.
- **CSV byte-identity** — `_read_csv_columns` uses the SAME `pl.read_csv(infer_schema_length=10000)` as `read_file`, so it's identical by construction; it just needs Polars (the accepted wall).
- **Zero-Polars for Parquet/Excel** — verified by a subprocess that blocks `polars` via a `sys.meta_path` finder and asserts `read_columns(parquet)` / `read_columns(xlsx)` succeed with `"polars" not in sys.modules` (the P4 payoff, provable now).
- **`read_file` unchanged** — the full Polars scan path is untouched; `read_columns` is purely additive.

## Testing
- `tests/engine/test_read_columns.py`: (a) parquet/xlsx read into expected dicts; (b) subprocess proving Parquet + Excel read with polars unimportable (`"polars" not in sys.modules`); (c) CSV raises a clear ImportError when polars blocked, and reads correctly when present; (d) unsupported-ext/not-found/empty guards.
- `tests/engine/test_scan_file_columns_parity.py`: for a parquet + xlsx fixture, `scan_columns(read_columns(f))` == the covered-profiler subset of `scan_file(f)` (build "expected" by running `_MECHANICAL_PROFILERS` + native-gated `_HARD_PROFILERS` + temporal over `PolarsFrame(read_file(f))`, mirroring the S2.3 parity-test construction).
- Existing reader/scanner tests pass UNEDITED; `import goldencheck` loads zero Polars (import gate).

## Non-goals (YAGNI)
JSON; a text-mode CSV reader; changing `read_file`; the deps-flip / version bump / nopolars-required lane (P4b); pyarrow-based CSV (pyarrow's CSV inference != Polars' → not byte-identical); streaming/lazy reads.
