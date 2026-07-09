"""Document/image ingest: turn PDFs/images into a records DataFrame for GoldenMatch."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from goldenmatch.core._hashing import record_fingerprint
from goldenmatch.documents.assemble import DOC_SIDECARS, assemble_structured
from goldenmatch.documents.config import resolve_extractor, resolve_structured
from goldenmatch.documents.extractor import (
    Classifier,
    Extractor,
    FallbackExtractor,
    TemplateExtractor,
)
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


def _flat_extract(extractor: Extractor, pages, schema: TargetSchema, path: str) -> ExtractResult:
    try:
        res = extractor.extract(pages, schema)
    except Exception as e:
        res = ExtractResult(rows=[], error=f"{type(e).__name__}: {e}")
    # stamp the real filename onto each row (backend only knew the page index)
    return ExtractResult(rows=[replace(r, source_file=path) for r in res.rows],
                         error=res.error)


def _generic_fallback(fallback: FallbackExtractor, pages, path: str) -> ExtractResult:
    """The generic path: suggest a schema then extract (2 VLM calls behind the
    seam). Wraps any failure into `ExtractResult(error=...)` so the batch continues."""
    try:
        res = fallback.suggest_and_extract(pages)
    except Exception as e:
        res = ExtractResult(rows=[], error=f"{type(e).__name__}: {e}")
    return ExtractResult(rows=[replace(r, source_file=path) for r in res.rows],
                         error=res.error)


def _structured_extract(template_extractor: TemplateExtractor, pages,
                        template: DocTemplate) -> StructuredResult:
    try:
        return template_extractor.extract_structured(pages, template)
    except Exception as e:  # transport/parse hard failure -> recorded
        return StructuredResult(header=None, line_items=[], error=f"{type(e).__name__}: {e}")


def _ingest_one(path: str, *, schema: TargetSchema | None, template: DocTemplate | None,
                extractor: Extractor | None, classifier: Classifier | None,
                template_extractor: TemplateExtractor | None,
                fallback_extractor: FallbackExtractor | None,
                classify_threshold: float) -> _DocOutcome:
    """Per-doc dispatch. Computes the stable `_doc_id` from the path (NOT header
    values, so re-runs are idempotent), loads pages, then routes:
      * flat `schema=`  -> generic extract, 1 VLM call, confidence 1.0
      * pinned `template=` -> structured extract, 1 VLM call, confidence 1.0
      * auto: classify (1 call) then route on confidence:
          - hit  (>= threshold and doctype has a template) -> structured, 2 calls
          - miss (low conf / no template / classify raised) -> generic fallback,
            3 calls (classify + suggest + extract; the classify call counts even
            when it raised, since the request went out).
    `vlm_calls` = the number of transport calls actually issued for this doc."""
    doc_id = record_fingerprint({"path": path})
    try:
        pages = load_pages(path)
    except Exception as e:  # loader failure -> recorded, batch continues (0 VLM calls)
        return _DocOutcome(doc_id=doc_id, source_file=path, doctype="generic",
                           confidence=1.0, vlm_calls=0,
                           result=ExtractResult(rows=[], error=f"{type(e).__name__}: {e}"))

    if schema is not None:  # flat path
        return _DocOutcome(doc_id=doc_id, source_file=path, doctype="generic",
                           confidence=1.0, vlm_calls=1,
                           result=_flat_extract(extractor, pages, schema, path))

    if template is not None:  # pinned template path
        return _DocOutcome(doc_id=doc_id, source_file=path, doctype=template.doctype,
                           confidence=1.0, vlm_calls=1,
                           result=_structured_extract(template_extractor, pages, template))

    # auto-classify path -------------------------------------------------------
    try:
        cls = classifier.classify(pages)  # 1 transport call
    except Exception:
        # classify request went out but failed to parse/return -> generic fallback.
        # vlm_calls = 1 (classify, issued) + 2 (suggest + extract) = 3. No classifier
        # value survived, so confidence is 0.0.
        return _DocOutcome(doc_id=doc_id, source_file=path, doctype="generic",
                           confidence=0.0, vlm_calls=3,
                           result=_generic_fallback(fallback_extractor, pages, path))

    if cls.confidence >= classify_threshold and cls.doctype in list_templates():
        return _DocOutcome(doc_id=doc_id, source_file=path, doctype=cls.doctype,
                           confidence=cls.confidence, vlm_calls=2,
                           result=_structured_extract(template_extractor, pages,
                                                      get_template(cls.doctype)))

    # low confidence, "generic", or a doctype with no template -> generic fallback.
    # vlm_calls = 1 (classify) + 2 (suggest + extract) = 3. Keep the real classifier
    # confidence so the report reflects why we routed generic.
    return _DocOutcome(doc_id=doc_id, source_file=path, doctype="generic",
                       confidence=cls.confidence, vlm_calls=3,
                       result=_generic_fallback(fallback_extractor, pages, path))


def ingest_documents(paths, schema: TargetSchema | None = None, *,
                     template: str | DocTemplate | None = None,
                     backend: str = "vlm", model: str = "gpt-4o",
                     extractor: Extractor | None = None,
                     classifier: Classifier | None = None,
                     template_extractor: TemplateExtractor | None = None,
                     fallback_extractor: FallbackExtractor | None = None,
                     auto_classify: bool = True, classify_threshold: float = 0.6,
                     drop_empty: bool = True, return_report: bool = False):
    """Extract records from a pile of documents into a header DataFrame (entities).

    Three modes (precedence `schema` > `template` > auto):
      * `schema=` -> flat generic extraction (each row is a header entity).
      * `template=` (doctype str or `DocTemplate`) -> structured extraction into a
        header record + linked line-item records (`report.line_items`).
      * neither, `auto_classify=True` (default) -> classify each doc then route:
        a confident hit on a known doctype extracts against its template; anything
        else falls back to the generic suggest-then-extract path. `report.doctypes`,
        `report.classify_confidence`, and `report.vlm_calls` record what happened.

    Hand the header frame to the ER pipeline as:
        dedupe_df(df, exclude_columns=DOC_SIDECARS)
    so provenance/`_doc_id`/`_doctype` sidecars are never treated as match fields.
    Line items link back to their header via the `_doc_id` foreign key.

    `extractor`/`classifier`/`template_extractor`/`fallback_extractor` override
    `backend`/`model` (tests / custom backends). The auto path resolves the three
    structured collaborators LAZILY -- a flat/pinned call (or one with all auto
    collaborators injected) never needs an API key. Returns the header DataFrame,
    or `(df, IngestReport)` when `return_report=True`.
    """
    if schema is not None and template is not None:
        raise ValueError("pass schema= OR template=, not both")

    tmpl = _resolve_template(template) if template is not None else None
    if schema is not None:  # flat path
        extractor = extractor or resolve_extractor(backend, model)
    elif template is not None:  # pinned template path
        if template_extractor is None:
            _, template_extractor, _ = resolve_structured(backend, model)
    else:  # auto-classify path -- resolve the three collaborators only if needed
        if classifier is None or template_extractor is None or fallback_extractor is None:
            r_cls, r_te, r_fb = resolve_structured(backend, model)
            classifier = classifier or r_cls
            template_extractor = template_extractor or r_te
            fallback_extractor = fallback_extractor or r_fb

    # De-dup paths (last occurrence wins) before the loop -- avoids a duplicate VLM
    # call for the same file; assemble is also last-wins-safe on the resulting doc_id.
    files = [str(Path(p)) for p in paths]
    files = list(dict.fromkeys(files[::-1]))[::-1]

    outcomes = [
        _ingest_one(f, schema=schema, template=tmpl, extractor=extractor,
                    classifier=classifier, template_extractor=template_extractor,
                    fallback_extractor=fallback_extractor,
                    classify_threshold=classify_threshold)
        for f in files
    ]

    df, report = assemble_structured(outcomes, drop_empty=drop_empty)
    # vlm_calls is the TOTAL transport cost across every doc (incl. errored/dropped
    # ones -- the call still went out). classify_confidence is keyed by
    # assemble_structured on exactly the docs that emitted a header row (same
    # key-set as report.doctypes), so it is NOT rebuilt here.
    report.vlm_calls = sum(o.vlm_calls for o in outcomes)
    return (df, report) if return_report else df
