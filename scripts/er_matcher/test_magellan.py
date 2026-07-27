"""Tests for the Magellan/DeepMatcher fetch-at-build EVAL loader (Task 6 of
the multi-source ER data pipeline). Stdlib only -- box-safe, no
torch/transformers. Never sets GOLDENMATCH_ALLOW_FETCH=1 -- we never
actually download in these tests."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from pathlib import Path

from sources.magellan import MagellanSource

FIX = Path(__file__).parent / "tests" / "fixtures" / "magellan_mini"


def _all_rows(src):
    return [r for split in src.splits().values() for r in split]


def test_splits_present_and_valid_maps_to_val():
    src = MagellanSource(name="mini_products", root=FIX, domain="product")
    splits = src.load_from_dir()
    assert set(splits) == {"train", "val", "test"}
    assert "valid" not in splits


def test_labels_mapped_from_0_1():
    src = MagellanSource(name="mini_products", root=FIX, domain="product")
    splits = src.load_from_dir()
    train_labels = {(r["eid_a"], r["eid_b"]): r["label"] for r in splits["train"]}
    assert train_labels[("A:a0", "B:b0")] == "match"
    assert train_labels[("A:a0", "B:b1")] == "no_match"
    assert train_labels[("A:a1", "B:b1")] == "no_match"
    assert {r["label"] for rows in splits.values() for r in rows} == {"match", "no_match"}


def test_a_b_fields_joined_from_tables():
    src = MagellanSource(name="mini_products", root=FIX, domain="product")
    splits = src.load_from_dir()
    row = next(r for r in splits["train"] if r["eid_a"] == "A:a0" and r["eid_b"] == "B:b0")
    assert row["a"] == {"title": "Widget Pro 12oz", "manufacturer": "Acme", "price": "19.99"}
    assert row["b"] == {"title": "Widget Pro 12 oz", "manufacturer": "Acme Inc", "price": "19.99"}


def test_namespaced_ids_and_provenance():
    src = MagellanSource(name="mini_products", root=FIX, domain="product")
    rows = _all_rows(src)
    assert rows  # sanity: the fixture actually produces rows
    for r in rows:
        assert r["eid_a"].startswith("A:")
        assert r["eid_b"].startswith("B:")
        assert r["source"] == "magellan"
        assert r["dataset"] == "mini_products"
        assert r["domain"] == "product"


def test_eval_only():
    src = MagellanSource(name="mini_products", root=FIX, domain="product")
    assert src.eval_only is True


def test_attribution_and_license_present():
    src = MagellanSource(name="mini_products", root=FIX, domain="product")
    assert src.license == "cite-only"
    assert "Magellan" in src.attribution or "DeepMatcher" in src.attribution


def test_deterministic():
    mk = lambda: _all_rows(MagellanSource("mini_products", FIX, "product"))  # noqa: E731
    assert mk() == mk()


def test_splits_no_network_on_local_fixture(monkeypatch):
    """The parse path must never touch the network: calling splits() against
    an existing fixture root succeeds with GOLDENMATCH_ALLOW_FETCH unset."""
    monkeypatch.delenv("GOLDENMATCH_ALLOW_FETCH", raising=False)
    src = MagellanSource(name="mini_products", root=FIX, domain="product")
    splits = src.splits()
    assert sum(len(v) for v in splits.values()) > 0


def test_load_from_dir_no_network_on_local_fixture(monkeypatch):
    monkeypatch.delenv("GOLDENMATCH_ALLOW_FETCH", raising=False)
    src = MagellanSource(name="mini_products", root=FIX, domain="product")
    splits = src.load_from_dir()
    assert sum(len(v) for v in splits.values()) > 0


def test_fetch_guard_raises_without_env_var(monkeypatch):
    monkeypatch.delenv("GOLDENMATCH_ALLOW_FETCH", raising=False)
    src = MagellanSource(name="mini_products", root=Path("/nonexistent"), domain="product")
    try:
        src.fetch()
        raised = False
    except RuntimeError as e:
        raised = True
        assert "GOLDENMATCH_ALLOW_FETCH" in str(e)
    assert raised
