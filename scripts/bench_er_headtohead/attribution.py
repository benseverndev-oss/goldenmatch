#!/usr/bin/env python
"""Recall attribution: localize where true pairs die (blocking vs threshold).

All inputs are sets of canonical (min,max) record-id pairs:
  gt_pairs        ground-truth matching pairs
  candidate_pairs pairs that survived candidate generation (blocking)
  emitted_pairs   pairs the scorer emitted above threshold

  blocking_recall = |gt & candidates| / |gt|     (the ceiling)
  final_recall    = |gt & emitted|    / |gt|
  threshold_loss  = (|gt & candidates| - |gt & emitted|) / |gt|
"""
from __future__ import annotations


def _canon(pairs):
    return {(min(a, b), max(a, b)) for a, b in pairs}


def attribution(gt_pairs, candidate_pairs, emitted_pairs) -> dict:
    gt = _canon(gt_pairs)
    cand = _canon(candidate_pairs)
    emit = _canon(emitted_pairs)
    n = len(gt)
    if n == 0:
        return {"n_gt_pairs": 0, "blocking_recall": 0.0,
                "final_recall": 0.0, "threshold_loss": 0.0}
    blocked = len(gt & cand)
    emitted_gt = len(gt & emit)
    return {
        "n_gt_pairs": n,
        "blocking_recall": round(blocked / n, 4),
        "final_recall": round(emitted_gt / n, 4),
        "threshold_loss": round((blocked - emitted_gt) / n, 4),
    }


def truth_to_pairs(truth) -> set:
    """Expand a {record_id, cluster_id} frame into within-cluster GT pairs."""
    from itertools import combinations
    pairs = set()
    for _cid, grp in truth.group_by("cluster_id"):
        ids = grp["record_id"].to_list()
        if len(ids) > 1:
            pairs.update((min(a, b), max(a, b)) for a, b in combinations(ids, 2))
    return pairs
