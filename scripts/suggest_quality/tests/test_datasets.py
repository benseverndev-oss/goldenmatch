"""Smoke tests for the suggest_quality dataset registry.

Mirrors ``scripts.autoconfig_quality.tests.test_datasets`` — same assertions,
adapted for the suggest_quality registry and the extra ``synthetic`` entry.
"""
import polars as pl

from scripts.suggest_quality.datasets import REGISTRY, Dataset, _pairs_to_row_index


def test_registry_is_non_empty():
    assert len(REGISTRY) >= 1


def test_anchors_always_load():
    by_name = {d.name: d for d in REGISTRY}
    for n in ("anchor_sparse_zip", "anchor_shared_email", "anchor_person_match"):
        d = by_name[n]
        assert isinstance(d, Dataset)
        assert d.kind == "anchor"
        loaded = d.loader()
        assert loaded is not None
        df, gt = loaded
        assert df.height > 0


def test_synthetic_always_loads():
    by_name = {d.name: d for d in REGISTRY}
    d = by_name["synthetic"]
    assert d.kind == "real"
    loaded = d.loader()
    assert loaded is not None
    df, gt = loaded
    assert df.height > 0
    assert len(gt) > 0  # gen_labeled produces GT


def test_person_anchor_has_gt_others_empty():
    by_name = {d.name: d for d in REGISTRY}
    _, gt = by_name["anchor_person_match"].loader()
    assert len(gt) > 0
    _, gt2 = by_name["anchor_sparse_zip"].loader()
    assert gt2 == set()


def test_real_loader_skips_when_absent():
    by_name = {d.name: d for d in REGISTRY}
    dblp = by_name["dblp_acm"]
    assert dblp.kind == "real"
    res = dblp.loader()
    assert res is None or (isinstance(res, tuple) and len(res) == 2)


def test_pairs_to_row_index_maps_and_canonicalizes():
    df = pl.DataFrame({"id": ["a", "b", "c"]})
    gt = _pairs_to_row_index(df, "id", {("c", "a"), ("b", "b"), ("x", "a")})
    assert gt == {(0, 2)}


def test_historical_50k_registered_full_scan():
    by_name = {d.name: d for d in REGISTRY}
    h = by_name["historical_50k"]
    assert h.full_scan is True
