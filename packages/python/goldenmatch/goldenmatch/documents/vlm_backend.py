"""Cloud VLM extractor: one OpenAI vision call per document, schema-directed.

Network I/O is a `transport(payload: dict) -> dict` callable so the class tests offline.
"""
from __future__ import annotations

import json

from goldenmatch.documents._openai import Transport, image_blocks, urllib_transport
from goldenmatch.documents.types import (
    ExtractedRow,
    ExtractResult,
    PageImage,
    TargetSchema,
)


def _instruction(schema: TargetSchema) -> str:
    lines = [f'- "{f.name}" ({f.kind})' + (f": {f.hint}" if f.hint else "")
             for f in schema.fields]
    cols = ", ".join(schema.column_names())
    return (
        "Extract every record present in the attached document image(s).\n"
        "A form/card/ID is ONE record; a table/list is MANY records (one per row).\n"
        "Target fields:\n" + "\n".join(lines) + "\n\n"
        "Return ONLY a JSON object of the form:\n"
        '{"records": [{"values": {<field>: <string or null>, ...}, '
        '"confidence": {<field>: <0..1>, ...}}, ...]}\n'
        f"Use exactly these field keys: {cols}. Omit a field if absent. No prose."
    )


class VLMExtractor:
    def __init__(self, *, api_key: str, model: str = "gpt-4o",
                 transport: Transport | None = None, max_attempts: int = 3):
        if max_attempts < 1:
            raise ValueError(f"max_attempts must be >= 1, got {max_attempts}")
        self._model = model
        self._max_attempts = max_attempts
        self._send = transport or urllib_transport(api_key)

    def _payload(self, pages: list[PageImage], schema: TargetSchema) -> dict:
        content = [{"type": "text", "text": _instruction(schema)}] + image_blocks(pages)
        return {"model": self._model, "temperature": 0, "max_tokens": 8000,
                "messages": [{"role": "user", "content": content}]}

    def extract(self, pages: list[PageImage], schema: TargetSchema) -> ExtractResult:
        payload = self._payload(pages, schema)
        src = pages[0].index if pages else None
        fname = ""  # assemble tags the real filename; backend only knows the page
        last_err = "no response"
        resp = None
        for _ in range(self._max_attempts):
            try:
                resp = self._send(payload)
                break
            except Exception as e:  # transport/network error: retry (non-deterministic)
                last_err = f"{type(e).__name__}: {e}"
        else:
            return ExtractResult(rows=[], error=last_err)

        # Response received: parsing is deterministic (temperature=0), so a parse
        # failure is not retried -- it would just reproduce the same bad response.
        try:
            choice = resp["choices"][0]
            if choice.get("finish_reason") == "length":
                return ExtractResult(
                    rows=[],
                    error="response truncated (finish_reason=length); increase max_tokens")
            text = choice["message"]["content"]
            data = json.loads(_strip_fence(text))
            rows = [
                ExtractedRow.from_partial(
                    rec.get("values", {}), rec.get("confidence", {}), schema,
                    source_file=fname, source_page=src)
                for rec in data.get("records", [])
            ]
            return ExtractResult(rows=rows)
        except (KeyError, ValueError, TypeError, IndexError) as e:
            return ExtractResult(rows=[], error=f"parse: {type(e).__name__}: {e}")


def _strip_fence(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        if t.endswith("```"):
            t = t[: -3]
    return t.strip()
