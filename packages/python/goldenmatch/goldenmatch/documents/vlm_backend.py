"""Cloud VLM extractor: one OpenAI vision call per document, schema-directed.

Network I/O is a `transport(payload: dict) -> dict` callable so the class tests offline.
"""
from __future__ import annotations

import base64
import json
from collections.abc import Callable

from goldenmatch.documents.types import (
    ExtractedRow,
    ExtractResult,
    PageImage,
    TargetSchema,
)

_ENDPOINT = "https://api.openai.com/v1/chat/completions"
Transport = Callable[[dict], dict]


def _urllib_transport(api_key: str) -> Transport:
    import urllib.request

    def send(payload: dict) -> dict:
        body = json.dumps(payload).encode()
        req = urllib.request.Request(
            _ENDPOINT, data=body,
            headers={"Authorization": f"Bearer {api_key}",
                     "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read())

    return send


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
                 transport: Transport | None = None, max_retries: int = 2):
        self._model = model
        self._max_retries = max_retries
        self._send = transport or _urllib_transport(api_key)

    def _payload(self, pages: list[PageImage], schema: TargetSchema) -> dict:
        content: list[dict] = [{"type": "text", "text": _instruction(schema)}]
        for pg in pages:
            b64 = base64.b64encode(pg.png_bytes).decode()
            content.append({"type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{b64}"}})
        return {"model": self._model, "temperature": 0, "max_tokens": 2000,
                "messages": [{"role": "user", "content": content}]}

    def extract(self, pages: list[PageImage], schema: TargetSchema) -> ExtractResult:
        payload = self._payload(pages, schema)
        src = pages[0].index if pages else None
        fname = ""  # assemble tags the real filename; backend only knows the page
        last_err = "no response"
        for _ in range(self._max_retries):
            try:
                resp = self._send(payload)
                text = resp["choices"][0]["message"]["content"]
                data = json.loads(_strip_fence(text))
                rows = [
                    ExtractedRow.from_partial(
                        rec.get("values", {}), rec.get("confidence", {}), schema,
                        source_file=fname, source_page=src)
                    for rec in data.get("records", [])
                ]
                return ExtractResult(rows=rows)
            except (KeyError, ValueError, TypeError) as e:
                last_err = f"{type(e).__name__}: {e}"
        return ExtractResult(rows=[], error=last_err)


def _strip_fence(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        if t.endswith("```"):
            t = t[: -3]
    return t.strip()
