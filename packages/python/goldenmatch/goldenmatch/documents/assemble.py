"""Collapse per-document ExtractResults into one records DataFrame + a report."""
from __future__ import annotations

from goldenmatch._polars_lazy import pl
from goldenmatch.documents.types import (
    ExtractResult,
    IngestReport,
    TargetSchema,
)

SIDECARS = ["_source_file", "_source_page", "_extract_confidence"]


def _empty_frame(schema: TargetSchema) -> pl.DataFrame:
    cols = {c: pl.Series(c, [], dtype=pl.Utf8) for c in schema.column_names()}
    cols["_source_file"] = pl.Series("_source_file", [], dtype=pl.Utf8)
    cols["_source_page"] = pl.Series("_source_page", [], dtype=pl.Int64)
    cols["_extract_confidence"] = pl.Series("_extract_confidence", [], dtype=pl.Float64)
    return pl.DataFrame(cols)


def assemble(results: list[ExtractResult], schema: TargetSchema, *,
             drop_empty: bool = True,
             files: list[str] | None = None) -> tuple[pl.DataFrame, IngestReport]:
    """`files` (optional) aligns to `results` positionally so an errored doc can be named
    in the report even though it produced no rows."""
    report = IngestReport(n_files=len(results))
    cols = schema.column_names()
    records: list[dict] = []
    for idx, res in enumerate(results):
        if res.error is not None:
            fname = files[idx] if files and idx < len(files) else "<unknown>"
            report.errors.append((fname, res.error))
            continue
        for row in res.rows:
            if drop_empty and all(v is None for v in row.values.values()):
                continue
            rec = dict(row.values)
            rec["_source_file"] = row.source_file
            rec["_source_page"] = row.source_page
            rec["_extract_confidence"] = row.row_confidence()
            records.append(rec)

    if not records:
        report.n_rows = 0
        return _empty_frame(schema), report

    df = pl.DataFrame(records)
    # enforce column order (schema cols first, then sidecars) and string typing on cols
    df = df.select([pl.col(c).cast(pl.Utf8) for c in cols] +
                   [pl.col("_source_file").cast(pl.Utf8),
                    pl.col("_source_page").cast(pl.Int64),
                    pl.col("_extract_confidence").cast(pl.Float64)])
    report.n_rows = df.height
    return df, report
