"""File ingest utilities for GoldenMatch."""

from __future__ import annotations

import codecs
import io
import logging
from pathlib import Path

from goldenmatch._polars_lazy import pl
from goldenmatch.core._paths import safe_path
from goldenmatch.core.frame import resolve_frame_backend

logger = logging.getLogger(__name__)

# Text-file extensions that should route through smart_load
_TEXT_SUFFIXES = {".csv", ".txt", ".tsv", ".dat", ".tab", ".psv", ".log", ".asc"}


def _arrow_route_eligible(suffix: str, parse_mode: str, delimiter: str | None) -> bool:
    """True when ``suffix``/``parse_mode``/``delimiter`` hit a branch this
    function's polars path handles WITHOUT delegating to ``smart_load``.

    Mirrors the exact dispatch below: ``.parquet`` and ``.xlsx`` are always
    covered; text suffixes (or no suffix) are covered only in the same
    ``parse_mode == "auto"`` fast-path condition the polars branch uses --
    anything else (fixed_width/key_value/block/entity_extract parse modes,
    or auto mode on a non-.csv text file with no explicit delimiter) falls
    through to ``smart_load``, which ``io_arrow`` does not implement, so the
    arrow route must not intercept it.
    """
    if suffix == ".parquet":
        return True
    if suffix == ".xlsx":
        return True
    if suffix in _TEXT_SUFFIXES or suffix == "":
        return parse_mode == "auto" and (delimiter is not None or suffix == ".csv")
    return False


def _is_probably_utf8(path: Path | str, sample_bytes: int = 1 << 16) -> bool:
    """True if a leading sample of the file decodes as UTF-8.

    Uses an incremental decoder with ``final=False`` so a multi-byte character
    split at the sample boundary is buffered (not a false 'invalid'); only real
    invalid bytes inside the sample flip it to False.
    """
    try:
        with open(path, "rb") as fh:
            head = fh.read(sample_bytes)
    except OSError:
        return True  # unreadable -> let the polars path surface the real error
    try:
        codecs.getincrementaldecoder("utf-8")().decode(head, final=False)
        return True
    except UnicodeDecodeError:
        return False


def load_file(
    path: Path | str,
    delimiter: str | None = None,  # None = auto-detect
    encoding: str | None = None,   # None = auto-detect
    sheet: str | None = None,
    parse_mode: str = "auto",
    header_row: int | None = None,
    has_header: bool | None = None,
    skip_rows: list[int] | None = None,
) -> pl.LazyFrame:
    """Load a data file as a Polars LazyFrame.

    Supports CSV/text files (with smart auto-detection), Parquet, and Excel.

    Args:
        path: Path to the file.
        delimiter: Column delimiter for text files. ``None`` triggers
            auto-detection via :func:`smart_load`.
        encoding: Text encoding. ``None`` triggers auto-detection.
        sheet: Sheet name for Excel files (optional).
        parse_mode: Parsing strategy — ``"auto"`` (default), ``"delimited"``,
            ``"fixed_width"``, ``"key_value"``, ``"block"``, or
            ``"entity_extract"``.
        header_row: Explicit header row index (``None`` = auto-detect).
        has_header: Whether file has a header row (``None`` = auto-detect).
        skip_rows: Explicit list of row indices to skip.

    Returns:
        A Polars LazyFrame.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If the file format is not supported.
    """
    path = safe_path(path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    suffix = path.suffix.lower()

    if resolve_frame_backend() == "arrow" and _arrow_route_eligible(
        suffix, parse_mode, delimiter
    ):
        # Lazy import -- keeps the W0 import-gate posture (io_arrow itself
        # also imports pyarrow lazily inside each function).
        from goldenmatch.core.io_arrow import read_table_arrow

        sep = delimiter or ","
        tbl = read_table_arrow(path, separator=sep, encoding=encoding, sheet=sheet)
        # Sanitize the user-provided path for logging (py/log-injection):
        # escape newlines so a crafted filename can't forge log records.
        safe_log_path = str(path).replace("\r", "\\r").replace("\n", "\\n")
        logger.info(
            "GOLDENMATCH_FRAME=arrow: reading %s via pyarrow (experimental)",
            safe_log_path,
        )
        return pl.from_arrow(tbl).lazy()

    if suffix == ".parquet":
        return pl.scan_parquet(path)

    if suffix == ".xlsx":
        kwargs = {"engine": "openpyxl"}
        if sheet is not None:
            kwargs["sheet_name"] = sheet
        df = pl.read_excel(path, **kwargs)
        return df.lazy()

    # Text file handling ─────────────────────────────────────────────────
    if suffix in _TEXT_SUFFIXES or suffix == "":
        # For .csv files with default auto mode, preserve backwards-compatible
        # fast Polars scan_csv path (comma-delimited by default).
        if parse_mode == "auto" and (delimiter is not None or suffix == ".csv"):
            sep = delimiter or ","
            if encoding is not None:
                # Caller was explicit. polars scan_csv only accepts utf8 /
                # utf8-lossy; for any other Python codec (cp1252, latin-1, ...)
                # decode via the codec and feed polars, so `--encoding cp1252`
                # actually works instead of erroring on an unsupported literal.
                if encoding in ("utf8", "utf8-lossy"):
                    return pl.scan_csv(path, separator=sep, encoding=encoding)
                text = Path(path).read_bytes().decode(encoding, errors="replace")
                return pl.read_csv(io.StringIO(text), separator=sep).lazy()
            if _is_probably_utf8(path):
                # Fast lazy path for the common (valid UTF-8) case -- unchanged.
                return pl.scan_csv(path, separator=sep, encoding="utf8-lossy")
            # Non-UTF-8 file (typically a Windows-1252 / Latin-1 export from
            # Excel or a legacy system). The old default silently lossy-decoded
            # these, turning "José Muñoz" into U+FFFD replacement chars in the
            # golden output -- silent corruption of a name-matching tool's own
            # canonical record. Decode as cp1252 (the dominant real-world case)
            # and WARN loudly instead. Explicit `encoding=` / `--encoding`
            # overrides. This path materializes (non-UTF-8 files are typically
            # small legacy exports); valid-UTF-8 files keep the lazy fast path.
            logger.warning(
                "%s is not valid UTF-8; decoding as Windows-1252 (cp1252). "
                "Pass encoding=/--encoding to override if that is wrong.",
                path,
            )
            text = Path(path).read_bytes().decode("cp1252", errors="replace")
            return pl.read_csv(io.StringIO(text), separator=sep).lazy()

        # Otherwise route through smart_load
        from goldenmatch.core.smart_ingest import smart_load

        df, _meta = smart_load(
            path,
            parse_mode=parse_mode,
            delimiter=delimiter,
            encoding=encoding,
            header_row=header_row,
            has_header=has_header,
            skip_rows=skip_rows,
        )
        return df.lazy()

    raise ValueError(f"Unsupported file format: {suffix!r}")


def load_files(file_specs: list[tuple[Path | str, str]]) -> list[pl.LazyFrame]:
    """Load multiple files, adding a __source__ column to each.

    Args:
        file_specs: List of (path, source_name) tuples.

    Returns:
        List of LazyFrames, each with a __source__ column.
    """
    frames = []
    for path, source_name in file_specs:
        lf = load_file(path)
        lf = lf.with_columns(pl.lit(source_name).alias("__source__"))
        frames.append(lf)
    return frames


def apply_column_map(lf: pl.LazyFrame, column_map: dict[str, str]) -> pl.LazyFrame:
    """Rename columns according to a mapping.

    Args:
        lf: The LazyFrame to rename columns in.
        column_map: Mapping of {original_name: target_name}.
            e.g. {"LNAME": "last_name", "FNAME": "first_name"}

    Returns:
        LazyFrame with renamed columns.

    Raises:
        ValueError: If a source column in the map doesn't exist in the frame.
    """
    available = set(lf.collect_schema().names())
    missing = [src for src in column_map if src not in available]
    if missing:
        raise ValueError(
            f"Column map references columns not in file: {missing}. "
            f"Available: {sorted(available)}"
        )
    return lf.rename(column_map)


def suggest_column_mapping(
    file_columns: list[str],
    target_columns: list[str],
    threshold: float = 0.75,
) -> dict[str, str]:
    """Suggest column mappings from file columns to target columns using fuzzy matching.

    Args:
        file_columns: Column names in the input file.
        target_columns: Expected/target column names from the config.
        threshold: Minimum similarity score (0-1) to suggest a mapping.

    Returns:
        Dict of {file_column: target_column} for suggested matches.
    """
    from rapidfuzz.distance import JaroWinkler

    suggestions: dict[str, str] = {}
    remaining_targets = set(target_columns)

    # Exact matches first (case-insensitive)
    for fc in file_columns:
        for tc in list(remaining_targets):
            if fc.lower() == tc.lower() and fc != tc:
                suggestions[fc] = tc
                remaining_targets.discard(tc)
                break

    # Skip targets that already have an exact match in the file (no rename needed)
    for fc in file_columns:
        for tc in list(remaining_targets):
            if fc == tc:
                remaining_targets.discard(tc)

    # Fuzzy matches for remaining
    for tc in list(remaining_targets):
        best_score = 0.0
        best_fc = None
        for fc in file_columns:
            if fc in suggestions or fc == tc:
                continue
            score = JaroWinkler.similarity(fc.lower(), tc.lower())
            if score > best_score and score >= threshold:
                best_score = score
                best_fc = fc
        if best_fc is not None:
            suggestions[best_fc] = tc
            remaining_targets.discard(tc)

    return suggestions


def validate_columns(lf: pl.LazyFrame, required: list[str]) -> None:
    """Check that required columns exist in a LazyFrame schema.

    Args:
        lf: The LazyFrame to validate.
        required: List of column names that must be present.

    Raises:
        ValueError: If any required columns are missing.
    """
    available = list(lf.collect_schema().names())
    missing = [c for c in required if c not in available]
    if missing:
        raise ValueError(
            f"Missing required columns: {missing}. Available columns: {available}"
        )
