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
"value": "<literal value>", \
"type": "date|quantity|ordinal|range|region|event|text"}}]}}
`relationships` connect two ENTITIES. `attributes` attach a LITERAL VALUE to an \
entity -- use them for any answer-bearing value that is NOT itself a named entity. \
Pick the closest `type`: `date` (e.g. "11 February 1929"), `quantity` (counts, \
money, measurements, e.g. "2" or "$3M"), `ordinal` (ranks/positions, e.g. \
"third-largest"), `range` (spans, e.g. "551-600" or "upper 40s-lower 50s F"), \
`region` (a place qualified by a sub-area, e.g. "northeastern Oklahoma"), `event` \
(a named occurrence, e.g. "the 2010 election"), or `text` for anything else. Do \
NOT put such a value in `entities`. The `description` disambiguates an entity for \
resolution. `subj`/`obj` are 0-based indices into `entities`. Text:
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


# Typed literal-leaf kinds. The original date/quantity/text set is broadened with
# ordinal/range/region/event (the "phrase-span Part 1" lever) so the qualified-value
# golds the entity-only graph drops -- ranks, ranges, sub-regions, events -- become
# typed leaf nodes. Every kind lives under the `literal:<kind>` namespace, so each
# inherits the existing isolation for free: excluded from query-seeding and from the
# cross-doc link candidate set, quoted as a value leaf in synthesis. An unknown type
# still coerces to `text`.
_ATTR_TYPES = ("date", "quantity", "ordinal", "range", "region", "event", "text")


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


#: Prepended when a closed relation schema is known -- the highest-ROI fix for weak models, which
#: get entities + edges right but SEMANTICALLY PARAPHRASE the predicate (measured: relation-F1 edge
#: 0.81 vs predicate-exact 0.30 on qwen-7B). The multi-hop questions are predicate-specific, so a
#: wrong label breaks the trace. Constraining to the canonical vocabulary fixes it without training.
_RELATION_VOCAB_INSTRUCTION = (
    "IMPORTANT: for every relationship, set `predicate` to EXACTLY ONE label, verbatim, from this "
    "closed set -- do NOT paraphrase, pluralize, or invent labels: [{vocab}]. If none of these "
    "relations holds between two entities, OMIT that relationship. "
    "DIRECTION MATTERS: `subj` is the entity that the relation acts FROM (the grammatical subject, "
    "stated FIRST), `obj` is the entity it acts ON (stated second). For 'A works_at B', subj=A, "
    "obj=B. Never invert subject and object.\n\n"
)


#: Prepended when the coarse entity-type vocab is enforced (GOLDENGRAPH_ENTITY_TYPE_CANON). An open-vocab
#: 7B invents a fresh `type` per document for the same entity (measured: 97.6% of cross-doc fragmentation
#: is type jitter), which shatters the (name,type) cross-doc key. Constraining to a small closed set makes
#: the type CONSISTENT across docs while still separating homograph classes.
_ENTITY_TYPE_VOCAB_INSTRUCTION = (
    "Every entity's `type` MUST be exactly one of: {vocab}. "
    "Pick the single closest; do not invent other type labels.\n\n"
)


def _relation_vocab(explicit) -> tuple[str, ...]:
    """The closed relation vocabulary: explicit arg, else the comma-separated
    `GOLDENGRAPH_RELATION_VOCAB` env, else empty (open extraction, unchanged)."""
    if explicit is not None:
        return tuple(explicit)
    raw = os.environ.get("GOLDENGRAPH_RELATION_VOCAB", "")
    return tuple(v.strip() for v in raw.split(",") if v.strip())


def extract(text: str, llm: LLMClient, *, literals: bool | None = None,
            relation_vocab=None) -> Extraction:
    """Extract entities + relationships (+ literal attributes when `literals`) from
    `text` via `llm`. `literals=None` (the default) reads the
    `GOLDENGRAPH_LITERAL_ATTRS` flag, so the build's 2-arg call site stays unchanged
    (and monkeypatched test stubs keep their `(text, llm)` shape); pass an explicit
    bool to force it. `relation_vocab` (or `GOLDENGRAPH_RELATION_VOCAB`) constrains
    predicates to a closed schema -- open extraction when absent."""
    if literals is None:
        literals = os.environ.get("GOLDENGRAPH_LITERAL_ATTRS", "0") not in ("0", "false", "")
    prompt = (_PROMPT_LITERALS if literals else _PROMPT).format(text=text)
    vocab = _relation_vocab(relation_vocab)
    if vocab:
        prompt = _RELATION_VOCAB_INSTRUCTION.format(vocab=", ".join(vocab)) + prompt
    from .schema import entity_type_canon_enabled, entity_type_vocab
    if entity_type_canon_enabled():
        prompt = _ENTITY_TYPE_VOCAB_INSTRUCTION.format(vocab=", ".join(entity_type_vocab())) + prompt
    return parse_extraction(_complete_extraction(llm, prompt))


def _complete_extraction(llm: LLMClient, prompt: str) -> str:
    """Use the JSON-constrained completion when the client supports it (and
    `GOLDENGRAPH_EXTRACT_JSON_MODE` != 0) -- forcing valid JSON is the highest-ROI
    lever for weak/OSS models, which otherwise emit unparseable extraction. Falls
    back to plain `complete` for stubs / clients without `complete_json`."""
    json_mode = os.environ.get("GOLDENGRAPH_EXTRACT_JSON_MODE", "1") not in ("0", "false", "")
    if json_mode and hasattr(llm, "complete_json"):
        return llm.complete_json(prompt)
    return llm.complete(prompt)
