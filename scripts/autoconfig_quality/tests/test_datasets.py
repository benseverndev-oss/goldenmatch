import polars as pl
import pytest

from scripts.autoconfig_quality.datasets import REGISTRY, Dataset, _pairs_to_row_index


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


def test_person_anchor_has_gt_others_none():
    by_name = {d.name: d for d in REGISTRY}
    _, gt = by_name["anchor_person_match"].loader()
    assert len(gt) > 0                              # gen_labeled has GT
    _, gt2 = by_name["anchor_sparse_zip"].loader()
    assert gt2 == set()                             # blocking-shape anchor, no F1


def test_real_loader_skips_when_absent():
    by_name = {d.name: d for d in REGISTRY}
    dblp = by_name["dblp_acm"]
    assert dblp.kind == "real"
    res = dblp.loader()                             # data absent locally -> None
    assert res is None or (isinstance(res, tuple) and len(res) == 2)


def test_pairs_to_row_index_maps_and_canonicalizes():
    df = pl.DataFrame({"id": ["a", "b", "c"]})
    # (c,a) -> rows (2,0) -> canonical (0,2); (b,b) self -> dropped; (x,a) missing -> dropped
    gt = _pairs_to_row_index(df, "id", {("c", "a"), ("b", "b"), ("x", "a")})
    assert gt == {(0, 2)}


def test_febrl3_loader_shape_or_skip():
    pytest.importorskip("recordlinkage")
    from scripts.autoconfig_quality.datasets import _febrl3
    loaded = _febrl3()
    assert loaded is not None
    df, gt = loaded
    assert "id" in df.columns and gt and all(0 <= a < b < df.height for a, b in gt)
