"""CLEAR-KG Track A extractors: doc -> predicted surface triples.

Deterministic stand-ins for an LLM extraction pass (a later phase). On the
closed-schema templated corpus a pattern extractor recovers what's written; the
point is to exercise the metric, not to claim SOTA extraction. A `lossy`
extractor (drops + corrupts) shows F1 tracks extraction quality (table stakes).

Each predicted triple is ``(subj_surface, rel_phrase, obj_surface, doc_id)``.
"""
from __future__ import annotations

import random

from extract_data import REL_SYNONYMS

# longest-first so "is based in" wins over a hypothetical "based in" substring
_PHRASES = sorted({p for ps in REL_SYNONYMS.values() for p in ps}, key=len, reverse=True)


def pattern_extractor(dataset: dict) -> list[tuple]:
    """Split each 'SUBJ PHRASE OBJ.' sentence on the known relation lexicon."""
    preds: list[tuple] = []
    for doc_id, text in dataset["docs"].items():
        body = text.rstrip(".").strip()
        low = body.lower()
        for phrase in _PHRASES:
            idx = low.find(f" {phrase} ")
            if idx == -1:
                continue
            subj = body[:idx].strip()
            obj = body[idx + len(phrase) + 2:].strip()
            preds.append((subj, phrase, obj, doc_id))
            break
    return preds


def lossy_extractor(dataset: dict, *, seed: int = 0, drop: float = 0.3,
                    corrupt: float = 0.15) -> list[tuple]:
    """Faithful extraction, then drop a fraction (misses -> recall hit) and
    corrupt some objects to a random other object (spurious -> precision hit)."""
    rng = random.Random(seed)
    base = pattern_extractor(dataset)
    obj_surfaces = [e["canonical"] for e in dataset["entities"] if e["type"] in ("ORG", "PLACE")]
    out: list[tuple] = []
    for subj, phrase, obj, doc_id in base:
        if rng.random() < drop:
            continue
        if rng.random() < corrupt:
            obj = rng.choice([o for o in obj_surfaces if o != obj])
        out.append((subj, phrase, obj, doc_id))
    return out


EXTRACTORS = {"pattern": pattern_extractor, "lossy": lossy_extractor}
