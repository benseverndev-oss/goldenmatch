"""Structured-extract parse: turn a VLM structured response
`{"header": {...}, "line_items": [{...}, ...]}` into a normalized header row +
per-line-item rows, against a `DocTemplate`.

Native (documents-core `documents_parse_structured`) is the source of truth; the
pure path below is the lossy fallback + the thing the parity corpus guards.
`parse_structured` WRAPS malformed responses into `StructuredResult(error=...)`
(the flow records them in `report.errors`); the pure-only inner
`_pure_structured_parsed` RAISES, mirroring the Rust kernel's `Err`, and is what
the parity harness replays.
"""
from __future__ import annotations

import json

from goldenmatch.documents.templates import _template_to_dict
from goldenmatch.documents.types import (
    DocTemplate,
    ExtractedRow,
    StructuredResult,
    normalize_row_pure,
)


def _record_parts(obj: object) -> tuple[dict, dict]:
    """Split a response record into (values, confidence), mirroring the flat
    extractor's record shape. Wrapped `{"values":..,"confidence":..}` when a
    `values` dict is present (confidence defaults to `{}` when absent/non-dict);
    otherwise the object itself is the bare `{field: value}` values map. A
    non-dict collapses to two empty maps. Byte-identical to Rust `record_parts`."""
    if isinstance(obj, dict):
        v = obj.get("values")
        if isinstance(v, dict):
            c = obj.get("confidence")
            return v, (c if isinstance(c, dict) else {})
        return obj, {}
    return {}, {}


def _pure_structured_parsed(text: str, template: DocTemplate) -> tuple[dict, dict, list[tuple[dict, dict]]]:
    """Pure-only parse. RAISES ValueError on malformed input (missing/absent
    header, invalid JSON), mirroring the Rust kernel's `Err`. Coercion routes
    through the shared `normalize_row_pure` (single source of truth vs Rust). Used
    by the parity harness and by `parse_structured`'s pure branch."""
    obj = json.loads(text)  # json.JSONDecodeError is a ValueError subclass
    if not isinstance(obj, dict) or "header" not in obj:
        raise ValueError("structured response missing 'header'")
    hv, hc = _record_parts(obj["header"])
    header_v, header_c = normalize_row_pure(hv, hc, template.header)

    items: list[tuple[dict, dict]] = []
    # Flat doctypes (empty line_item fields, e.g. receipt) force [] regardless.
    if template.line_items.fields:
        raw = obj.get("line_items")
        if isinstance(raw, list):
            for it in raw:
                iv, ic = _record_parts(it)
                items.append(normalize_row_pure(iv, ic, template.line_items))
    return header_v, header_c, items


def _native_structured_parsed(
    nm, text: str, template: DocTemplate
) -> tuple[dict, dict, list[tuple[dict, dict]]]:
    """Native parse via `documents_parse_structured`. RAISES ValueError on
    malformed input. Column order re-imposed from the template (the native map is
    alphabetical -- see normalize.rs)."""
    out = json.loads(nm.documents_parse_structured(text, json.dumps(_template_to_dict(template))))
    hcols = template.header.column_names()
    icols = template.line_items.column_names()
    hv = {c: out["header"]["values"].get(c) for c in hcols}
    hc = {c: float(out["header"]["confidence"].get(c, 0.0)) for c in hcols}
    items = [
        (
            {c: it["values"].get(c) for c in icols},
            {c: float(it["confidence"].get(c, 0.0)) for c in icols},
        )
        for it in out["line_items"]
    ]
    return hv, hc, items


def parse_structured(text: str, template: DocTemplate) -> StructuredResult:
    """Parse a VLM structured response against `template`. Dual-path (native when
    the `documents_parse_structured` symbol is present, else pure). Malformed ->
    `StructuredResult(header=None, line_items=[], error=<msg>)` (WRAPS, does NOT
    raise). source_file/page are placeholders here; the flow stamps them later."""
    from goldenmatch.core._native_loader import native_enabled, native_module

    try:
        if native_enabled("documents") and (nm := native_module()) is not None and hasattr(
            nm, "documents_parse_structured"
        ):
            hv, hc, items = _native_structured_parsed(nm, text, template)
        else:
            hv, hc, items = _pure_structured_parsed(text, template)
    except ValueError as e:
        return StructuredResult(header=None, line_items=[], error=str(e))

    header = ExtractedRow(values=hv, confidence=hc, source_file="", source_page=None)
    line_items = [
        ExtractedRow(values=v, confidence=c, source_file="", source_page=None)
        for v, c in items
    ]
    return StructuredResult(header=header, line_items=line_items, error=None)
