"""Real-Wikipedia-prose substrate corpus (level 2): wikilink parser + committed-snapshot loader."""
from __future__ import annotations

import json
import re
from pathlib import Path

#: [[Target]] or [[Target|Surface]]; group1=target, group2=optional surface. No nested brackets/pipes.
_WIKILINK = re.compile(r"\[\[([^\[\]|]+)(?:\|([^\[\]|]+))?\]\]")


def parse_wikilinks(wikitext: str) -> list[tuple[str, str]]:
    """(surface, target_title) per article-namespace wikilink. Skips File:/Category:/interwiki (`:` in
    target) and section-only links; strips a `#section` anchor from the target."""
    out: list[tuple[str, str]] = []
    for m in _WIKILINK.finditer(wikitext):
        target = m.group(1).strip()
        surface = (m.group(2) or m.group(1)).strip()
        if not target or target.startswith("#") or ":" in target:
            continue
        target = target.split("#", 1)[0].strip()
        if target and surface:
            out.append((surface, target))
    return out


def load_wiki_corpus(path: str | Path | None = None):
    """Read the committed `wiki_corpus.jsonl` -> (documents, gold_mentions). `documents` reuse
    `corpora.Document` (id/text; surface fields unused); `gold_mentions` = `(Target_QID, Surface, doc_id)`
    flattened across articles. Pure / no network."""
    from .corpora import Document

    p = Path(path) if path else Path(__file__).resolve().parents[2] / "dataset" / "wiki_corpus.jsonl"
    docs: list[Document] = []
    gold: list[tuple[str, str, str]] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        docs.append(Document(id=rec["doc_id"], text=rec["text"]))
        for qid, surface in rec.get("gold", []):
            gold.append((qid, surface, rec["doc_id"]))
    return docs, gold
