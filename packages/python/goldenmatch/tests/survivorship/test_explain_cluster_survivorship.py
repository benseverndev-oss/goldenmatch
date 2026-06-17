import polars as pl
from goldenmatch.core.explain import explain_cluster_nl
from goldenmatch.core.golden import ClusterProvenance, GroupProvenance


def _cp():
    g = GroupProvenance(name="addr", columns=["street", "city"], strategy="most_complete",
                        winner_row_id=7, winner_source=None, values={}, tie=False, confidence=1.0)
    return ClusterProvenance(cluster_id=5, cluster_quality="strong", cluster_confidence=0.9, fields={}, groups=[g])


def test_cluster_explain_appends_survivorship_block():
    cinfo = {"id": 5, "members": [10, 11], "size": 2}
    df = pl.DataFrame({"__row_id__": [10, 11], "street": ["a", "b"], "city": ["LA", "LA"]})
    out = explain_cluster_nl(cinfo, df, [], cluster_provenance=_cp())
    assert "Survivorship:" in out
    assert "promoted together from record 7" in out


def test_cluster_explain_no_provenance_unchanged():
    cinfo = {"id": 5, "members": [10], "size": 1}
    df = pl.DataFrame({"__row_id__": [10], "street": ["a"]})
    out = explain_cluster_nl(cinfo, df, [])      # no cluster_provenance -> no block
    assert "Survivorship:" not in out
