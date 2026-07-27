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
