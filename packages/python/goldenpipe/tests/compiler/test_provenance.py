from goldenpipe.compiler.provenance import provenance


def _cp(nodes):
    return {"nodes": nodes, "edges": []}


def _n(kind, nid, stage="s", resolved=False, **rest):
    return {"kind": kind, "id": nid, "origin_stage": stage, "resolved": resolved, **rest}


def test_column_gets_checks_and_ordered_transforms():
    cp = _cp([
        _n("Scan", 0, column="email", ops=["pattern_consistency"]),
        _n("Map", 1, column="email", op="email_normalize"),
        _n("Map", 2, column="email", op="email_canonical"),
    ])
    out = provenance(cp)
    f = next(x for x in out["fields"] if x["column"] == "email")
    assert f["checks"] == ["pattern_consistency"]
    assert f["transforms"] == ["email_normalize", "email_canonical"]
    assert f["node_ids"] == [0, 1, 2]


def test_blocking_and_scorer_roles():
    cp = _cp([
        _n("Map", 0, column="last_name", op="name_proper"),
        _n("Partition", 1, keys=["last_name"]),
        _n("PairScore", 2, scorer={"columns": ["email", "last_name"]}),
    ])
    out = provenance(cp)
    ln = {x["column"]: x for x in out["fields"]}
    assert ln["last_name"]["blocking_key"] is True
    assert ln["last_name"]["scorer_input"] is True
    assert ln["email"]["scorer_input"] is True
    assert ln["email"]["blocking_key"] is False


def test_source_connected_barrier_are_unmapped_notes():
    cp = _cp([_n("Source", 0, produces=["df"]), _n("Connected", 1, method={"name": "cc"}), _n("Barrier", 2, raw_config={})])
    out = provenance(cp)
    assert out["fields"] == []
    assert [u["kind"] for u in out["unmapped"]] == ["Source", "Connected", "Barrier"]


def test_empty_pipeline():
    assert provenance({"nodes": [], "edges": []}) == {"fields": [], "unmapped": []}
