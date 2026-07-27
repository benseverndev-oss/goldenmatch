"""Tests for the PairSource contract + registry (Task 1 of the multi-source
ER data pipeline). Stdlib only -- box-safe, no torch/transformers/network."""

from __future__ import annotations

import os
import sys

sys.path.insert(
    0, os.path.dirname(__file__)
)  # so `import sources...` resolves (matches test_gen_pairs.py)

from sources.base import PairSource, Row, get_source, iter_sources, register


def test_row_has_dataset_provenance_field():
    row: Row = {
        "a": {"x": "1"},
        "b": {"x": "2"},
        "label": "no_match",
        "domain": "people",
        "source": "synthetic",
        "dataset": "febrl",
        "eid_a": "1",
        "eid_b": "2",
    }
    assert row["dataset"] == "febrl"  # the field this pipeline ADDS
    assert set(row) >= {"a", "b", "label", "domain", "source", "dataset", "eid_a", "eid_b"}


def test_registry_roundtrip_and_isolation():
    class Dummy:
        name = "dummy_ds"

        def splits(self):
            return {"train": [], "val": [], "test": []}

    register(Dummy())
    assert get_source("dummy_ds").name == "dummy_ds"
    assert "dummy_ds" in {s.name for s in iter_sources()}
    assert isinstance(Dummy(), PairSource)


def test_get_unknown_source_raises():
    import pytest

    with pytest.raises(KeyError):
        get_source("does_not_exist")
