"""Polars-free Arrow IO front for GoldenMatch (Polars-eviction W1).

``read_table_arrow`` reads CSV / Parquet / Excel into a ``pyarrow.Table``
with POLARS-PARITY semantics -- it is proven byte-for-byte (values + dtype
class) against ``goldenmatch.core.ingest.load_file`` by the corpus test in
``tests/test_io_arrow_ingest_parity.py``. This is gate #1 of the Polars-
eviction program's parity contract: any divergence between this reader and
the polars reference gets fixed HERE, at the reader, not downstream.

pyarrow is imported lazily inside each function so importing this module
never forces pyarrow to load (mirrors the W0 lazy-polars-import discipline
even though pyarrow is already a hard base dependency).

Known reader deltas vs ``load_file`` (documented, not silently tolerated):

  - Date inference: pyarrow's CSV reader infers ``date32``/``timestamp`` for
    ISO-date-shaped strings, while polars' ``scan_csv``/``read_csv`` never
    auto-parses dates (``load_file`` never passes ``try_parse_dates``) --
    is resolved below: the CSV reader probes the naturally-inferred schema
    and forces any temporal column back to ``pa.string()`` via
    ``ConvertOptions(column_types=...)`` before the real read, so the raw
    source text passes through unparsed exactly like polars.
  - Empty string fields (found by the Task 6 output-level differential
    harness, ``scripts/diff_frame_backends.py``, on a blank ``external_id``
    column -- the 16-case reader-only corpus had no blank-field case):
    pyarrow's CSV reader defaults an empty **string**-typed cell to ``""``,
    not null (``strings_can_be_null`` defaults False; numeric columns
    already null an empty cell either way, so the divergence is
    string-column-only). Polars' ``scan_csv``/``read_csv`` always treats a
    bare empty field as null. Fixed via ``ConvertOptions(strings_can_be_null=
    True, null_values=[""])`` on every CSV read (not just the temporal
    second pass) -- ``null_values`` is narrowed to ``[""]`` because
    pyarrow's own default list additionally treats ``"NA"``/``"NULL"``/
    ``"null"``/``"NaN"``/etc. as null, which polars does NOT (those stay
    literal strings) -- the wider default list would trade one divergence
    for another.
  - Error parity: a CSV row with the wrong column count raises on BOTH
    engines (``load_file``'s default path passes no
    ``ignore_errors``/``truncate_ragged_lines`` knob), so this reader does
    not set ``ParseOptions(invalid_row_handler=...)`` either -- a ragged
    row is a hard error here too.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from goldenmatch.core.ingest import _TEXT_SUFFIXES, _is_probably_utf8

# Polars' CSV readers only ever null a BARE empty field -- never "NA"/"NULL"/
# "null"/etc (those stay literal strings). pyarrow's own ConvertOptions
# default null_values list is much wider, so it must be narrowed explicitly
# rather than left at the default.
_NULL_VALUES = [""]


def read_table_arrow(
    path: Path | str,
    *,
    separator: str = ",",
    encoding: str | None = None,
    sheet: str | None = None,
):
    """Read a data file into a ``pyarrow.Table`` with polars-parity semantics.

    Args:
        path: Path to the file.
        separator: Column delimiter for CSV/text files.
        encoding: Text encoding for CSV files. ``None`` = auto-detect
            (mirrors ``load_file``'s ``_is_probably_utf8`` probe).
        sheet: Sheet name for Excel files (``None`` = first/active sheet,
            matching ``pl.read_excel``'s default).

    Returns:
        A ``pyarrow.Table``.
    """
    path = Path(path)
    suffix = path.suffix.lower()

    # Suffix dispatch mirrors load_file EXACTLY: only ".parquet" is parquet
    # (not ".pq"), only ".xlsx" is Excel (not ".xlsm"/".xltx"/".xltm"), and
    # only the ingest text-suffix set (or no suffix) is CSV. Anything else
    # raises the same ValueError load_file does -- error parity includes
    # rejecting what the reference rejects.
    if suffix == ".parquet":
        return _read_parquet_arrow(path)

    if suffix == ".xlsx":
        return _read_excel_arrow(path, sheet=sheet)

    if suffix in _TEXT_SUFFIXES or suffix == "":
        return _read_csv_arrow(path, separator=separator, encoding=encoding)

    raise ValueError(f"Unsupported file format: {suffix!r}")


# --------------------------------------------------------------------------
# CSV
# --------------------------------------------------------------------------


def _read_csv_arrow(path: Path, *, separator: str, encoding: str | None):
    import pyarrow.csv as pa_csv

    parse_options = pa_csv.ParseOptions(delimiter=separator)

    if encoding is not None:
        if encoding in ("utf8", "utf8-lossy"):
            if encoding == "utf8":
                # Strict utf-8, matching polars scan_csv(encoding="utf8"):
                # invalid bytes raise rather than being silently replaced.
                return _read_csv_direct(path, parse_options)
            # utf8-lossy: decode with replacement, then feed the re-encoded
            # text through the same buffer-reader path as the auto/non-utf8
            # branches below.
            text = path.read_bytes().decode("utf-8", errors="replace")
            return _read_csv_from_text(text, parse_options)
        # Named Python codec (cp1252, latin-1, ...): mirror
        # ingest.py's ``decode(encoding, errors="replace")``.
        text = path.read_bytes().decode(encoding, errors="replace")
        return _read_csv_from_text(text, parse_options)

    # AUTO mode: probe like ingest._is_probably_utf8.
    if _is_probably_utf8(path):
        # Fast path for the common (valid UTF-8) case. load_file uses
        # encoding="utf8-lossy" here too, but for genuinely valid UTF-8
        # there is no observable difference from a direct strict read.
        return _read_csv_direct(path, parse_options)

    # Non-UTF-8 file: mirror ingest.py's cp1252 fallback (no warning log
    # here -- load_file already logs it; this reader is a parallel path,
    # not the caller-facing one, so it stays quiet to avoid double-logging
    # once wired into the pipeline).
    text = path.read_bytes().decode("cp1252", errors="replace")
    return _read_csv_from_text(text, parse_options)


def _base_convert_options(**extra: Any):
    """The polars-parity ``ConvertOptions`` every CSV read uses: only a bare
    empty field is null (not pyarrow's wider "NA"/"NULL"/etc default list).
    ``extra`` overlays additional kwargs (e.g. a temporal ``column_types``
    override) onto the same base.
    """
    import pyarrow.csv as pa_csv

    return pa_csv.ConvertOptions(
        strings_can_be_null=True, null_values=_NULL_VALUES, **extra
    )


def _read_csv_direct(path: Path, parse_options: Any):
    """Read directly from the file path, forcing inferred temporal columns
    back to string (polars never auto-parses CSV dates)."""
    import pyarrow.csv as pa_csv

    probe = pa_csv.read_csv(
        str(path), parse_options=parse_options,
        convert_options=_base_convert_options(),
    )
    column_types = _temporal_override_types(probe.schema)
    if not column_types:
        return probe
    convert_options = _base_convert_options(
        timestamp_parsers=[], column_types=column_types
    )
    return pa_csv.read_csv(
        str(path), parse_options=parse_options, convert_options=convert_options
    )


def _read_csv_from_text(text: str, parse_options: Any):
    """Read from an in-memory decoded string, forcing inferred temporal
    columns back to string (same rationale as ``_read_csv_direct``)."""
    import pyarrow as pa
    import pyarrow.csv as pa_csv

    raw = text.encode("utf-8")
    probe = pa_csv.read_csv(
        pa.BufferReader(raw), parse_options=parse_options,
        convert_options=_base_convert_options(),
    )
    column_types = _temporal_override_types(probe.schema)
    if not column_types:
        return probe
    convert_options = _base_convert_options(
        timestamp_parsers=[], column_types=column_types
    )
    return pa_csv.read_csv(
        pa.BufferReader(raw), parse_options=parse_options, convert_options=convert_options
    )


def _temporal_override_types(schema: Any) -> dict:
    """Map every temporal (date/time/timestamp/duration) column name to
    ``pa.string()``, so a forced re-read takes the raw CSV text as-is
    instead of parsing it -- matching polars' never-auto-parse-dates
    behavior on scan_csv/read_csv.
    """
    import pyarrow as pa

    return {
        field.name: pa.string() for field in schema if pa.types.is_temporal(field.type)
    }


# --------------------------------------------------------------------------
# Parquet
# --------------------------------------------------------------------------


def _read_parquet_arrow(path: Path):
    import pyarrow.parquet as pa_parquet

    return pa_parquet.read_table(str(path))


# --------------------------------------------------------------------------
# Excel
# --------------------------------------------------------------------------


def _read_excel_arrow(path: Path, *, sheet: str | None):
    import openpyxl
    import pyarrow as pa

    wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    try:
        # sheet=None -> the active sheet, matching pl.read_excel's default
        # (its first/active sheet, not necessarily the workbook's index-0
        # sheet if a different one was made active when the file was saved).
        ws = wb[sheet] if sheet is not None else wb.active
        if ws is None:
            # openpyxl types `wb.active` as Optional (a workbook saved with
            # no active sheet); pl.read_excel would fail on such a file too.
            raise ValueError(f"sheet {sheet!r} not found in {path}")
        rows = list(ws.iter_rows(values_only=True))
    finally:
        wb.close()

    if not rows:
        return pa.table({})

    header = [str(c) for c in rows[0]]
    data_rows = rows[1:]
    columns = {
        name: [row[i] for row in data_rows] for i, name in enumerate(header)
    }
    # pa.array's own type inference already promotes mixed int/float ->
    # double and recognizes datetime.date/datetime.datetime -> timestamp,
    # matching pl.read_excel's per-column inference on the same openpyxl
    # values (both engines read the same cell values; the parity is in
    # letting Arrow's/Polars' own inference run rather than second-guessing
    # it here).
    return pa.table({name: pa.array(values) for name, values in columns.items()})
