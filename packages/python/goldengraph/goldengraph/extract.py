"""LLM extraction: text -> entities + relationships (typed triples).

This is genuinely new logic — goldenmatch has `llm_extract_features` (ER feature
extraction), not a text->triples extractor. Only the LLM transport (`LLMClient`)
and (optionally) `BudgetTracker` are reused. The extractor is defensive about LLM
drift: malformed JSON raises; out-of-range / non-int relationship endpoints are
dropped rather than poisoning the graph.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from .llm import LLMClient

_PROMPT = """You extract a knowledge graph from text. Return STRICT JSON only, \
no prose, in exactly this shape:
{{"entities": [{{"name": "<surface name>", "type": "<coarse type>", \
"description": "<one short factual phrase describing the entity>"}}], \
"relationships": [{{"subj": <entity index>, "predicate": "<verb phrase>", \
"obj": <entity index>}}]}}
The `description` is a brief, source-grounded characterization (e.g. "American \
technology corporation") that disambiguates the entity for resolution. \
`subj`/`obj` are 0-based indices into `entities`. Text:
{text}"""

# Literals-aware variant (GOLDENGRAPH_LITERAL_ATTRS): also captures attribute
# VALUES (dates, quantities, amounts) that are not themselves entities, so the
# graph can answer "when/how much" questions an entity-only graph drops.
_PROMPT_LITERALS = """You extract a knowledge graph from text. Return STRICT JSON \
only, no prose, in exactly this shape:
{{"entities": [{{"name": "<surface name>", "type": "<coarse type>", \
"description": "<one short factual phrase describing the entity>"}}], \
"relationships": [{{"subj": <entity index>, "predicate": "<verb phrase>", \
"obj": <entity index>}}], \
"attributes": [{{"subj": <entity index>, "predicate": "<attribute phrase>", \
"value": "<literal value>", "type": "date|quantity|text"}}]}}
`relationships` connect two ENTITIES. `attributes` attach a LITERAL VALUE to an \
entity -- use them for dates, quantities, money amounts, measurements, and other \
values that are NOT themselves entities (e.g. {{"subj": 0, "predicate": "born on", \
"value": "11 February 1929", "type": "date"}}). Do NOT put a date/number/amount in \
`entities`. The `description` disambiguates an entity for resolution. \
`subj`/`obj` are 0-based indices into `entities`. Text:
{text}"""


@dataclass
class Mention:
    name: str
    typ: str
    context: str = ""  # short description; sharpens resolution (optional, default "")


@dataclass
class Relationship:
    subj: int  # index into the Extraction.mentions list
    predicate: str
    obj: int


@dataclass
class Attribute:
    """A literal VALUE attached to an entity (entity -[predicate]-> "value").
    `subj` indexes Extraction.mentions; `value` is a literal, not an entity."""
    subj: int
    predicate: str
    value: str
    typ: str = "text"  # date | quantity | text


@dataclass
class Extraction:
    mentions: list[Mention]
    relationships: list[Relationship]
    attributes: list[Attribute] = field(default_factory=list)


def _strip_fence(raw: str) -> str:
    """Strip a leading ```json / ``` fence if the model wrapped its output."""
    s = raw.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s[3:]
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    return s.strip()


_ATTR_TYPES = ("date", "quantity", "text")


def parse_extraction(raw: str) -> Extraction:
    data = json.loads(_strip_fence(raw))
    mentions = [
        Mention(
            name=str(e["name"]),
            typ=str(e.get("type", e.get("typ", ""))),
            context=str(e.get("description", e.get("context", ""))),
        )
        for e in data.get("entities", [])
    ]
    n = len(mentions)
    rels: list[Relationship] = []
    for r in data.get("relationships", []):
        s, o = r.get("subj"), r.get("obj")
        # defensive: drop endpoints that aren't valid entity indices
        if isinstance(s, int) and isinstance(o, int) and 0 <= s < n and 0 <= o < n:
            rels.append(Relationship(subj=s, predicate=str(r.get("predicate", "")), obj=o))
    attrs: list[Attribute] = []
    for a in data.get("attributes", []):
        s, v = a.get("subj"), a.get("value")
        # defensive: valid subject entity index + a non-empty scalar value
        if not (isinstance(s, int) and 0 <= s < n):
            continue
        if isinstance(v, bool) or not isinstance(v, (str, int, float)):
            continue
        val = str(v).strip()
        if not val:
            continue
        typ = str(a.get("type", "text")).lower()
        attrs.append(
            Attribute(
                subj=s,
                predicate=str(a.get("predicate", "")),
                value=val,
                typ=typ if typ in _ATTR_TYPES else "text",
            )
        )
    return Extraction(mentions=mentions, relationships=rels, attributes=attrs)


def extract(text: str, llm: LLMClient, *, literals: bool | None = None) -> Extraction:
    """Extract entities + relationships (+ literal attributes when `literals`) from
    `text` via `llm`. `literals=None` (the default) reads the
    `GOLDENGRAPH_LITERAL_ATTRS` flag, so the build's 2-arg call site stays unchanged
    (and monkeypatched test stubs keep their `(text, llm)` shape); pass an explicit
    bool to force it."""
    if literals is None:
        literals = os.environ.get("GOLDENGRAPH_LITERAL_ATTRS", "0") not in ("0", "false", "")
    prompt = _PROMPT_LITERALS if literals else _PROMPT
    return parse_extraction(llm.complete(prompt.format(text=text)))
