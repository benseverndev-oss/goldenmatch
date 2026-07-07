"""Document/image ingest: turn PDFs/images into a records DataFrame for GoldenMatch."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from goldenmatch.documents.assemble import assemble
from goldenmatch.documents.config import resolve_extractor
from goldenmatch.documents.extractor import Extractor
from goldenmatch.documents.loader import load_pages
from goldenmatch.documents.types import (
    ExtractResult,
    Field,
    IngestReport,
    TargetSchema,
)

__all__ = ["ingest_documents", "TargetSchema", "Field", "IngestReport"]


def ingest_documents(paths, schema: TargetSchema, *, backend: str = "vlm",
                     model: str = "gpt-4o", extractor: Extractor | None = None,
                     drop_empty: bool = True, return_report: bool = False):
    """Extract records from a pile of documents into one Polars DataFrame.

    Hand the result to the ER pipeline as:
        dedupe_df(df, exclude_columns=["_source_file", "_source_page", "_extract_confidence"])
    so the provenance/confidence sidecars are never treated as match fields.

    `extractor` overrides `backend`/`model` (used for tests / custom backends).
    Returns the DataFrame, or `(df, IngestReport)` when `return_report=True`.
    """
    ex = extractor or resolve_extractor(backend, model)
    files = [str(Path(p)) for p in paths]
    results: list[ExtractResult] = []
    for fpath in files:
        try:
            pages = load_pages(fpath)
            res = ex.extract(pages, schema)
        except Exception as e:  # loader/backend hard failure -> recorded, batch continues
            res = ExtractResult(rows=[], error=f"{type(e).__name__}: {e}")
        # stamp the real filename onto each row (backend only knew the page index)
        res = ExtractResult(
            rows=[replace(r, source_file=fpath) for r in res.rows], error=res.error)
        results.append(res)

    df, report = assemble(results, schema, drop_empty=drop_empty, files=files)
    return (df, report) if return_report else df
