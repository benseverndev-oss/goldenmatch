"""Collapse per-document ExtractResults into one records DataFrame + a report."""
from __future__ import annotations

import polars as pl

from goldenmatch.documents.types import (
    ExtractResult,
    IngestReport,
    StructuredResult,
    TargetSchema,
    _DocOutcome,
)

SIDECARS = ["_source_file", "_source_page", "_extract_confidence"]

# The structured (two-frame) sidecar set: provenance/confidence + the stable
# `_doc_id` (path fingerprint, the FK linking line items to their header) and
# `_doctype`. Callers exclude these from ER match fields:
#     dedupe_df(df, exclude_columns=DOC_SIDECARS)
DOC_SIDECARS = ["_source_file", "_source_page", "_extract_confidence", "_doc_id", "_doctype"]

# Sidecar (name, dtype) specs in emit order for each frame.
_HEADER_SIDECAR_SPECS = [
    ("_source_file", pl.Utf8), ("_source_page", pl.Int64),
    ("_extract_confidence", pl.Float64), ("_doc_id", pl.Utf8), ("_doctype", pl.Utf8),
]
_ITEM_SIDECAR_SPECS = [
    ("_doc_id", pl.Utf8), ("_line_no", pl.Int64), ("_source_file", pl.Utf8),
    ("_source_page", pl.Int64), ("_extract_confidence", pl.Float64),
]


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


def _frame_from_records(records: list[dict], data_cols: list[str],
                        sidecar_specs: list[tuple[str, object]]) -> pl.DataFrame:
    """Build a frame from heterogeneous-keyed records with a DETERMINISTIC column
    order. `data_cols` is the caller's pre-computed first-appearance union; each
    record is padded with the missing cols = None before one `pl.DataFrame` call
    (a plain ragged `pl.DataFrame(list_of_dicts)` raises). The final select list
    is built from `data_cols` (NOT `df.columns`) so a polars dict-key reshuffle
    can't reorder the output."""
    for rec in records:
        for c in data_cols:
            rec.setdefault(c, None)
    df = pl.DataFrame(records)
    return df.select(
        [pl.col(c).cast(pl.Utf8) for c in data_cols]
        + [pl.col(name).cast(dtype) for name, dtype in sidecar_specs]
    )


def _empty_header_frame() -> pl.DataFrame:
    return pl.DataFrame(
        {name: pl.Series(name, [], dtype=dtype) for name, dtype in _HEADER_SIDECAR_SPECS}
    )


def assemble_structured(outcomes: list[_DocOutcome], *, drop_empty: bool = True
                        ) -> tuple[pl.DataFrame, IngestReport]:
    """Turn per-doc `_DocOutcome`s into a header frame (one row per entity) + an
    optional line-item frame (children linked by `_doc_id`), plus an `IngestReport`.

    Column order is deterministic: header/line-item field names in FIRST-APPEARANCE
    order across the batch, then the fixed sidecars. Flat `ExtractResult` outcomes
    (doctype "generic") contribute their rows to the header frame with no line items.
    The caller fills `report.vlm_calls` / `report.classify_confidence` (flow facts)."""
    # De-dup outcomes by doc_id, last-wins, preserving first-appearance position.
    by_id: dict[str, _DocOutcome] = {}
    for o in outcomes:
        by_id[o.doc_id] = o
    deduped = list(by_id.values())

    report = IngestReport(n_files=len(deduped))

    header_records: list[dict] = []
    header_cols: list[str] = []
    header_seen: set[str] = set()
    item_records: list[dict] = []
    item_cols: list[str] = []
    item_seen: set[str] = set()

    def _add_header(row, o: _DocOutcome) -> None:
        if drop_empty and all(v is None for v in row.values.values()):
            return
        rec = dict(row.values)
        for name in row.values:
            if name not in header_seen:
                header_seen.add(name)
                header_cols.append(name)
        rec["_source_file"] = o.source_file
        rec["_source_page"] = row.source_page
        rec["_extract_confidence"] = row.row_confidence()
        rec["_doc_id"] = o.doc_id
        rec["_doctype"] = o.doctype
        header_records.append(rec)

    for o in deduped:
        res = o.result
        if res.error is not None:
            report.errors.append((o.source_file, res.error))
            continue
        report.doctypes[o.doc_id] = o.doctype
        if isinstance(res, StructuredResult):
            if res.header is not None:
                _add_header(res.header, o)
            for line_no, item in enumerate(res.line_items):
                rec = dict(item.values)
                for name in item.values:
                    if name not in item_seen:
                        item_seen.add(name)
                        item_cols.append(name)
                rec["_doc_id"] = o.doc_id
                rec["_line_no"] = line_no
                rec["_source_file"] = o.source_file
                rec["_source_page"] = item.source_page
                rec["_extract_confidence"] = item.row_confidence()
                item_records.append(rec)
        else:  # flat ExtractResult -> each row is a header row, no line items
            for row in res.rows:
                _add_header(row, o)

    if header_records:
        df = _frame_from_records(header_records, header_cols, _HEADER_SIDECAR_SPECS)
    else:
        df = _empty_header_frame()
    report.n_rows = df.height

    if item_records:
        report.line_items = _frame_from_records(item_records, item_cols, _ITEM_SIDECAR_SPECS)
    else:
        report.line_items = None

    return df, report
