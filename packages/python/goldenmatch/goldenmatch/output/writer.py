"""Output writer for GoldenMatch results."""

from __future__ import annotations

from pathlib import Path

from goldenmatch._polars_lazy import pl, polars_available


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

    # Availability is checked BEFORE any `pl.` access: polars is an optional
    # extra, and `isinstance(df, pl.DataFrame)` would itself import it (and
    # raise on a polars-free install) just to ask the question.
    have_polars = polars_available()
    is_pl = have_polars and isinstance(df, pl.DataFrame)

    if not is_pl:
        if fmt == "parquet":
            import pyarrow.parquet as pq

            pq.write_table(df, path)
            return path
        if not have_polars:
            if fmt == "csv":
                # Polars-free install: write the bytes polars would have
                # written (parity pinned by tests/test_csv_arrow_polars_parity.py)
                # rather than failing on the missing optional extra.
                from goldenmatch.output._csv_arrow import write_csv_polars_parity

                return write_csv_polars_parity(df, path)
            raise RuntimeError(
                f"{fmt!r} output requires the optional polars extra "
                f"(pip install 'goldenmatch[polars]'). 'csv' and 'parquet' "
                f"write without it."
            )
        df = pl.from_arrow(df)  # polars-lane: xlsx byte-formatting (engine) is the PINNED output contract; csv/parquet already returned above

    if fmt == "csv":
        df.write_csv(path)
    elif fmt == "parquet":
        df.write_parquet(path)
    elif fmt == "xlsx":
        df.write_excel(path)
    else:
        raise ValueError(f"Unsupported format: {fmt}")

    return path
