"""CLEAR-KG Track A — LLM relation extractor for Re-DocRED.

Standard DocRED relation-extraction setting: given a document's text + its gold
ENTITY set + the closed relation schema, extract every (head, relation, tail)
triple. The model picks head/tail by entity INDEX from the numbered list (so
scoring is exact against the gold index triples, no entity string-matching noise).

`openai_extract` needs `OPENAI_API_KEY` in the environment (never committed).
`mock_extract` is deterministic and offline (returns a caller-supplied oracle
fraction) so the harness is testable without a key or network.
"""
from __future__ import annotations

import json
import os

_SYSTEM = (
    "You are a precise information-extraction system for document-level relation "
    "extraction. You are given a document, a numbered list of entities, and a "
    "closed list of allowed relation names. Output ONLY relations that are "
    "explicitly supported by the text, choosing head and tail from the numbered "
    "entities and the relation from the allowed list. Respond as JSON: "
    '{"triples": [{"h": <entity index>, "r": "<relation name>", "t": <entity index>}]}.'
)

# The exhaustive/inverse variant. Re-DocRED gold is dense (it annotates INVERSE
# relations and pairs related across sentences), and a single-shot "list the
# triples" prompt under-generates. Instructing exhaustiveness + inverses lifts
# recall materially on a frozen model (measured: gpt-4.1 R 0.129 -> 0.196 on a
# controlled 5-doc slice) -- see RESULTS.md. It does NOT reach fine-tuned SOTA.
_EXHAUSTIVE_SYSTEM = _SYSTEM + (
    " Be EXHAUSTIVE and favor recall: emit a relation for EVERY entity pair the "
    "text supports, INCLUDING inverse relations (if you emit X located-in Y, also "
    "emit Y contains X when both are in the allowed list) and facts stated across "
    "different sentences. Do not stop early; scan every pair."
)


def _system_for(exhaustive: bool) -> str:
    return _EXHAUSTIVE_SYSTEM if exhaustive else _SYSTEM


def _prompt(doc: dict, schema: list[str]) -> str:
    ents = "\n".join(f"[{i}] {e['canonical']} ({e['type']})"
                     for i, e in enumerate(doc["entities"]))
    return (f"Document:\n{doc['text']}\n\n"
            f"Entities:\n{ents}\n\n"
            f"Allowed relations (use these names EXACTLY):\n{', '.join(schema)}\n\n"
            "List every relation triple explicitly supported by the document.")


def _coerce(raw: str, doc: dict, schema: set[str]) -> set[tuple]:
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        return set()
    n = len(doc["entities"])
    out: set[tuple] = set()
    for t in obj.get("triples", []):
        try:
            h, r, tt = int(t["h"]), str(t["r"]).strip(), int(t["t"])
        except (KeyError, ValueError, TypeError):
            continue
        # normalize the relation to the closed schema (case-insensitive); drop
        # anything the model invented outside the allowed list
        match = next((s for s in schema if s.lower() == r.lower()), None)
        if match is not None and 0 <= h < n and 0 <= tt < n and h != tt:
            out.add((h, match, tt))
    return out


def _is_reasoning(model: str) -> bool:
    # gpt-5* / o-series spend hidden reasoning tokens and reject temperature != 1
    return model.startswith(("gpt-5", "o1", "o3", "o4"))


def openai_extract(doc: dict, schema: list[str], *, model: str = "gpt-4o-mini",
                   exhaustive: bool = False, client=None) -> set[tuple]:
    import openai
    client = client or openai.OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    reasoning = _is_reasoning(model)
    kwargs = dict(
        model=model,
        # reasoning models need generous headroom -- hidden reasoning tokens are
        # spent BEFORE the JSON output, and at 6000 gpt-5 (full) truncated to an
        # empty response on ~half of the longer Re-DocRED docs
        max_completion_tokens=16000 if reasoning else 2000,
        response_format={"type": "json_object"},
        messages=[{"role": "system", "content": _system_for(exhaustive)},
                  {"role": "user", "content": _prompt(doc, schema)}],
    )
    if not reasoning:
        kwargs["temperature"] = 0  # deterministic where supported
    resp = client.chat.completions.create(**kwargs)
    return _coerce(resp.choices[0].message.content or "", doc, set(schema))


def mock_extract(doc: dict, schema: list[str], *, oracle: float = 1.0,
                 seed: int = 0) -> set[tuple]:
    """Return an ``oracle`` fraction of the gold triples (deterministic) -- a
    stand-in for a real extractor in offline tests."""
    gold = sorted(doc["gold"])
    k = int(round(len(gold) * oracle))
    # deterministic subset without RNG (seed varies the rotation)
    return set(gold[(seed % max(1, len(gold))):][:k]) if gold else set()
