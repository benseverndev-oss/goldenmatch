from goldenflow.config.schema import (
    DedupSpec,
    FilterSpec,
    GoldenFlowConfig,
    SplitSpec,
    TransformSpec,
)


def test_transform_spec():
    spec = TransformSpec(column="name", ops=["strip", "title_case"])
    assert spec.column == "name"
    assert spec.ops == ["strip", "title_case"]


def test_split_spec():
    spec = SplitSpec(source="name", target=["first_name", "last_name"], method="split_name")
    assert spec.target == ["first_name", "last_name"]


def test_filter_spec():
    spec = FilterSpec(column="email", condition="not_null")
    assert spec.condition == "not_null"


def test_dedup_spec_defaults():
    spec = DedupSpec(columns=["email"])
    assert spec.keep == "first"


def test_golden_flow_config_defaults():
    config = GoldenFlowConfig()
    assert config.transforms == []
    assert config.renames == {}
    assert config.drop == []
    assert config.dedup is None


def test_golden_flow_config_full():
    config = GoldenFlowConfig(
        source="data.csv",
        output="clean.csv",
        transforms=[TransformSpec(column="name", ops=["strip"])],
        splits=[SplitSpec(source="name", target=["first", "last"], method="split_name")],
        renames={"email_address": "email"},
        drop=["internal_id"],
        filters=[FilterSpec(column="email", condition="not_null")],
        dedup=DedupSpec(columns=["email"]),
    )
    assert len(config.transforms) == 1
    assert config.renames == {"email_address": "email"}
