"""Document/image ingest: turn PDFs/images into a records DataFrame for GoldenMatch."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from goldenmatch.core._hashing import record_fingerprint
from goldenmatch.documents.assemble import DOC_SIDECARS, assemble_structured
from goldenmatch.documents.config import resolve_extractor, resolve_structured
from goldenmatch.documents.extractor import Extractor, TemplateExtractor
from goldenmatch.documents.loader import load_pages
from goldenmatch.documents.templates import get_template, list_templates
from goldenmatch.documents.types import (
    DocTemplate,
    ExtractResult,
    Field,
    IngestReport,
    StructuredResult,
    TargetSchema,
    _DocOutcome,
)

__all__ = [
    "ingest_documents", "TargetSchema", "Field", "IngestReport",
    "DocTemplate", "list_templates", "DOC_SIDECARS",
]


def _resolve_template(template: str | DocTemplate) -> DocTemplate:
    if isinstance(template, str):
        return get_template(template)  # raises ValueError on unknown doctype
    if isinstance(template, DocTemplate):
        return template
    raise TypeError(f"template must be a doctype str or DocTemplate, got {type(template).__name__}")


def _ingest_one(path: str, *, schema: TargetSchema | None,
                template: DocTemplate | None, extractor: Extractor | None,
                template_extractor: TemplateExtractor | None) -> _DocOutcome:
    """Per-doc dispatch (explicit paths only -- flat schema OR pinned template).
    Computes the stable `_doc_id` from the path (NOT header values, so re-runs are
    idempotent), loads pages, extracts, and wraps into a `_DocOutcome`."""
    doc_id = record_fingerprint({"path": path})
    try:
        pages = load_pages(path)
    except Exception as e:  # loader failure -> recorded, batch continues (0 VLM calls)
        return _DocOutcome(doc_id=doc_id, source_file=path, doctype="generic",
                           confidence=1.0, vlm_calls=0,
                           result=ExtractResult(rows=[], error=f"{type(e).__name__}: {e}"))

    if schema is not None:  # flat path
        try:
            res = extractor.extract(pages, schema)
        except Exception as e:
            res = ExtractResult(rows=[], error=f"{type(e).__name__}: {e}")
        # stamp the real filename onto each row (backend only knew the page index)
        res = ExtractResult(rows=[replace(r, source_file=path) for r in res.rows],
                            error=res.error)
        return _DocOutcome(doc_id=doc_id, source_file=path, doctype="generic",
                           confidence=1.0, vlm_calls=1, result=res)

    # pinned template path
    try:
        res = template_extractor.extract_structured(pages, template)
    except Exception as e:  # transport/parse hard failure -> recorded
        res = StructuredResult(header=None, line_items=[], error=f"{type(e).__name__}: {e}")
    return _DocOutcome(doc_id=doc_id, source_file=path, doctype=template.doctype,
                       confidence=1.0, vlm_calls=1, result=res)


def ingest_documents(paths, schema: TargetSchema | None = None, *,
                     template: str | DocTemplate | None = None,
                     backend: str = "vlm", model: str = "gpt-4o",
                     extractor: Extractor | None = None,
                     template_extractor: TemplateExtractor | None = None,
                     auto_classify: bool = True, classify_threshold: float = 0.6,
                     drop_empty: bool = True, return_report: bool = False):
    """Extract records from a pile of documents into a header DataFrame (entities).

    Three explicit modes:
      * `schema=` -> flat generic extraction (each row is a header entity).
      * `template=` (doctype str or `DocTemplate`) -> structured extraction into a
        header record + linked line-item records (`report.line_items`).
      * neither, `auto_classify=True` (default) -> classify each doc then route
        (Task 6, not yet wired -- raises `NotImplementedError`).

    Hand the header frame to the ER pipeline as:
        dedupe_df(df, exclude_columns=DOC_SIDECARS)
    so provenance/`_doc_id`/`_doctype` sidecars are never treated as match fields.
    Line items link back to their header via the `_doc_id` foreign key.

    `extractor`/`template_extractor` override `backend`/`model` (tests / custom
    backends). Returns the header DataFrame, or `(df, IngestReport)` when
    `return_report=True`.
    """
    if schema is not None and template is not None:
        raise ValueError("pass schema= OR template=, not both")
    if schema is None and template is None:
        raise NotImplementedError(
            "auto-classify flow lands in Task 6; pass schema= (flat) or "
            "template= (pinned doctype) for now"
        )

    tmpl = _resolve_template(template) if template is not None else None
    if schema is not None:
        extractor = extractor or resolve_extractor(backend, model)
    elif template_extractor is None:  # pinned template, no injected extractor
        _, template_extractor, _ = resolve_structured(backend, model)

    # De-dup paths (last occurrence wins) before the loop -- avoids a duplicate VLM
    # call for the same file; assemble is also last-wins-safe on the resulting doc_id.
    files = [str(Path(p)) for p in paths]
    files = list(dict.fromkeys(files[::-1]))[::-1]

    outcomes = [
        _ingest_one(f, schema=schema, template=tmpl, extractor=extractor,
                    template_extractor=template_extractor)
        for f in files
    ]

    df, report = assemble_structured(outcomes, drop_empty=drop_empty)
    # vlm_calls is the TOTAL transport cost across every doc (incl. errored/dropped
    # ones -- the call still went out). classify_confidence is keyed by
    # assemble_structured on exactly the docs that emitted a header row (same
    # key-set as report.doctypes), so it is NOT rebuilt here.
    report.vlm_calls = sum(o.vlm_calls for o in outcomes)
    return (df, report) if return_report else df
