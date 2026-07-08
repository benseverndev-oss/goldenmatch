from types import SimpleNamespace

from goldenpipe.compiler.e2e import end_to_end_lineage, format_end_to_end


def _cp(nodes):
    return {"nodes": nodes, "edges": []}


def _n(kind, nid, **rest):
    return {"kind": kind, "id": nid, "origin_stage": "s", "resolved": False, **rest}


def test_stitch_combines_survivorship_and_plan():
    compiled = _cp([
        _n("Scan", 0, column="email", ops=["pattern_consistency"]),
        _n("Map", 1, column="email", op="email_normalize"),
        _n("Partition", 2, keys=["email"]),
    ])
    fp = SimpleNamespace(value="j@x.com", source_row_id=24, strategy="conditional", confidence=1.0)
    cp = SimpleNamespace(cluster_id=1, fields={"email": fp})
    out = end_to_end_lineage(compiled, [cp])
    assert len(out["entries"]) == 1
    e = out["entries"][0]
    assert e["source_row_id"] == 24 and e["strategy"] == "conditional"
    assert e["transforms"] == ["email_normalize"] and e["blocking_key"] is True
    assert e["checks"] == ["pattern_consistency"]


def test_none_provenance_degrades_with_note():
    out = end_to_end_lineage({"nodes": [], "edges": []}, None)
    assert out["entries"] == []
    assert "survivorship inactive" in out["notes"][0]


def test_column_without_sp2_lineage_gets_empty_plan():
    fp = SimpleNamespace(value="x", source_row_id=3, strategy="conditional", confidence=0.9)
    cp = SimpleNamespace(cluster_id=1, fields={"phone": fp})
    out = end_to_end_lineage({"nodes": [], "edges": []}, [cp])
    e = out["entries"][0]
    assert e["source_row_id"] == 3 and e["transforms"] == [] and e["blocking_key"] is False


def test_format_end_to_end():
    out = {"entries": [{
        "cluster_id": 1, "column": "email", "value": "j@x.com", "source_row_id": 24,
        "strategy": "conditional", "survivor_confidence": 1.0, "checks": [],
        "transforms": ["email_normalize"], "blocking_key": False, "scorer_input": True,
    }], "notes": []}
    assert format_end_to_end(out) == (
        "cluster 1 email = 'j@x.com' (row 24 via conditional); pre-match transforms[email_normalize], scorer-input"
    )
