"""Two-frame structured assemble: turn per-doc `_DocOutcome`s into a header frame
(entities) + an optional line-item frame (children linked by `_doc_id`)."""
from __future__ import annotations

import polars as pl

from goldenmatch.documents.types import (
    IngestReport,
    StructuredResult,
    _DocOutcome,
)

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


class _ColUnion:
    """First-appearance-ordered column union. Keeps the `seen` set and the ordered
    list in lockstep so the header and line-item bookkeeping can't drift."""

    def __init__(self) -> None:
        self.cols: list[str] = []
        self._seen: set[str] = set()

    def add(self, names) -> None:
        for name in names:
            if name not in self._seen:
                self._seen.add(name)
                self.cols.append(name)


def _frame_from_records(records: list[dict], data_cols: list[str],
                        sidecar_specs: list[tuple[str, object]]) -> pl.DataFrame:
    """Build a frame from heterogeneous-keyed records with a DETERMINISTIC column
    order. `data_cols` is the caller's pre-computed first-appearance union; each
    record is padded IN PLACE with the missing cols = None before one `pl.DataFrame`
    call (a plain ragged `pl.DataFrame(list_of_dicts)` raises). The in-place fill is
    intentional -- `records` is a throwaway list this function owns. The final
    select list is built from `data_cols` (NOT `df.columns`) so a polars dict-key
    reshuffle can't reorder the output."""
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
    header_union = _ColUnion()
    item_records: list[dict] = []
    item_union = _ColUnion()

    def _add_header(row, o: _DocOutcome) -> bool:
        """Emit a header row unless drop_empty removes an all-null-match-field row.
        Returns True iff a row was actually emitted (the caller gates line items +
        report keys on this so no orphaned child can ever be produced)."""
        if drop_empty and all(v is None for v in row.values.values()):
            return False
        header_union.add(row.values)
        rec = dict(row.values)
        rec["_source_file"] = o.source_file
        rec["_source_page"] = row.source_page
        rec["_extract_confidence"] = row.row_confidence()
        rec["_doc_id"] = o.doc_id
        rec["_doctype"] = o.doctype
        header_records.append(rec)
        return True

    def _register(o: _DocOutcome) -> None:
        # doctypes and classify_confidence key on EXACTLY the docs that emitted a
        # header row -- keeps the two report maps' key-sets identical + well-defined.
        report.doctypes[o.doc_id] = o.doctype
        report.classify_confidence[o.doc_id] = o.confidence

    for o in deduped:
        # A non-fatal notice (e.g. a raised classify that fell back to generic) rides
        # the report.errors channel so a broken classifier leaves a trace instead of
        # masquerading as a genuine 0.0 classification -- surfaced UNCONDITIONALLY,
        # independent of whether this doc later emits a header row, so an empty/dropped
        # fallback can't swallow it. It does NOT touch doctypes/classify_confidence, so
        # their key-sets stay aligned. Recorded separately from result.error below
        # (distinct messages -> both kept, no double-count of the same string).
        if o.warning is not None:
            report.errors.append((o.source_file, o.warning))
        res = o.result
        if res.error is not None:
            report.errors.append((o.source_file, res.error))
            continue
        if isinstance(res, StructuredResult):
            emitted = res.header is not None and _add_header(res.header, o)
            if not emitted:
                # Header absent or dropped: its line items would be orphans (a
                # `_doc_id` FK with no header row). Discard them, but surface the
                # loss so a failed-entity doc isn't silently swallowed.
                if res.line_items:
                    report.errors.append((
                        o.source_file,
                        f"header empty/dropped; {len(res.line_items)} line item(s) discarded",
                    ))
                continue
            _register(o)
            for line_no, item in enumerate(res.line_items):
                item_union.add(item.values)
                rec = dict(item.values)
                rec["_doc_id"] = o.doc_id
                rec["_line_no"] = line_no
                rec["_source_file"] = o.source_file
                rec["_source_page"] = item.source_page
                rec["_extract_confidence"] = item.row_confidence()
                item_records.append(rec)
        else:  # flat ExtractResult -> each row is a header row, no line items
            # list (not any/generator) so EVERY row is emitted, not just up to the
            # first survivor.
            if any([_add_header(row, o) for row in res.rows]):
                _register(o)

    if header_records:
        df = _frame_from_records(header_records, header_union.cols, _HEADER_SIDECAR_SPECS)
    else:
        df = _empty_header_frame()
    report.n_rows = df.height

    if item_records:
        report.line_items = _frame_from_records(item_records, item_union.cols, _ITEM_SIDECAR_SPECS)
    else:
        report.line_items = None

    return df, report
