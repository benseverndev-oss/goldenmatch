"""File reader — loads CSV, Parquet, and Excel files into Polars DataFrames."""
from __future__ import annotations
import logging
from pathlib import Path
import polars as pl

logger = logging.getLogger(__name__)
SUPPORTED_EXTENSIONS = {".csv", ".parquet", ".xlsx", ".xls"}

def read_file(path: Path) -> pl.DataFrame:
    path = Path(path)
    ext = path.suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Unsupported file format: {ext}. Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}")
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    logger.info("Reading %s (%s)", path, ext)

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
            return pl.read_excel(path)
        except Exception as e:
            if "password" in str(e).lower() or "encrypted" in str(e).lower():
                raise ValueError("File appears to be password-protected. GoldenCheck cannot read encrypted files.") from e
            raise
    else:
        raise ValueError(f"Unsupported file format: {ext}")
