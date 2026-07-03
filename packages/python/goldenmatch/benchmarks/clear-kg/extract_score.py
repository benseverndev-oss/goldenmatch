"""CLEAR-KG Track A scoring: canonicalized triple precision / recall / F1.

Three entity-matching modes -- the object of study:

  exact     -- subject/object must equal a gold entity's CANONICAL string
               (Text2KGBench 'exact'). Alias-surfaced subjects (abbreviations,
               homographs) miss -> the alias-canonicalization penalty.
  relaxed   -- resolve a surface to a gold entity by ALIAS membership, tie-broken
               by id order when the surface is ambiguous (the field's string-
               based 'relaxed' matching). Recovers aliases, but MIS-CREDITS
               homographs (picks a partner arbitrarily).
  er_aware  -- resolve by alias membership + CO-MENTION disambiguation: when a
               surface is ambiguous, pick the candidate whose gold neighborhood
               contains the sentence's object. Correct on homographs -- the moat,
               in the metric.

Relations are schema-closed (normalized to canonical in every mode via
REL_SYNONYMS), so the entity surface is the sole axis. TP = predicted triples
matching a distinct gold triple; precision = TP/|pred|, recall = TP/|gold|.
"""
from __future__ import annotations

from collections import defaultdict

from er_utils import norm
from extract_data import _phrase_to_canonical

_PHRASE2CANON = _phrase_to_canonical()


def _index(dataset: dict):
    by_id = {e["entity_id"]: e for e in dataset["entities"]}
    # canonical (norm) -> entity id, for exact matching
    canon2id = {norm(e["canonical"]): e["entity_id"] for e in dataset["entities"]}
    # alias (norm) -> [entity ids], id-sorted, for relaxed/er_aware
    alias2ids: dict[str, list[str]] = defaultdict(list)
    for e in dataset["entities"]:
        for a in e["aliases"]:
            alias2ids[norm(a)].append(e["entity_id"])
    for k in alias2ids:
        alias2ids[k].sort()
    # gold co-mention context: entity id -> set of entity ids it appears with
    context: dict[str, set[str]] = defaultdict(set)
    for s, _r, o in dataset["gold"]:
        context[s].add(o)
        context[o].add(s)
    return by_id, canon2id, alias2ids, context


def _resolve(surface, *, mode, ctx_id, canon2id, alias2ids, context):
    key = norm(surface)
    if mode == "exact":
        return canon2id.get(key)
    cands = alias2ids.get(key, [])
    if not cands:
        return None
    if len(cands) == 1:
        return cands[0]
    if mode == "er_aware" and ctx_id is not None:
        disamb = [c for c in cands if ctx_id in context.get(c, ())]
        if disamb:
            return disamb[0]
    return cands[0]  # relaxed (or er_aware with no context): homograph-blind


def score_extraction(preds: list[tuple], dataset: dict, mode: str) -> dict:
    _by_id, canon2id, alias2ids, context = _index(dataset)
    gold = set(dataset["gold"])
    homograph_ids = set(dataset["homograph_ids"])
    gold_homograph = {t for t in gold if t[0] in homograph_ids}

    matched: set[tuple] = set()
    tp = 0
    for subj_s, rel_p, obj_s, _doc in preds:
        rel = _PHRASE2CANON.get(rel_p, rel_p)  # schema-closed relation
        # resolve object first (unambiguous), then use it to disambiguate subject
        obj_e = _resolve(obj_s, mode=mode, ctx_id=None, canon2id=canon2id,
                         alias2ids=alias2ids, context=context)
        subj_e = _resolve(subj_s, mode=mode, ctx_id=obj_e, canon2id=canon2id,
                          alias2ids=alias2ids, context=context)
        if not subj_e or not obj_e:
            continue
        t = (subj_e, rel, obj_e)
        if t in gold and t not in matched:
            tp += 1
            matched.add(t)

    n_pred, n_gold = len(preds), len(gold)
    p = tp / n_pred if n_pred else 0.0
    r = tp / n_gold if n_gold else 0.0
    f1 = 2 * p * r / (p + r) if (p + r) else 0.0
    hg_recall = (len(matched & gold_homograph) / len(gold_homograph)
                 if gold_homograph else 1.0)
    return {"precision": p, "recall": r, "f1": f1, "tp": tp,
            "n_pred": n_pred, "n_gold": n_gold,
            "homograph_recall": hg_recall, "n_gold_homograph": len(gold_homograph)}
