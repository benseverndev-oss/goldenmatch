"""Tests for the lean synthetic ``SyntheticSource`` (Phase 1b, Task 4).

Imports both the synthetic package modules (``generate``/``schemas``) AND
``sources.*`` (the reused negatives/splits helpers + ``Row``), so the header
puts BOTH this file's directory (``synthetic/``, for ``generate``) and its
parent (``er_matcher/``, for ``sources.*``) on ``sys.path``. Stdlib + the
local modules only -- box-safe, no torch/transformers/network.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from generate import SyntheticSource  # noqa: E402


def _src(**kw):
    # n_entities kept generous so per-domain surname collisions (needed by the
    # hard-negative test) reliably occur under Zipf-weighted census draws.
    return SyntheticSource(name="synthetic", seed=20260728, n_entities=180,
                           profile="light", **kw)


def test_deterministic_byte_identical():
    import json
    a = {s: [json.dumps(r, sort_keys=True) for r in rows] for s, rows in _src().splits().items()}
    b = {s: [json.dumps(r, sort_keys=True) for r in rows] for s, rows in _src().splits().items()}
    assert a == b


def test_all_three_domains_present():
    rows = [r for rows in _src().splits().values() for r in rows]
    assert {r["domain"] for r in rows} == {"crm_contact", "organization", "business"}


def test_balance_and_labels():
    rows = [r for rows in _src().splits().values() for r in rows]
    labels = [r["label"] for r in rows]
    frac = labels.count("match") / len(labels)
    assert 0.4 < frac < 0.6
    assert set(labels) <= {"match", "no_match"}


def test_record_pools_leakage_consistent():
    src = _src()
    _splits, pools = src.splits(), src.record_pools()
    seen = {}
    for split, recs in pools.items():
        for rec in recs:
            k = tuple(sorted(rec.items()))
            assert k not in seen, "record in two splits"
            seen[k] = split
    assert all(pools[s] for s in ("train", "val", "test"))


def test_hard_negative_shares_name_but_conflicts_on_strong_id():
    from schemas import DOMAINS
    rows = [r for rows in _src().splits().values() for r in rows]
    negs = [r for r in rows if r["label"] == "no_match"]
    def shares_name_conflicts_id(r):
        sid = DOMAINS[r["domain"]].strong_id
        a, b = r["a"], r["b"]
        name_a = a.get("last") or a.get("name") or a.get("legal_name") or ""
        name_b = b.get("last") or b.get("name") or b.get("legal_name") or ""
        return name_a and name_a == name_b and a.get(sid) != b.get(sid)
    assert any(shares_name_conflicts_id(r) for r in negs)
