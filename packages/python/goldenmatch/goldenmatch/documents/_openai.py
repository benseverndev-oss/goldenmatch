"""Shared OpenAI-vision plumbing for the documents backends (extractor + schema suggest).
Kept dependency-free (stdlib urllib) and transport-injectable so callers test offline."""
from __future__ import annotations

import base64
import json
from collections.abc import Callable

from goldenmatch.documents.types import PageImage

ENDPOINT = "https://api.openai.com/v1/chat/completions"
Transport = Callable[[dict], dict]


def urllib_transport(api_key: str) -> Transport:
    import urllib.request

    def send(payload: dict) -> dict:
        body = json.dumps(payload).encode()
        req = urllib.request.Request(
            ENDPOINT, data=body,
            headers={"Authorization": f"Bearer {api_key}",
                     "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read())

    return send


def image_blocks(pages: list[PageImage]) -> list[dict]:
    out = []
    for pg in pages:
        b64 = base64.b64encode(pg.png_bytes).decode()
        out.append({"type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{b64}"}})
    return out
