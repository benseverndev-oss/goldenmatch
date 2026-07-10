"""CLEAR-KG Track C engines: grounding-verification mechanisms.

Each takes the candidate triples + the document corpus and, per triple, decides:
  {triple_id, grounded (span emitted?), span, verdict ('supported'|'unsupported'),
   confidence (float|None)}.

Faithful reimplementations of the field's DOCUMENTED faithfulness mechanisms
(isolating the algorithm from LLM/extractor differences, exactly as Track B's
`incumbents.py` does for ER), plus the principled relation-aware grounder:

  ungrounded          -- emit the extracted triple, assert it, cite no span, no
                         confidence (LlamaIndex / LangChain LLMGraphTransformer
                         default: extraction with no verification layer).
  sentence_presence   -- SUPPORTED iff the two entities appear together in some
                         sentence; span = that sentence. The "within-sentence
                         presence" faithfulness the landscape scan found is
                         universal. Blind to whether the sentence states THIS
                         relation -> grounds distractors.
  ontology_conformance-- SUPPORTED iff the (subj_type, obj_type) matches the
                         relation's schema signature. Type-checks, never reads
                         the text -> grounds distractors AND hallucinations.
  relation_aware      -- SUPPORTED iff a co-occurring sentence contains a trigger
                         of THIS relation; cites that span; emits a graded
                         confidence (evidence strength). The "cite your source
                         with a confidence" mechanism the market skips.
"""
from __future__ import annotations

import re

from grounding_data import RELATIONS

_ACCEPT = 0.5  # relation_aware support threshold on the evidence-strength score


def _find(text: str, surface: str) -> re.Match | None:
    return re.search(rf"\b{re.escape(surface)}\b", text, flags=re.IGNORECASE)


def _cooccur_docs(cand: dict, docs: dict[str, str]) -> list[tuple[str, str]]:
    out = []
    for doc_id, text in docs.items():
        if _find(text, cand["subj_surface"]) and _find(text, cand["obj_surface"]):
            out.append((doc_id, text))
    return out


def _decision(cand, *, grounded, span, verdict, confidence):
    return {"triple_id": cand["triple_id"], "grounded": grounded, "span": span,
            "verdict": verdict, "confidence": confidence}


def ground_ungrounded(cands: list[dict], docs: dict[str, str]) -> list[dict]:
    # Extracts and asserts every triple; no span, no confidence.
    return [_decision(c, grounded=False, span=None, verdict="supported", confidence=None)
            for c in cands]


def ground_sentence_presence(cands: list[dict], docs: dict[str, str]) -> list[dict]:
    out = []
    for c in cands:
        co = _cooccur_docs(c, docs)
        if co:
            doc_id, text = co[0]
            out.append(_decision(c, grounded=True, span=(doc_id, [0, len(text)]),
                                 verdict="supported", confidence=None))
        else:
            out.append(_decision(c, grounded=False, span=None,
                                 verdict="unsupported", confidence=None))
    return out


def ground_ontology_conformance(cands: list[dict], docs: dict[str, str]) -> list[dict]:
    out = []
    for c in cands:
        sig = RELATIONS[c["rel"]]["sig"]
        conforms = (c["subj_type"], c["obj_type"]) == sig
        # schema check, not a span check -> no provenance emitted
        out.append(_decision(c, grounded=False, span=None,
                             verdict="supported" if conforms else "unsupported",
                             confidence=None))
    return out


def _relation_evidence(cand: dict, docs: dict[str, str]) -> tuple[float, tuple | None]:
    """Best (confidence, span) over co-occurring sentences for THIS relation.

    0.0  no co-occurring sentence, or none contains a trigger of this relation;
    0.7  a co-occurring sentence contains a trigger of this relation;
    0.95 ...and the trigger lies textually between the two entity mentions (the
         canonical "subj TRIGGER obj" shape) -- strongest evidence.
    """
    triggers = RELATIONS[cand["rel"]]["triggers"]
    best_conf, best_span = 0.0, None
    for doc_id, text in _cooccur_docs(cand, docs):
        low = text.lower()
        s_m, o_m = _find(text, cand["subj_surface"]), _find(text, cand["obj_surface"])
        lo, hi = sorted((s_m.start(), o_m.start()))
        for trg in triggers:
            idx = low.find(trg)
            if idx == -1:
                continue
            conf = 0.95 if lo < idx < hi else 0.7
            if conf > best_conf:
                best_conf, best_span = conf, (doc_id, [0, len(text)])
    return best_conf, best_span


def ground_relation_aware(cands: list[dict], docs: dict[str, str]) -> list[dict]:
    out = []
    for c in cands:
        conf, span = _relation_evidence(c, docs)
        supported = conf >= _ACCEPT
        out.append(_decision(c, grounded=supported, span=span if supported else None,
                             verdict="supported" if supported else "unsupported",
                             confidence=conf))
    return out


ENGINES = {
    "ungrounded": ground_ungrounded,
    "sentence_presence": ground_sentence_presence,
    "ontology_conformance": ground_ontology_conformance,
    "relation_aware": ground_relation_aware,
}
