"""VLM-assisted target-schema suggestion: look at a sample document and PROPOSE fields.
Distinct prompt from vlm_backend (which extracts against a KNOWN schema); shares only the
transport + image encoding."""
from __future__ import annotations

import json

from goldenmatch.documents._openai import (
    Transport,
    image_blocks,
    parse_message_text,
    urllib_transport,
)
from goldenmatch.documents.config import resolve_api_key
from goldenmatch.documents.loader import load_pages
from goldenmatch.documents.schema_io import schema_from_dict
from goldenmatch.documents.types import PageImage, TargetSchema

_PROMPT = (
    "You are shown a sample document. Propose a compact extraction schema: the fields a "
    "person would want pulled from documents like this for record matching (names, "
    "emails, addresses, phones, ids, dates...). Return ONLY JSON:\n"
    '{"fields": [{"name": "<snake_case>", "kind": "text|email|phone|address|date|number", '
    '"hint": "<short guidance>"}, ...]}\n'
    "Prefer 3-12 stable, matchable fields. No prose."
)


def suggest_schema(pages: list[PageImage], *, transport: Transport,
                   model: str = "gpt-4o", max_attempts: int = 3) -> TargetSchema:
    payload = {"model": model, "temperature": 0, "max_tokens": 1500,
               "messages": [{"role": "user",
                             "content": [{"type": "text", "text": _PROMPT}] + image_blocks(pages)}]}
    last = "no response"
    resp = None
    for _ in range(max(1, max_attempts)):
        try:
            resp = transport(payload)
            break
        except Exception as e:  # transport/network: retry
            last = f"{type(e).__name__}: {e}"
    else:
        raise ValueError(f"schema suggestion failed: {last}")
    text = parse_message_text(resp)
    return schema_from_dict(json.loads(text))  # raises ValueError on empty/malformed


def suggest_schema_from_file(path, *, backend: str = "vlm", model: str = "gpt-4o") -> TargetSchema:
    if backend != "vlm":
        raise ValueError(f"unsupported backend: {backend!r} (only 'vlm' is supported)")
    return suggest_schema(load_pages(path), transport=urllib_transport(resolve_api_key()),
                          model=model)
