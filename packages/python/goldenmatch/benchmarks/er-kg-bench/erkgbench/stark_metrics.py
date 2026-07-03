"""Standard IR metrics for the STaRK retrieval spike. Definitions match STaRK
(arXiv 2404.13207): Hit@k, Recall@20, MRR over a single query's ranked id list
against its gold answer id set. Reimplemented (not STaRK's harness) -- ~30 lines,
no dependency or retriever-API coupling. ``recall@20`` returns ``None`` on a
zero-gold query so the caller can EXCLUDE it from the recall mean (never divide by
zero).
"""
from __future__ import annotations

from collections.abc import Iterable

_RECALL_K = 20


def dedup_first_seen(ids: Iterable[int]) -> list[int]:
    """De-duplicate preserving first-seen order (Arm-B ranks seeds ++ neighbors; an
    undeduped repeat can push a distinct gold id past position 20)."""
    seen: set[int] = set()
    out: list[int] = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


def metrics(ranked_ids: list[int], gold_ids: set[int]) -> dict:
    """One query's metrics. ``ranked_ids``: rank-ordered retrieved ids (assumed
    already deduped by the caller). ``gold_ids``: the answer set. ``recall@20`` is
    ``None`` when there is no gold (caller skips it from the recall mean)."""
    gold = set(gold_ids)
    hit1 = 1.0 if ranked_ids[:1] and ranked_ids[0] in gold else 0.0
    hit5 = 1.0 if gold & set(ranked_ids[:5]) else 0.0
    mrr = 0.0
    for rank, i in enumerate(ranked_ids, start=1):
        if i in gold:
            mrr = 1.0 / rank
            break
    recall = None if not gold else len(gold & set(ranked_ids[:_RECALL_K])) / len(gold)
    return {"hit@1": hit1, "hit@5": hit5, "recall@20": recall, "mrr": mrr}


def mean_metrics(per_query: list[dict]) -> dict:
    """Aggregate per-query dicts. Recall averages only over non-None (has-gold)
    queries; the rest average over all queries."""
    n = len(per_query) or 1
    rec = [m["recall@20"] for m in per_query if m["recall@20"] is not None]
    return {
        "hit@1": sum(m["hit@1"] for m in per_query) / n,
        "hit@5": sum(m["hit@5"] for m in per_query) / n,
        "mrr": sum(m["mrr"] for m in per_query) / n,
        "recall@20": (sum(rec) / len(rec)) if rec else 0.0,
        "n_queries": len(per_query),
        "n_with_gold": len(rec),
    }
