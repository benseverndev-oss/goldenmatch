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

  - NONE as of this writing. The one real divergence surfaced during
    development -- pyarrow's CSV reader infers ``date32``/``timestamp`` for
    ISO-date-shaped strings, while polars' ``scan_csv``/``read_csv`` never
    auto-parses dates (``load_file`` never passes ``try_parse_dates``) --
    is resolved below: the CSV reader probes the naturally-inferred schema
    and forces any temporal column back to ``pa.string()`` via
    ``ConvertOptions(column_types=...)`` before the real read, so the raw
    source text passes through unparsed exactly like polars.
  - Error parity: a CSV row with the wrong column count raises on BOTH
    engines (``load_file``'s default path passes no
    ``ignore_errors``/``truncate_ragged_lines`` knob), so this reader does
    not set ``ParseOptions(invalid_row_handler=...)`` either -- a ragged
    row is a hard error here too.
"""

from __future__ import annotations

from pathlib import Path

from goldenmatch.core.ingest import _is_probably_utf8

_PARQUET_SUFFIXES = {".parquet", ".pq"}
_EXCEL_SUFFIXES = {".xlsx", ".xlsm", ".xltx", ".xltm"}


def read_table_arrow(
    path,
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

    if suffix in _PARQUET_SUFFIXES:
        return _read_parquet_arrow(path)

    if suffix in _EXCEL_SUFFIXES:
        return _read_excel_arrow(path, sheet=sheet)

    return _read_csv_arrow(path, separator=separator, encoding=encoding)


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


def _read_csv_direct(path: Path, parse_options):
    """Read directly from the file path, forcing inferred temporal columns
    back to string (polars never auto-parses CSV dates)."""
    import pyarrow.csv as pa_csv

    probe = pa_csv.read_csv(str(path), parse_options=parse_options)
    column_types = _temporal_override_types(probe.schema)
    if not column_types:
        return probe
    convert_options = pa_csv.ConvertOptions(
        timestamp_parsers=[], column_types=column_types
    )
    return pa_csv.read_csv(
        str(path), parse_options=parse_options, convert_options=convert_options
    )


def _read_csv_from_text(text: str, parse_options):
    """Read from an in-memory decoded string, forcing inferred temporal
    columns back to string (same rationale as ``_read_csv_direct``)."""
    import pyarrow as pa
    import pyarrow.csv as pa_csv

    raw = text.encode("utf-8")
    probe = pa_csv.read_csv(pa.BufferReader(raw), parse_options=parse_options)
    column_types = _temporal_override_types(probe.schema)
    if not column_types:
        return probe
    convert_options = pa_csv.ConvertOptions(
        timestamp_parsers=[], column_types=column_types
    )
    return pa_csv.read_csv(
        pa.BufferReader(raw), parse_options=parse_options, convert_options=convert_options
    )


def _temporal_override_types(schema) -> dict:
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
# Parquet / Excel (Task 3 -- not yet implemented)
# --------------------------------------------------------------------------


def _read_parquet_arrow(path: Path):
    raise NotImplementedError("parquet support lands in Task 3")


def _read_excel_arrow(path: Path, *, sheet: str | None):
    raise NotImplementedError("excel support lands in Task 3")
