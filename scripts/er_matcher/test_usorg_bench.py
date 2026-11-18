"""Tests for the USORG-Bench loader. Stdlib only, box-safe, no
torch/transformers. Never sets GOLDENMATCH_ALLOW_FETCH=1, so nothing is
downloaded. The fixture is six real pairs (two per split) from release v1.0.1."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from pathlib import Path

import pytest
from sources.base import PairSource
from sources.usorg_bench import FEATURE_FIELDS, UsorgBenchSource

FIX = Path(__file__).parent / "tests" / "fixtures" / "usorg_mini"


def _src() -> UsorgBenchSource:
    return UsorgBenchSource(name="usorg_mini", root=FIX)


def test_is_pairsource():
    assert isinstance(_src(), PairSource)


def test_released_splits_with_dev_as_val():
    splits = _src().load_from_dir()
    assert set(splits) == {"train", "val", "test"}
    for rows in splits.values():
        assert sorted(r["label"] for r in rows) == ["match", "no_match"]


def test_labels_and_fields():
    splits = _src().load_from_dir()
    row = next(r for r in splits["test"] if r["label"] == "match")
    assert row["eid_a"] == "r15e91f3b2a33"
    assert row["eid_b"] == "rc8574a8470ea"
    assert row["a"]["name"] == "AMERICAN SOCIETY FOR RADIATION ONCOLOGY"
    assert row["b"]["name"] == "ASTRO"
    assert row["b"]["zip5"] == "22202"  # stays a string


def test_only_feature_fields_reach_rows():
    """source, id_type, name_kind and entity_id predict the label, so the
    benchmark's evaluation rule keeps them out."""
    for rows in _src().load_from_dir().values():
        for r in rows:
            assert tuple(r["a"]) == FEATURE_FIELDS
            assert tuple(r["b"]) == FEATURE_FIELDS


def test_provenance():
    for rows in _src().load_from_dir().values():
        for r in rows:
            assert r["source"] == "usorg_bench"
            assert r["dataset"] == "usorg_mini"
            assert r["domain"] == "organization"


def test_record_pools_are_disjoint_and_cover_pairs():
    src = _src()
    pools = src.record_pools()
    assert {k: len(v) for k, v in pools.items()} == {"train": 4, "val": 4, "test": 4}
    names = [rec["name"] for pool in pools.values() for rec in pool]
    assert "HARMAR" in [rec["name"] for rec in pools["val"]]
    assert len(names) == 12


def test_deterministic():
    assert _src().load_from_dir() == _src().load_from_dir()


def test_license_and_attribution():
    src = _src()
    assert src.license == "CC-BY"
    assert "USORG-Bench" in src.attribution


def test_splits_no_network_on_local_fixture(monkeypatch):
    monkeypatch.delenv("GOLDENMATCH_ALLOW_FETCH", raising=False)
    assert sum(len(v) for v in _src().splits().values()) == 6


def test_fetch_guard_raises_without_env_var(monkeypatch, tmp_path):
    monkeypatch.delenv("GOLDENMATCH_ALLOW_FETCH", raising=False)
    src = UsorgBenchSource(name="usorg_bench", root=tmp_path / "missing")
    with pytest.raises(RuntimeError, match="GOLDENMATCH_ALLOW_FETCH"):
        src.splits()
