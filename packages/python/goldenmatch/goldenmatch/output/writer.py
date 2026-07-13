"""Output writer for GoldenMatch results."""

from __future__ import annotations

from pathlib import Path

from goldenmatch._polars_lazy import pl


def write_output(
    df,
    directory: str | Path,
    run_name: str,
    output_type: str,
    fmt: str,
) -> Path:
    """Write a frame to the specified format (csv, parquet, xlsx).

    W-2 widening: dual-rep. A ``pa.Table`` writes parquet NATIVELY
    (pyarrow.parquet); csv/xlsx BRIDGE through polars because the polars
    writers' formatting (csv quoting/null spelling, xlsx engine) is the
    pinned output contract -- an arrow-native csv writer would change
    bytes on disk. Revisit at D6 (format change allowed at a major).
    Returns the Path of the written file.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)

    filename = f"{run_name}_{output_type}.{fmt}"
    path = directory / filename

    is_pl = isinstance(df, pl.DataFrame)
    if fmt == "parquet" and not is_pl:
        import pyarrow.parquet as pq

        pq.write_table(df, path)
        return path
    if not is_pl:
        df = pl.from_arrow(df)

    if fmt == "csv":
        df.write_csv(path)
    elif fmt == "parquet":
        df.write_parquet(path)
    elif fmt == "xlsx":
        df.write_excel(path)
    else:
        raise ValueError(f"Unsupported format: {fmt}")

    return path
