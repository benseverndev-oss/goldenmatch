from types import SimpleNamespace

from goldenpipe.compiler.capture import capture_stage


def _planned(name, config=None):
    return SimpleNamespace(name=name, stage=SimpleNamespace(info=SimpleNamespace(name=name)), spec=SimpleNamespace(), config=config or {})


def test_load_captures_source():
    ctx = SimpleNamespace(artifacts={}, df=None)
    assert capture_stage(_planned("load"), ctx, None) == ("source", {}, False)


def test_flow_captures_map_specs_from_manifest_grouped():
    records = [
        SimpleNamespace(column="email", transform="email_normalize"),
        SimpleNamespace(column="email", transform="email_canonical"),
        SimpleNamespace(column="name", transform="name_proper"),
    ]
    ctx = SimpleNamespace(artifacts={"manifest": SimpleNamespace(records=records)}, df=None)
    kind, concrete, resolved = capture_stage(_planned("goldenflow.transform"), ctx, None)
    assert kind == "map"
    assert concrete == {"transforms": [
        {"column": "email", "ops": ["email_normalize", "email_canonical"]},
        {"column": "name", "ops": ["name_proper"]},
    ]}
    assert resolved is True  # no explicit config -> auto


def test_flow_explicit_config_not_resolved():
    ctx = SimpleNamespace(artifacts={"manifest": SimpleNamespace(records=[SimpleNamespace(column="email", transform="email_normalize")])}, df=None)
    _, _, resolved = capture_stage(_planned("goldenflow.transform", config={"config": {"transforms": []}}), ctx, None)
    assert resolved is False


def test_unknown_stage_captures_barrier():
    ctx = SimpleNamespace(artifacts={}, df=None)
    assert capture_stage(_planned("infer_schema", config={"x": 1}), ctx, None) == ("barrier", {"x": 1}, False)
