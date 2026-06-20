"""LLM extraction: text -> entities + relationships (typed triples).

This is genuinely new logic — goldenmatch has `llm_extract_features` (ER feature
extraction), not a text->triples extractor. Only the LLM transport (`LLMClient`)
and (optionally) `BudgetTracker` are reused. The extractor is defensive about LLM
drift: malformed JSON raises; out-of-range / non-int relationship endpoints are
dropped rather than poisoning the graph.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from .llm import LLMClient

_PROMPT = """You extract a knowledge graph from text. Return STRICT JSON only, \
no prose, in exactly this shape:
{{"entities": [{{"name": "<surface name>", "type": "<coarse type>"}}], \
"relationships": [{{"subj": <entity index>, "predicate": "<verb phrase>", \
"obj": <entity index>}}]}}
`subj`/`obj` are 0-based indices into `entities`. Text:
{text}"""


@dataclass
class Mention:
    name: str
    typ: str


@dataclass
class Relationship:
    subj: int  # index into the Extraction.mentions list
    predicate: str
    obj: int


@dataclass
class Extraction:
    mentions: list[Mention]
    relationships: list[Relationship]


def _strip_fence(raw: str) -> str:
    """Strip a leading ```json / ``` fence if the model wrapped its output."""
    s = raw.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s[3:]
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    return s.strip()


def parse_extraction(raw: str) -> Extraction:
    data = json.loads(_strip_fence(raw))
    mentions = [
        Mention(name=str(e["name"]), typ=str(e.get("type", e.get("typ", ""))))
        for e in data.get("entities", [])
    ]
    n = len(mentions)
    rels: list[Relationship] = []
    for r in data.get("relationships", []):
        s, o = r.get("subj"), r.get("obj")
        # defensive: drop endpoints that aren't valid entity indices
        if isinstance(s, int) and isinstance(o, int) and 0 <= s < n and 0 <= o < n:
            rels.append(Relationship(subj=s, predicate=str(r.get("predicate", "")), obj=o))
    return Extraction(mentions=mentions, relationships=rels)


def extract(text: str, llm: LLMClient) -> Extraction:
    """Extract entities + relationships from `text` via `llm`."""
    return parse_extraction(llm.complete(_PROMPT.format(text=text)))
