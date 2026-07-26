"""Tests for the synthetic ER-matcher pair generator (P1).

Locks the four guarantees the training run depends on: determinism, entity-level
splits (no leakage), class balance, and that every generated pair round-trips
through the shared serializer (train == inference contract).
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import gen_pairs  # noqa: E402,I001

from goldenmatch.core.er_matcher import (  # noqa: E402
    parse_verdict,
    render_target,
    serialize_pair_v1,
)

_KW = dict(n_entities=1200, seed=7, val_frac=0.15, test_frac=0.15, hard_neg_frac=0.5,
           holdout_domain="business")


def _all_rows(ds):
    return [r for rows in ds.values() for r in rows]


def test_deterministic_same_seed():
    a = gen_pairs.generate_dataset(**_KW)
    b = gen_pairs.generate_dataset(**_KW)
    assert a == b


def test_different_seed_differs():
    a = gen_pairs.generate_dataset(**_KW)
    b = gen_pairs.generate_dataset(**{**_KW, "seed": 99})
    assert a != b


def test_no_entity_leakage_across_splits():
    ds = gen_pairs.generate_dataset(**_KW)
    eid_split: dict[int, str] = {}
    for split, rows in ds.items():
        for r in rows:
            for eid in (r["eid_a"], r["eid_b"]):
                if eid < 0:
                    continue
                assert eid_split.setdefault(eid, split) == split, (
                    f"entity {eid} appears in both {eid_split[eid]} and {split}"
                )


def test_class_balance_and_hard_share():
    ds = gen_pairs.generate_dataset(**_KW)
    rows = _all_rows(ds)
    match = sum(r["label"] == "match" for r in rows)
    nomatch = sum(r["label"] == "no_match" for r in rows)
    # ~50/50 (one positive + one negative per entity, minus tiny-pool negatives).
    assert 0.9 <= match / nomatch <= 1.1
    hard = sum(r["source"] == "synthetic_hard" for r in rows if r["label"] == "no_match")
    assert 0.4 <= hard / nomatch <= 0.6  # ~hard_neg_frac


def test_holdout_domain_only_in_test():
    ds = gen_pairs.generate_dataset(**_KW)
    for split in ("train", "val"):
        assert all(r["domain"] != "business" for r in ds[split]), \
            "holdout domain leaked into non-test split"
    assert any(r["domain"] == "business" for r in ds["test"])


def test_splits_roughly_sized():
    ds = gen_pairs.generate_dataset(**_KW)
    n = sum(len(v) for v in ds.values())
    # test is inflated by the holdout domain, so just assert train is the majority
    # and val is a real minority slice.
    assert len(ds["train"]) > len(ds["val"]) > 0
    assert len(ds["test"]) > 0 and n == len(ds["train"]) + len(ds["val"]) + len(ds["test"])


def test_every_pair_serializes_and_target_round_trips():
    ds = gen_pairs.generate_dataset(**{**_KW, "n_entities": 300})
    for r in _all_rows(ds):
        text = serialize_pair_v1(r["a"], r["b"])
        assert "Record A:" in text and "Record B:" in text
        # the training target for this row parses back to a well-formed verdict.
        target = render_target(r["label"] == "match", 1.0 if r["label"] == "match" else 0.0)
        v = parse_verdict(target)
        assert v is not None and v["match"] == (r["label"] == "match")


def test_positive_is_same_entity_negative_is_different():
    ds = gen_pairs.generate_dataset(**_KW)
    for r in _all_rows(ds):
        if r["label"] == "match":
            assert r["eid_a"] == r["eid_b"]
        else:
            assert r["eid_a"] != r["eid_b"]
