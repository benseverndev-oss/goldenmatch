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

#: Appended to `_PROMPT` (before the `Text:` line) when GOLDENGRAPH_LITERAL_ATTRS is
#: on. Many questions ask for a LITERAL value (a date, a quantity, a measurement, a
#: short descriptive phrase) that is NOT a named entity -- "when was X founded?",
#: "how many people?", "what kind of engine?". The base schema can only emit
#: entity->entity edges, so those answers never enter the graph and are
#: unanswerable by construction. This adds an `attributes` array carrying each such
#: literal fact as (entity, key, value); the host materializes every attribute as a
#: literal NODE plus an edge from its entity, so the value becomes a reachable,
#: answerable graph node.
_ATTRS_SCHEMA = """ Additionally extract an `attributes` array capturing LITERAL \
facts -- a date/year, a quantity/amount/measurement, or a short defining value -- \
that answers a "when / how many / how much / what kind / how long" question about \
an entity but is NOT itself a named entity: \
{{"attributes": [{{"subj": <entity index>, "key": "<what the value is, e.g. \
'release date', 'population', 'length'>", "value": "<the literal value EXACTLY as \
stated, e.g. 'May 1990', '$72,641', '4.3 km'>"}}]}}. \
`subj` is a 0-based index into `entities`. Keep `value` terse and verbatim; omit \
attributes you are unsure of."""


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
    """A literal fact about an entity: `mentions[subj]` has `key` = `value`, where
    `value` is a date/quantity/short defining phrase, NOT a named entity. The host
    materializes each as a literal node + an edge so the value is answerable."""

    subj: int  # index into the Extraction.mentions list
    key: str
    value: str


@dataclass
class Extraction:
    mentions: list[Mention]
    relationships: list[Relationship]
    # Literal attribute facts (default empty -> back-compat with the base schema and
    # any extractor/stub that doesn't emit them).
    attributes: list[Attribute] = field(default_factory=list)


def _literal_attrs_enabled() -> bool:
    """Read inside `extract()` (NOT a call-site kwarg) so monkeypatched 2-arg
    extractor stubs keep working -- the #1236 lesson."""
    return os.environ.get("GOLDENGRAPH_LITERAL_ATTRS", "0") not in ("0", "false", "")


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
        s = a.get("subj")
        value = str(a.get("value", "")).strip()
        # defensive: a valid owner index + a non-empty literal value (an empty value
        # would materialize a useless blank node).
        if isinstance(s, int) and 0 <= s < n and value:
            attrs.append(Attribute(subj=s, key=str(a.get("key", "")).strip(), value=value))
    return Extraction(mentions=mentions, relationships=rels, attributes=attrs)


def extract(text: str, llm: LLMClient) -> Extraction:
    """Extract entities + relationships from `text` via `llm`. When
    GOLDENGRAPH_LITERAL_ATTRS is set, also extract literal attribute facts
    (dates/quantities/short defining values) so non-entity answers can enter the
    graph (see `_ATTRS_SCHEMA`)."""
    prompt = _PROMPT
    if _literal_attrs_enabled():
        # Splice the attribute schema in BEFORE the trailing "Text:\n{text}" so the
        # instruction precedes the document.
        head, _, tail = _PROMPT.partition(" Text:\n{text}")
        prompt = head + _ATTRS_SCHEMA + " Text:\n{text}"
    return parse_extraction(llm.complete(prompt.format(text=text)))
