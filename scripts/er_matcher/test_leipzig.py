"""Tests for the Leipzig CC-BY benchmark loader (Task 4 of the multi-source
ER data pipeline). Stdlib only -- box-safe, no torch/transformers/network."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from pathlib import Path

from sources.leipzig import LeipzigSource

FIX = Path(__file__).parent / "tests" / "fixtures" / "leipzig_mini"


def _all_rows(src):
    return [r for split in src.splits().values() for r in split]


def test_emits_positives_and_negatives():
    src = LeipzigSource(name="abt_buy", root=FIX, domain="product", block_fields=["title"], seed=1)
    rows = _all_rows(src)
    assert {r["label"] for r in rows} == {"match", "no_match"}
    assert all(r["dataset"] == "abt_buy" and r["source"] == "leipzig" for r in rows)
    assert sum(r["label"] == "match" for r in rows) == 2  # 2 gold pairs in the fixture


def test_negatives_exclude_gold_matches():
    src = LeipzigSource(name="abt_buy", root=FIX, domain="product", block_fields=["title"], seed=1)
    gold = {("A:a1", "B:b1"), ("A:a2", "B:b2")}
    negs = {(r["eid_a"], r["eid_b"]) for r in _all_rows(src) if r["label"] == "no_match"}
    negs |= {(b, a) for a, b in negs}  # both orderings
    assert gold.isdisjoint(negs)


def test_deterministic():
    mk = lambda: _all_rows(LeipzigSource("abt_buy", FIX, "product", ["title"], 1))  # noqa: E731
    assert mk() == mk()


def test_namespaced_ids_and_domain_provenance():
    src = LeipzigSource(name="abt_buy", root=FIX, domain="product", block_fields=["title"], seed=1)
    rows = _all_rows(src)
    for r in rows:
        assert r["domain"] == "product"
        assert r["eid_a"].startswith(("A:", "B:"))
        assert r["eid_b"].startswith(("A:", "B:"))


def test_positive_pairs_match_mapping_fixture():
    src = LeipzigSource(name="abt_buy", root=FIX, domain="product", block_fields=["title"], seed=1)
    rows = _all_rows(src)
    positives = {(r["eid_a"], r["eid_b"]) for r in rows if r["label"] == "match"}
    assert positives == {("A:a1", "B:b1"), ("A:a2", "B:b2")}


def test_splits_partition_and_are_nonempty_overall():
    src = LeipzigSource(name="abt_buy", root=FIX, domain="product", block_fields=["title"], seed=1)
    splits = src.splits()
    assert set(splits) == {"train", "val", "test"}
    assert sum(len(v) for v in splits.values()) > 0


def test_attribution_and_license_present():
    src = LeipzigSource(name="abt_buy", root=FIX, domain="product", block_fields=["title"], seed=1)
    assert src.license == "CC-BY"
    assert "Leipzig" in src.attribution


def test_negatives_are_strictly_cross_table():
    src = LeipzigSource(name="abt_buy", root=FIX, domain="product", block_fields=["title"], seed=1)
    rows = _all_rows(src)
    no_match = [r for r in rows if r["label"] == "no_match"]
    assert no_match  # sanity: the fixture actually produces negatives
    assert all(r["eid_a"][0] != r["eid_b"][0] for r in no_match)


def test_no_shortfall_warning_on_fixture():
    import warnings

    src = LeipzigSource(name="abt_buy", root=FIX, domain="product", block_fields=["title"], seed=1)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        rows = _all_rows(src)
    shortfall_warnings = [w for w in caught if "negatives after gold-filter" in str(w.message)]
    assert not shortfall_warnings
    assert sum(r["label"] == "no_match" for r in rows) == 2
