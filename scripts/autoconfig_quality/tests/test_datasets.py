from scripts.autoconfig_quality.datasets import REGISTRY, Dataset


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
