"""Value types for document/image ingest. Stdlib only, offline-testable."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # runtime stays stdlib-only (offline-testable); polars only for typing
    import polars as pl


@dataclass(frozen=True)
class Field:
    name: str
    kind: str = "text"          # text | email | phone | address | date | number
    hint: str | None = None     # natural-language guidance for the VLM


@dataclass(frozen=True)
class TargetSchema:
    fields: list[Field]

    def column_names(self) -> list[str]:
        return [f.name for f in self.fields]


def _coerce_value(v: object) -> str | None:
    """Pure-Python mirror of documents-core `normalize::py_str` (the REFERENCE):
    null/missing -> None; bool -> "True"/"False"; list/dict -> serde-style compact
    JSON (NOT Python repr); everything else -> str(). Kept byte-identical to Rust."""
    if v is None:
        return None
    if isinstance(v, bool):
        return "True" if v else "False"
    if isinstance(v, (list, dict)):
        return json.dumps(v, separators=(",", ":"))
    return str(v)


def _coerce_confidence(c: object) -> float:
    """Pure-Python mirror of documents-core `normalize_record` confidence:
    `as_f64().unwrap_or(0.0)` -- a real number (NOT bool) -> its float; null,
    string, bool, or missing -> 0.0 (coerce, does NOT raise)."""
    if isinstance(c, bool):
        return 0.0
    if isinstance(c, (int, float)):
        return float(c)
    return 0.0


def normalize_row_pure(values: object, confidence: object, schema: TargetSchema
                       ) -> tuple[dict[str, str | None], dict[str, float]]:
    """Pure-only normalize (NEVER dispatches to native). The single source of
    coercion truth shared by `ExtractedRow.from_partial` and the structured parse
    path so they can't re-drift from the Rust reference."""
    cols = schema.column_names()
    vals = values if isinstance(values, dict) else {}
    conf = confidence if isinstance(confidence, dict) else {}
    v = {c: _coerce_value(vals.get(c)) for c in cols}
    cf = {c: _coerce_confidence(conf.get(c)) for c in cols}
    return v, cf


@dataclass(frozen=True)
class DocTemplate:
    doctype: str
    header: TargetSchema
    line_items: TargetSchema      # .fields == [] for flat doctypes (receipt)


@dataclass(frozen=True)
class ClassifyResult:
    doctype: str                # invoice | po | statement | receipt | generic
    confidence: float           # clamped to [0, 1]


@dataclass(frozen=True)
class PageImage:
    png_bytes: bytes            # normalized PNG
    width: int
    height: int
    index: int                  # 0-based page index within the source file


@dataclass(frozen=True)
class ExtractedRow:
    values: dict[str, str | None]
    confidence: dict[str, float]
    source_file: str
    source_page: int | None

    @classmethod
    def from_partial(cls, values, confidence, schema: TargetSchema,
                     *, source_file: str, source_page: int | None) -> ExtractedRow:
        cols = schema.column_names()
        from goldenmatch.core._native_loader import native_enabled, native_module

        if native_enabled("documents") and (nm := native_module()) is not None and hasattr(
            nm, "documents_normalize_record"
        ):
            schema_json = json.dumps(
                {"fields": [{"name": f.name, "kind": f.kind, "hint": f.hint} for f in schema.fields]}
            )
            out = json.loads(
                nm.documents_normalize_record(json.dumps(values), json.dumps(confidence), schema_json)
            )
            # re-impose schema-column order (the Rust map's own order is not
            # authoritative -- see documents-core normalize.rs COLUMN ORDER note).
            pv, pc = out["values"], out["confidence"]
            v = {c: pv.get(c) for c in cols}
            conf = {c: float(pc.get(c, 0.0)) for c in cols}
            return cls(values=v, confidence=conf,
                       source_file=source_file, source_page=source_page)
        # Pure fallback -- routed through the ONE shared coercion helper so it can't
        # drift from the Rust reference (str-coerce scalars incl. bool/int, compact
        # JSON for containers, missing/null value -> None, non-numeric confidence
        # -> 0.0 without raising). A VLM may return a bare number (phone/zip); mixed
        # int/str across rows would make pl.DataFrame(records) raise before the
        # downstream cast in assemble, so scalars are stringified here.
        v, conf = normalize_row_pure(values, confidence, schema)
        return cls(values=v, confidence=conf,
                   source_file=source_file, source_page=source_page)

    def row_confidence(self) -> float:
        return min(self.confidence.values()) if self.confidence else 0.0


@dataclass(frozen=True)
class ExtractResult:
    rows: list[ExtractedRow] = field(default_factory=list)
    error: str | None = None


@dataclass(frozen=True)
class StructuredResult:
    """One structured document: a header row + linked line-item rows. On a
    malformed response the flow RECORDS the error (report.errors) rather than
    raising, so parse helpers WRAP failures here instead of throwing."""
    header: ExtractedRow | None
    line_items: list[ExtractedRow] = field(default_factory=list)
    error: str | None = None


# A single document's extraction is either flat (generic schema) or structured
# (header + line items against a template). The dispatch/assemble layers key on
# this union.
DocResult = ExtractResult | StructuredResult


@dataclass
class _DocOutcome:
    """Per-document dispatch carrier. Carries the FLOW facts (`confidence`,
    `vlm_calls`) that assemble can't derive from the `result` alone, plus the
    stable `doc_id` (path fingerprint) both frames and the report key on."""

    doc_id: str            # record_fingerprint({"path": normalized source_file})
    source_file: str
    doctype: str           # invoice | po | statement | receipt | generic
    confidence: float      # classifier confidence; 1.0 for pinned template / flat schema
    vlm_calls: int         # transport calls this doc cost
    result: DocResult      # ExtractResult (flat) | StructuredResult


@dataclass
class IngestReport:
    n_files: int = 0
    n_rows: int = 0
    errors: list[tuple[str, str]] = field(default_factory=list)  # (file, message)
    line_items: "pl.DataFrame | None" = None                     # child frame, None if none
    doctypes: dict[str, str] = field(default_factory=dict)       # doc_id -> doctype
    classify_confidence: dict[str, float] = field(default_factory=dict)  # doc_id -> conf
    vlm_calls: int = 0                                           # total transport calls
