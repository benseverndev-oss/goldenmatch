"""File reader — loads CSV, Parquet, and Excel files into Polars DataFrames."""
from __future__ import annotations

import logging
from pathlib import Path

from goldencheck._polars_lazy import pl

logger = logging.getLogger(__name__)
SUPPORTED_EXTENSIONS = {".csv", ".parquet", ".xlsx", ".xls"}

def read_file(path: Path) -> pl.DataFrame:
    # Normalise the path early so symlink-and-`..` traversal can't smuggle
    # access to unexpected locations through downstream Polars / openpyxl
    # readers. `path` is a trusted-config / CLI value, but the normalisation
    # is cheap insurance against accidental traversal in API callers.
    path = Path(path).resolve()
    ext = path.suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Unsupported file format: {ext}. Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}")
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path.name}")

    # Log basename only — full paths can carry control characters /
    # newlines that pollute structured logs (CodeQL py/log-injection).
    logger.info("Reading %s (%s)", path.name, ext)

    if path.stat().st_size == 0:
        raise ValueError("File has no data rows. Nothing to profile.")

    if ext == ".csv":
        try:
            return pl.read_csv(path, infer_schema_length=10000)
        except Exception:
            try:
                return pl.read_csv(path, infer_schema_length=10000, encoding="latin-1")
            except Exception as e:
                raise ValueError(f"Could not read CSV: {e}. Try specifying --separator or --quote-char") from e
    elif ext == ".parquet":
        return pl.read_parquet(path)
    elif ext in (".xlsx", ".xls"):
        try:
            return pl.read_excel(path, engine="openpyxl")
        except Exception as e:
            if "password" in str(e).lower() or "encrypted" in str(e).lower():
                raise ValueError("File appears to be password-protected. GoldenCheck cannot read encrypted files.") from e
            raise
    else:
        raise ValueError(f"Unsupported file format: {ext}")


def _read_parquet_columns(path: Path) -> dict[str, list]:
    """Read a Parquet file into a dict[str, list] without Polars.

    Byte-identical to `pl.read_parquet(path).to_dict(as_series=False)` — pyarrow's
    `to_pydict()` produces value-identical Python scalars for the supported dtypes.
    """
    try:
        import pyarrow.parquet as pq
    except ImportError as e:
        raise ImportError(
            "Reading Parquet without Polars needs pyarrow: pip install goldencheck[parquet]"
        ) from e
    return pq.read_table(str(path)).to_pydict()
