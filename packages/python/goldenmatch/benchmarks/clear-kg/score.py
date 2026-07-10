"""Track B (corpus-level entity resolution) scoring for CLEAR-KG.

Given a predicted clustering of entity mentions and the gold mention->entity map,
report the standard clustering trio plus the headline differentiator:

- pairwise P/R/F1  -- comparable to WhoIsWho SND (the through-line)
- B-cubed P/R/F1   -- the coreference-community standard (SciCo/CDCR read this)
- HOMOGRAPH SPLIT-RATE -- the money metric: of gold mention-pairs that SHARE a
  surface string but are DIFFERENT entities, what fraction did the system
  correctly keep in different clusters? Goes to ~0 for `if similar: merge`,
  stays high for neighborhood-aware ER.
"""
from __future__ import annotations

from collections import defaultdict
from itertools import combinations

from er_utils import norm


def _pred_map(pred_clusters: list[list[str]]) -> dict[str, int]:
    """mention_id -> predicted cluster index."""
    out: dict[str, int] = {}
    for cid, members in enumerate(pred_clusters):
        for m in members:
            out[m] = cid
    return out


def pairwise_prf(pred_clusters: list[list[str]], gold: dict[str, str]) -> dict:
    """Pairwise precision/recall/F1 over same-cluster mention pairs."""
    pred = _pred_map(pred_clusters)
    mids = [m for m in pred if m in gold]

    def _same_pairs(label_of) -> set[frozenset]:
        buckets: dict[object, list[str]] = defaultdict(list)
        for m in mids:
            buckets[label_of(m)].append(m)
        pairs: set[frozenset] = set()
        for grp in buckets.values():
            for a, b in combinations(sorted(grp), 2):
                pairs.add(frozenset((a, b)))
        return pairs

    pp = _same_pairs(lambda m: pred[m])
    gp = _same_pairs(lambda m: gold[m])
    tp = len(pp & gp)
    p = tp / len(pp) if pp else 0.0
    r = tp / len(gp) if gp else 0.0
    f1 = 2 * p * r / (p + r) if (p + r) else 0.0
    return {"precision": p, "recall": r, "f1": f1, "tp": tp, "pred_pairs": len(pp),
            "gold_pairs": len(gp)}


def bcubed_prf(pred_clusters: list[list[str]], gold: dict[str, str]) -> dict:
    """B-cubed precision/recall/F1 (per-mention, averaged)."""
    pred = _pred_map(pred_clusters)
    mids = [m for m in pred if m in gold]
    pred_grp: dict[int, set[str]] = defaultdict(set)
    gold_grp: dict[str, set[str]] = defaultdict(set)
    for m in mids:
        pred_grp[pred[m]].add(m)
        gold_grp[gold[m]].add(m)
    if not mids:
        return {"precision": 0.0, "recall": 0.0, "f1": 0.0}
    p_sum = r_sum = 0.0
    for m in mids:
        pc, gc = pred_grp[pred[m]], gold_grp[gold[m]]
        inter = len(pc & gc)
        p_sum += inter / len(pc)
        r_sum += inter / len(gc)
    p, r = p_sum / len(mids), r_sum / len(mids)
    f1 = 2 * p * r / (p + r) if (p + r) else 0.0
    return {"precision": p, "recall": r, "f1": f1}


def homograph_split_rate(
    pred_clusters: list[list[str]],
    gold: dict[str, str],
    mentions: list[dict],
) -> dict:
    """Of gold mention-pairs sharing a normalized surface but belonging to
    DIFFERENT entities, the fraction placed in different predicted clusters."""
    pred = _pred_map(pred_clusters)
    surf = {m["mention_id"]: norm(m["surface"]) for m in mentions}
    by_surface: dict[str, list[str]] = defaultdict(list)
    for m in mentions:
        mid = m["mention_id"]
        if mid in gold and mid in pred:
            by_surface[surf[mid]].append(mid)

    confusable = 0   # gold-different pairs sharing a surface
    split = 0        # ...correctly put in different predicted clusters
    for grp in by_surface.values():
        for a, b in combinations(sorted(grp), 2):
            if gold[a] != gold[b]:               # a homograph-confusable pair
                confusable += 1
                if pred[a] != pred[b]:
                    split += 1
    rate = split / confusable if confusable else 1.0
    return {"split_rate": rate, "confusable_pairs": confusable, "split": split}


def score_track_b(pred_clusters, gold, mentions) -> dict:
    pw = pairwise_prf(pred_clusters, gold)
    b3 = bcubed_prf(pred_clusters, gold)
    hg = homograph_split_rate(pred_clusters, gold, mentions)
    return {
        "pairwise_f1": pw["f1"], "pairwise_p": pw["precision"], "pairwise_r": pw["recall"],
        "bcubed_f1": b3["f1"], "bcubed_p": b3["precision"], "bcubed_r": b3["recall"],
        "homograph_split_rate": hg["split_rate"], "homograph_confusable": hg["confusable_pairs"],
        "n_pred_clusters": len(pred_clusters),
        "n_gold_entities": len(set(gold.values())),
    }
