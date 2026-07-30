"""Tests for the deterministic blocker + negative-pair synthesis (Task 3 of
the multi-source ER data pipeline). Stdlib only -- box-safe, no
torch/transformers/network."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from sources.negatives import blocking_key, synth_negatives  # noqa: E402


def test_blocking_key_is_deterministic_prefix_token():
    e = {"name": "Robert Smith", "city": "Newark"}
    assert blocking_key(e, ["name"]) == blocking_key(dict(e), ["name"])
    assert blocking_key(e, ["name"]) != ""


def test_synth_negatives_balance_and_determinism():
    names = ["Alice", "Bob", "Carol", "Dave", "Eve"]
    ents = {f"e{i}": {"name": names[i % 5], "phone": str(i)} for i in range(20)}
    negs1 = synth_negatives(ents, block_keys=["name"], hard_frac=0.5, seed=3, n=10)
    negs2 = synth_negatives(ents, block_keys=["name"], hard_frac=0.5, seed=3, n=10)
    assert negs1 == negs2                                  # deterministic
    assert all(a != b for a, b, _ in negs1)               # no self-pairs
    assert {tag for _, _, tag in negs1} == {"hard", "easy"}
    hard = [n for n in negs1 if n[2] == "hard"]
    easy = [n for n in negs1 if n[2] == "easy"]
    assert all(blocking_key(ents[a], ["name"]) == blocking_key(ents[b], ["name"])
               for a, b, _ in hard)                        # hard = same block
    assert all(blocking_key(ents[a], ["name"]) != blocking_key(ents[b], ["name"])
               for a, b, _ in easy)                        # easy = different block


def test_synth_negatives_partition_of_forces_cross_partition():
    # eids like "A:1", "B:2" -- partition_of picks off the "A"/"B" prefix.
    names = ["Alice", "Bob", "Carol", "Dave", "Eve"]
    ents = {}
    for i in range(10):
        prefix = "A" if i % 2 == 0 else "B"
        ents[f"{prefix}:{i}"] = {"name": names[i % 5], "phone": str(i)}
    negs = synth_negatives(
        ents, block_keys=["name"], hard_frac=0.5, seed=3, n=10,
        partition_of=lambda eid: eid[0],
    )
    assert negs  # sanity: the constraint didn't starve sampling to empty
    assert all(a[0] != b[0] for a, b, _ in negs)
