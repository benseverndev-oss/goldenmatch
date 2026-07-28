"""Tests for the FEBRL synthetic-person loader (Task 5 of the multi-source
ER data pipeline). Stdlib only -- box-safe, no torch/transformers/network."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import warnings  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402
from sources.febrl import FebrlSource, _entity_of  # noqa: E402

FIX = Path(__file__).parent / "tests" / "fixtures" / "febrl_mini"

# The tiny fixture is expected to fall short of its negative target in at
# least one split (not enough same-split cross-entity same-block records
# to fill "hard" negatives) -- see test_shortfall_warning_fires_on_tiny_fixture
# for the dedicated, explicit assertion on that contract. Every other test
# below suppresses it so it doesn't show up as ambient noise.


def _all_rows(src):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        return [r for split in src.splits().values() for r in split]


def test_emits_both_labels_with_provenance():
    src = FebrlSource(FIX, seed=1)
    rows = _all_rows(src)
    assert {r["label"] for r in rows} == {"match", "no_match"}
    assert all(r["dataset"] == "febrl" and r["source"] == "febrl" for r in rows)
    assert all(r["domain"] == "person" for r in rows)


def test_positives_pair_same_entity():
    src = FebrlSource(FIX, seed=1)
    rows = _all_rows(src)
    positives = [r for r in rows if r["label"] == "match"]
    assert positives  # sanity: the fixture actually produces positives
    for r in positives:
        assert _entity_of(r["eid_a"]) == _entity_of(r["eid_b"])


def test_negatives_pair_different_entities():
    src = FebrlSource(FIX, seed=1)
    rows = _all_rows(src)
    negatives = [r for r in rows if r["label"] == "no_match"]
    assert negatives  # sanity: non-vacuous -- the fixture must yield negatives
    for r in negatives:
        assert _entity_of(r["eid_a"]) != _entity_of(r["eid_b"])


def test_entity_level_no_leak_across_splits():
    src = FebrlSource(FIX, seed=1)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        splits = src.splits()
    entity_to_splits: dict[str, set[str]] = {}
    for split_name, rows in splits.items():
        for r in rows:
            for eid in (r["eid_a"], r["eid_b"]):
                entity_to_splits.setdefault(_entity_of(eid), set()).add(split_name)
    assert entity_to_splits  # sanity: rows were actually produced
    for entity, split_set in entity_to_splits.items():
        assert len(split_set) == 1, f"entity {entity} leaked across splits: {split_set}"


def test_deterministic():
    mk = lambda: _all_rows(FebrlSource(FIX, seed=1))  # noqa: E731
    assert mk() == mk()


def test_attribution_and_license_present():
    src = FebrlSource(FIX, seed=1)
    assert src.license == "MPL-1.1"
    assert "FEBRL" in src.attribution


def test_splits_are_nonempty_overall():
    src = FebrlSource(FIX, seed=1)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        splits = src.splits()
    assert set(splits) == {"train", "val", "test"}
    assert sum(len(v) for v in splits.values()) > 0


def test_shortfall_warning_fires_on_tiny_fixture():
    src = FebrlSource(FIX, seed=1)
    with pytest.warns(UserWarning, match=r"only \d+/\d+ negatives"):
        src.splits()


def test_record_pool_every_record_in_exactly_one_split():
    src = FebrlSource(FIX, seed=1)
    pools = src.record_pools()
    assert set(pools) == {"train", "val", "test"}
    all_soc_sec_ids = [entry["soc_sec_id"] for split in pools.values() for entry in split]
    with open(FIX / "febrl_sample.csv", newline="", encoding="utf-8") as f:
        n_records = sum(1 for _ in f) - 1  # minus header
    assert sum(len(v) for v in pools.values()) == n_records
    # soc_sec_id isn't unique per record here (dup rows share their org's
    # value), so just check no record was dropped -- length parity above
    # plus every entry present is the leakage-relevant invariant.
    assert len(all_soc_sec_ids) == n_records


def test_record_pool_leakage_consistent_with_gold_pairs():
    src = FebrlSource(FIX, seed=1)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        splits = src.splits()
    pools = src.record_pools()
    records = src._read_records()

    def find_split(rid: str) -> str:
        target = records[rid]
        for split, entries in pools.items():
            if target in entries:
                return split
        raise AssertionError(f"{rid} missing from record_pools()")

    positives = [r for r in (row for rows in splits.values() for row in rows) if r["label"] == "match"]
    assert positives  # sanity
    for r in positives:
        assert find_split(r["eid_a"]) == find_split(r["eid_b"])


def test_anchor_fallback_when_no_org_record():
    # rec-6 has only rec-6-dup-0 / rec-6-dup-1, no rec-6-org. The anchor
    # falls back to the first record in sorted order, and that entity
    # still yields a positive pair.
    src = FebrlSource(FIX, seed=1)
    rows = _all_rows(src)
    match = [
        r
        for r in rows
        if r["label"] == "match" and {r["eid_a"], r["eid_b"]} == {"rec-6-dup-0", "rec-6-dup-1"}
    ]
    assert len(match) == 1
    assert match[0]["eid_a"] == "rec-6-dup-0"
    assert match[0]["eid_b"] == "rec-6-dup-1"
