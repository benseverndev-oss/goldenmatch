"""Relation re-prompt (GOLDENGRAPH_RELATION_REPROMPT): a 2nd extraction pass that, given the
already-extracted entities + full doc text, asks the LLM only for the relations AMONG them.
The edge-miss diagnostic showed the real-prose residual is relation-never-extracted -- entities
correct, edges missing. Narrowing the task (entities provided; only connect them) recovers those
edges. Runs whole-doc over the unioned entity set, so it also targets chunking's cross-window
relation loss. Pure w.r.t. the store; LLM injected; gate off by default."""

from __future__ import annotations

import json
import os

from .extract import (
    _RELATION_VOCAB_INSTRUCTION,
    Mention,
    Relationship,
    _complete_extraction,
    _relation_vocab,
    _strip_fence,
)

_REPROMPT = """Given this text and a numbered list of entities found in it, list every \
relation that holds BETWEEN TWO of these entities, grounded in the text. Return STRICT JSON \
only, no prose, in exactly this shape:
{{"relationships": [{{"subj": <entity number>, "predicate": "<verb phrase>", "obj": <entity number>}}]}}
`subj` and `obj` are numbers from the entity list. Use only relations stated or clearly implied \
by the text. Omit an entity if it has no relation.
Entities:
{entities}
Text:
{text}"""


def relation_reprompt_enabled() -> bool:
    """`GOLDENGRAPH_RELATION_REPROMPT` gate. Off by default; case-insensitive, stripped:
    ""/"0"/"false"/"no"/"off" -> off."""
    return os.environ.get("GOLDENGRAPH_RELATION_REPROMPT", "0").strip().lower() not in (
        "",
        "0",
        "false",
        "no",
        "off",
    )


def _parse_relationships(raw: str, n: int) -> list[Relationship]:
    """Parse a `{"relationships": [...]}` blob into Relationships indexed into a mentions list of
    length `n`. Drops non-int / out-of-range endpoints and self-loops (defensive, like
    parse_extraction). Malformed top-level JSON -> []."""
    data = json.loads(_strip_fence(raw))
    out: list[Relationship] = []
    for r in data.get("relationships", []):
        s, o = r.get("subj"), r.get("obj")
        if isinstance(s, int) and isinstance(o, int) and 0 <= s < n and 0 <= o < n and s != o:
            out.append(Relationship(subj=s, predicate=str(r.get("predicate", "")), obj=o))
    return out


def relation_reprompt(text: str, mentions: list[Mention], llm, *, relation_vocab=None) -> list[Relationship]:
    """Second pass: ask `llm` for the relations among the already-extracted `mentions`, grounded in
    `text`. Returns new Relationships indexed into `mentions` (unchanged). Empty mentions -> [] (no
    LLM call). Any LLM/parse error -> [] (fail-soft; the caller keeps its first-pass extraction)."""
    if not mentions:
        return []
    try:
        entity_lines = "\n".join(f"{i}: {m.name} ({m.typ})" for i, m in enumerate(mentions))
        prompt = _REPROMPT.format(entities=entity_lines, text=text)
        vocab = _relation_vocab(relation_vocab)
        if vocab:
            prompt = _RELATION_VOCAB_INSTRUCTION.format(vocab=", ".join(vocab)) + prompt
        return _parse_relationships(_complete_extraction(llm, prompt), len(mentions))
    except Exception:
        return []
