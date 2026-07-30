"""Tests for the shared entity-split helper (Task 2 of the multi-source ER
data pipeline). Stdlib only -- box-safe, no torch/transformers/network."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from sources.splits import entity_keys_from_edges, split_of  # noqa: E402


def test_connected_components_merges_transitively():
    keys = entity_keys_from_edges(
        ["A1", "A2", "B1", "B2"],
        [("A1", "B1"), ("B1", "A2")],
    )
    assert keys["A1"] == keys["B1"] == keys["A2"]
    assert keys["B2"] != keys["A1"]
    assert keys["A1"] == "A1"


def test_singletons_get_own_key():
    keys = entity_keys_from_edges(["X", "Y"], [])
    assert keys["X"] == "X" and keys["Y"] == "Y" and keys["X"] != keys["Y"]


def test_entity_split_has_no_leakage():
    ids = [f"A{i}" for i in range(50)] + [f"B{i}" for i in range(50)]
    edges = [(f"A{i}", f"B{i}") for i in range(50)]
    keys = entity_keys_from_edges(ids, edges)
    sp = {rid: split_of(keys[rid], seed=1, val_frac=0.15, test_frac=0.15) for rid in ids}
    for i in range(50):
        assert sp[f"A{i}"] == sp[f"B{i}"]


def test_str_and_int_eid_parity():
    # generalization requirement: benchmark IDs are strings; must match int behavior
    assert split_of(7, seed=1, val_frac=.15, test_frac=.15, holdout_domain=None) == \
           split_of("7", seed=1, val_frac=.15, test_frac=.15, holdout_domain=None)


def test_holdout_domain_forced_to_test():
    assert split_of("abc", seed=1, val_frac=.15, test_frac=.15,
                    holdout_domain="business", domain="business") == "test"


def test_deterministic_and_partitions():
    got = {split_of(str(i), seed=9, val_frac=.15, test_frac=.15, holdout_domain=None)
           for i in range(200)}
    assert got == {"train", "val", "test"}
    assert split_of("k", seed=9, val_frac=.15, test_frac=.15, holdout_domain=None) == \
           split_of("k", seed=9, val_frac=.15, test_frac=.15, holdout_domain=None)
