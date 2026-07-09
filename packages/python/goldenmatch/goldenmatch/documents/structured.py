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

from goldenmatch.documents._openai import (
    Transport,
    image_blocks,
    parse_message_text,
)
from goldenmatch.documents.templates import _template_to_dict
from goldenmatch.documents.types import (
    DocTemplate,
    ExtractedRow,
    PageImage,
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


def _structured_instruction(template: DocTemplate) -> str:
    """Build the structured-extract instruction for a template. Python-only (its
    OUTPUT is NOT parity-locked -- only `parse_structured` is), so a plain f-string
    is fine; it names the header + line-item fields so the VLM returns the
    `{"header": {...}, "line_items": [...]}` shape the parser expects."""
    def _lines(schema) -> str:
        return "\n".join(
            f'- "{f.name}" ({f.kind})' + (f": {f.hint}" if f.hint else "")
            for f in schema.fields
        )

    header_cols = ", ".join(template.header.column_names())
    body = (
        f"Extract the {template.doctype} in the attached document image(s) into a "
        "single header record plus its line items.\n"
        "Header fields:\n" + _lines(template.header) + "\n"
    )
    if template.line_items.fields:
        item_cols = ", ".join(template.line_items.column_names())
        body += (
            "Line-item fields (one object per row in the document's line-item table):\n"
            + _lines(template.line_items) + "\n\n"
            'Return ONLY a JSON object of the form:\n'
            '{"header": {"values": {<field>: <string or null>, ...}, '
            '"confidence": {<field>: <0..1>, ...}}, '
            '"line_items": [{"values": {<field>: ...}, "confidence": {...}}, ...]}\n'
            f"Header keys: {header_cols}. Line-item keys: {item_cols}. "
            "Omit a field if absent. No prose."
        )
    else:
        body += (
            "\nReturn ONLY a JSON object of the form:\n"
            '{"header": {"values": {<field>: <string or null>, ...}, '
            '"confidence": {<field>: <0..1>, ...}}, "line_items": []}\n'
            f"Header keys: {header_cols}. Omit a field if absent. No prose."
        )
    return body


class VLMTemplateExtractor:
    """One OpenAI vision call per document, template-directed: extract a header
    record + line items against a `DocTemplate`. Network I/O is an injectable
    `transport` so the class tests offline. Batch-continues: transport/parse
    failures return `StructuredResult(error=...)` rather than raising."""

    def __init__(self, *, transport: Transport, model: str = "gpt-4o",
                 max_attempts: int = 3):
        if max_attempts < 1:
            raise ValueError(f"max_attempts must be >= 1, got {max_attempts}")
        self._send = transport
        self._model = model
        self._max_attempts = max_attempts

    def _payload(self, pages: list[PageImage], template: DocTemplate) -> dict:
        content = [{"type": "text", "text": _structured_instruction(template)}] + image_blocks(pages)
        return {"model": self._model, "temperature": 0, "max_tokens": 8000,
                "messages": [{"role": "user", "content": content}]}

    def extract_structured(
        self, pages: list[PageImage], template: DocTemplate
    ) -> StructuredResult:
        payload = self._payload(pages, template)
        last_err = "no response"
        resp = None
        for _ in range(self._max_attempts):
            try:
                resp = self._send(payload)
                break
            except Exception as e:  # transport/network error: retry (non-deterministic)
                last_err = f"{type(e).__name__}: {e}"
        else:
            return StructuredResult(header=None, line_items=[], error=last_err)

        # Response received: parsing is deterministic (temperature=0), so a parse
        # failure is not retried. parse_structured already WRAPS malformed
        # responses into StructuredResult(error=...) -- never raises.
        try:
            text = parse_message_text(resp)
        except ValueError as e:
            return StructuredResult(header=None, line_items=[], error=str(e))
        return parse_structured(text, template)
