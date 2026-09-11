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
    assert len(gt) > 0  # gen_labeled has GT
    _, gt2 = by_name["anchor_sparse_zip"].loader()
    assert gt2 == set()  # blocking-shape anchor, no F1


def test_real_loader_skips_when_absent():
    by_name = {d.name: d for d in REGISTRY}
    dblp = by_name["dblp_acm"]
    assert dblp.kind == "real"
    res = dblp.loader()  # data absent locally -> None
    assert res is None or (isinstance(res, tuple) and len(res) == 2)


def test_records_truth_to_frame_drops_the_surrogate_and_pairs_clusters():
    """The head-to-head loaders key rows by `record_id`; the adapter must drop it
    (a unique surrogate the kernel could otherwise match on) and turn cluster ids
    into row-index pairs, including a 3-member cluster and a singleton."""
    import pyarrow as pa

    from scripts.autoconfig_quality.datasets import _records_truth_to_frame

    ids = ["a:1", "b:1", "a:2", "b:9", "a:3"]
    records = pa.table({"record_id": ids, "title": ["x", "x", "y", "z", "x"]})
    truth = pa.table({"record_id": ids, "cluster_id": [0, 0, 2, 3, 0]})
    df, gt = _records_truth_to_frame(records, truth)
    assert df.columns == ["title"]
    assert gt == {(0, 1), (0, 4), (1, 4)}


def test_every_ab_lever_panel_dataset_resolves_to_a_loader():
    """ab_lever looks loaders up by name; a panel entry with no loader raises
    only when the gate runs. Catch the typo here instead."""
    from scripts.autoconfig_quality import datasets as D
    from scripts.bench_er_headtohead import ab_lever

    missing = [n for n in ab_lever._PANEL if not callable(getattr(D, f"_{n}", None))]
    assert not missing, missing


def test_ab_lever_required_datasets_must_be_measured():
    from scripts.bench_er_headtohead.ab_lever import _missing_required

    assert _missing_required("febrl3, dblp_acm", {"febrl3"}) == ["dblp_acm"]
    assert _missing_required("febrl3", {"febrl3", "person"}) == []
    assert _missing_required("", set()) == []


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


def test_ncvr_synthetic_always_loads_with_row_index_gt():
    from scripts.autoconfig_quality.datasets import _ncvr_synthetic

    df, gt = _ncvr_synthetic()
    assert "ncid" in df.columns
    assert gt and all(0 <= a < b < df.height for a, b in gt)


def test_ncvr_real_skips_when_absent(monkeypatch):
    import scripts.autoconfig_quality.datasets as ds

    monkeypatch.setattr(ds, "_NCVR_REAL_PATH", ds.Path("does/not/exist.txt"))
    assert ds._ncvr_real() is None


def test_historical_50k_loads_drops_truth_and_has_row_index_gt():
    from scripts.autoconfig_quality.datasets import _historical_50k

    loaded = _historical_50k()
    assert loaded is not None  # parquet is committed
    df, gt = loaded
    assert "cluster" not in df.columns and "unique_id" not in df.columns
    assert gt and all(0 <= a < b < df.height for a, b in gt)


def test_historical_50k_registered_full_scan():
    from scripts.autoconfig_quality.datasets import REGISTRY

    h = next(d for d in REGISTRY if d.name == "historical_50k")
    assert h.full_scan is True


@pytest.mark.parametrize(
    ("s7_name", "default_name"),
    [
        ("_household_hardneg_s7", "_household_hardneg"),
        ("_cotenant_hardneg_s7", "_cotenant_hardneg"),
        ("_ncvr_synthetic_s7", "_ncvr_synthetic"),
    ],
)
def test_s7_seed_variant_loads_and_differs_from_default_seed(s7_name, default_name):
    """DESIGN seed variants (link-cut routing P3): a non-empty frame, non-empty
    row-index truth, and a different frame than the default-seed loader."""
    import scripts.autoconfig_quality.datasets as ds

    s7_fn = getattr(ds, s7_name)
    default_fn = getattr(ds, default_name)

    s7_df, s7_gt = s7_fn()
    assert s7_df.height > 0
    assert s7_gt and all(0 <= a < b < s7_df.height for a, b in s7_gt)

    default_df, _ = default_fn()
    assert not s7_df.equals(default_df)
