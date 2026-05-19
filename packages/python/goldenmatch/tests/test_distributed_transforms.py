import polars as pl


def test_transform_plan_roundtrips_via_cloudpickle():
    """TransformPlan must survive cloudpickle (Ray's serializer) unchanged."""
    cloudpickle = __import__("cloudpickle")
    from goldenmatch.distributed.transforms import TransformPlan, apply_plan

    plan = TransformPlan(column="name", op="lower")
    restored = cloudpickle.loads(cloudpickle.dumps(plan))
    assert restored == plan

    df = pl.DataFrame({"name": ["ALICE", "Bob"]})
    out = apply_plan(df, restored)
    assert out["name"].to_list() == ["alice", "bob"]


def test_transform_plan_strip_punctuation():
    from goldenmatch.distributed.transforms import TransformPlan, apply_plan

    plan = TransformPlan(column="name", op="strip_punctuation")
    df = pl.DataFrame({"name": ["A.B!C?"]})
    out = apply_plan(df, plan)
    assert out["name"].to_list() == ["ABC"]


def test_transform_plan_upper():
    from goldenmatch.distributed.transforms import TransformPlan, apply_plan

    plan = TransformPlan(column="name", op="upper")
    df = pl.DataFrame({"name": ["alice", "Bob"]})
    out = apply_plan(df, plan)
    assert out["name"].to_list() == ["ALICE", "BOB"]


def test_transform_plan_target_column():
    """When target is set, write to a new column instead of overwriting."""
    from goldenmatch.distributed.transforms import TransformPlan, apply_plan

    plan = TransformPlan(column="name", op="lower", target="name_lower")
    df = pl.DataFrame({"name": ["ALICE"]})
    out = apply_plan(df, plan)
    assert out["name"].to_list() == ["ALICE"]
    assert out["name_lower"].to_list() == ["alice"]
