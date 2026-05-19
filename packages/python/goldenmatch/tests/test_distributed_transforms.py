import pytest

import polars as pl


def test_transform_plan_roundtrips_via_cloudpickle():
    """TransformPlan must survive cloudpickle (Ray's serializer) unchanged."""
    cloudpickle = pytest.importorskip("cloudpickle")
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


def test_legacy_build_transform_matches_plan_output():
    """build_transform shim must produce identical output to TransformPlan."""
    from goldenmatch.core.transform import build_transform
    from goldenmatch.distributed.transforms import TransformPlan, apply_plan

    df = pl.DataFrame({"name": ["ALICE", "Bob"]})

    legacy_fn = build_transform("name", "lower")
    legacy_out = legacy_fn(df)
    plan_out = apply_plan(df, TransformPlan(column="name", op="lower"))

    assert legacy_out.equals(plan_out)


def test_legacy_build_transform_strip_punctuation_matches():
    from goldenmatch.core.transform import build_transform
    from goldenmatch.distributed.transforms import TransformPlan, apply_plan

    df = pl.DataFrame({"name": ["A.B!C"]})
    legacy_out = build_transform("name", "strip_punctuation")(df)
    plan_out = apply_plan(df, TransformPlan(column="name", op="strip_punctuation"))
    assert legacy_out.equals(plan_out)
