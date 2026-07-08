from goldenpipe.compiler.ir import lower


def test_load_lowers_to_source():
    nodes, nid = lower("load", "source", {}, 0)
    assert nodes == [{"kind": "Source", "id": 0, "origin_stage": "load", "resolved": False, "produces": ["df"]}]
    assert nid == 1


def test_flow_transforms_lower_to_ordered_map_nodes():
    concrete = {"transforms": [{"column": "email", "ops": ["email_normalize", "email_canonical"]}]}
    nodes, nid = lower("goldenflow.transform", "map", concrete, 5, resolved=True)
    assert nodes == [
        {"kind": "Map", "id": 5, "origin_stage": "goldenflow.transform", "resolved": True, "column": "email", "op": "email_normalize"},
        {"kind": "Map", "id": 6, "origin_stage": "goldenflow.transform", "resolved": True, "column": "email", "op": "email_canonical"},
    ]
    assert nid == 7


def test_check_lowers_to_scan_per_column():
    concrete = {"columns": [{"column": "name", "ops": ["nullability", "pattern_consistency"]}]}
    nodes, _ = lower("goldencheck.scan", "scan", concrete, 0, resolved=True)
    assert nodes == [{"kind": "Scan", "id": 0, "origin_stage": "goldencheck.scan", "resolved": True, "column": "name", "ops": ["nullability", "pattern_consistency"]}]


def test_match_lowers_to_partition_pairscore_connected():
    concrete = {"keys": ["email"], "scorer": {"name": "jaro"}, "method": {"name": "connected_components"}}
    nodes, _ = lower("goldenmatch.dedupe", "match", concrete, 0, resolved=True)
    kinds = [n["kind"] for n in nodes]
    assert kinds == ["Partition", "PairScore", "Connected"]
    assert nodes[0]["keys"] == ["email"]
    assert nodes[1]["scorer"] == {"name": "jaro"}
    assert nodes[2]["method"] == {"name": "connected_components"}


def test_unknown_stage_lowers_to_barrier():
    nodes, _ = lower("infer_schema", "barrier", {"foo": 1}, 3)
    assert nodes == [{"kind": "Barrier", "id": 3, "origin_stage": "infer_schema", "resolved": False, "raw_config": {"foo": 1}}]
