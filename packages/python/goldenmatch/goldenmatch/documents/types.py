"""Value types for document/image ingest. Stdlib only, offline-testable."""
from __future__ import annotations

import json
from dataclasses import dataclass, field


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
        # coerce non-null values to str: a VLM may return a bare number (phone/zip),
        # and mixed int/str across rows would make pl.DataFrame(records) raise before
        # the downstream cast in assemble. Keep None as None.
        v = {c: (str(values[c]) if values.get(c) is not None else None) for c in cols}
        conf = {c: float(confidence.get(c, 0.0)) for c in cols}
        return cls(values=v, confidence=conf,
                   source_file=source_file, source_page=source_page)

    def row_confidence(self) -> float:
        return min(self.confidence.values()) if self.confidence else 0.0


@dataclass(frozen=True)
class ExtractResult:
    rows: list[ExtractedRow] = field(default_factory=list)
    error: str | None = None


@dataclass
class IngestReport:
    n_files: int = 0
    n_rows: int = 0
    errors: list[tuple[str, str]] = field(default_factory=list)  # (file, message)
