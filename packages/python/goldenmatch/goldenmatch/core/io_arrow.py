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

import logging
from pathlib import Path
from typing import Any

from goldenmatch.core.ingest import _TEXT_SUFFIXES, _is_probably_utf8

# Polars' CSV readers only ever null a BARE empty field -- never "NA"/"NULL"/
# "null"/etc (those stay literal strings). pyarrow's own ConvertOptions
# default null_values list is much wider, so it must be narrowed explicitly
# rather than left at the default.
_NULL_VALUES = [""]


logger = logging.getLogger(__name__)


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

    # Non-UTF-8 file: mirror ingest.py's cp1252 fallback INCLUDING the
    # warning (W5e: on the arrow-default route THIS is the caller-facing
    # reader -- ingest.py's polars-route warning never fires, so the
    # "stays quiet to avoid double-logging" assumption inverted).
    logger.warning(
        "%s is not valid UTF-8; decoding as Windows-1252 (cp1252). "
        "Pass encoding=/--encoding to override if that is wrong.",
        path,
    )
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


# --------------------------------------------------------------------------
# Multi-file ingest (CLI + service entry points)
# --------------------------------------------------------------------------


def read_files_arrow(
    specs,
    *,
    source_column: str | None = None,
    row_id_column: str | None = None,
    encoding: str | None = "utf8-lossy",
):
    """Read one or more files into a single ``pyarrow.Table``, polars-free.

    This is the shape half the CLI needs and kept open-coding against polars:
    read each file, optionally stamp the source name onto every row, concat
    with a UNION of columns, then number the rows. Written once here because it
    had already been written three times (``auto_configure``, ``a2a.skills``,
    ``api.server``) and, in the CLI, written against ``pl.concat`` -- which is
    why ten commands raised ``ImportError`` on a default install.

    Args:
        specs: iterable of ``path`` or ``(path, source_name)``.
        source_column: when set, add a column of the per-file source name.
        row_id_column: when set, append an Int64 row index.
        encoding: passed to the CSV reader for text files.

    Returns:
        A ``pyarrow.Table``.

    Concat semantics are ``promote_options="permissive"``, which is what
    ``pl.concat(..., how="diagonal")`` did: files with different column sets
    union, and the missing cells are null. Plain ``pa.concat_tables`` would
    raise instead -- the exact trap that made a multi-file fixture blow up
    while porting auto-config.
    """
    import pyarrow as pa

    tables = []
    for spec in specs:
        if isinstance(spec, (tuple, list)):
            path, source = spec[0], (spec[1] if len(spec) > 1 else None)
        else:
            path, source = spec, None

        path = Path(path)
        suffix = path.suffix.lower()
        if suffix in (".parquet", ".xlsx"):
            table = read_table_arrow(path)
        else:
            table = read_table_arrow(path, encoding=encoding)

        if source_column is not None:
            label = source if source is not None else path.stem
            table = table.append_column(
                source_column, pa.array([label] * table.num_rows, type=pa.string())
            )
        tables.append(table)

    if not tables:
        raise ValueError("read_files_arrow requires at least one file")

    combined = (
        pa.concat_tables(tables, promote_options="permissive")
        if len(tables) > 1
        else tables[0]
    )

    if row_id_column is not None:
        combined = combined.append_column(
            row_id_column,
            pa.array(list(range(combined.num_rows)), type=pa.int64()),
        )
    return combined
