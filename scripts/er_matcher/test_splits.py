"""Tests for the shared entity-split helper (Task 2 of the multi-source ER
data pipeline). Stdlib only -- box-safe, no torch/transformers/network."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from sources.splits import split_of  # noqa: E402


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
