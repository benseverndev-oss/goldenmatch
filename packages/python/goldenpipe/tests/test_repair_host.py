import polars as pl
from goldenpipe.models.context import PipeContext
from goldenpipe.repair_host import attach_repair_plan, build_column_inputs, sample_column


def test_sample_column_first_n_nonnull_as_str():
    s = pl.Series("c", [None, "a", "b", None, "c"])
    assert sample_column(s, limit=2) == ["a", "b"]

def test_build_column_inputs_from_contexts_and_df():
    df = pl.DataFrame({"iban": ["GB82WEST12345698765432", "DE89370400440532013000"]})
    class Ctx:  # minimal ColumnContext stand-in
        def __init__(self, name, t): self.name, self.inferred_type = name, t
    cols = build_column_inputs([Ctx("iban", "string")], df)
    assert cols[0]["name"] == "iban" and cols[0]["coarse_type"] == "string"
    assert cols[0]["samples"] == ["GB82WEST12345698765432", "DE89370400440532013000"]

def test_attach_repair_plan_sets_artifact_and_reasoning():
    df = pl.DataFrame({"signup_date": ["2020-01-01", "2021-05-05"]})
    findings = [{"column": "signup_date", "check": "future_dated", "message": "12 rows after today", "severity": "warning"}]
    class Ctx:
        def __init__(self, name, t): self.name, self.inferred_type = name, t
    ctx = PipeContext()
    attach_repair_plan(ctx, findings, [Ctx("signup_date", "date")], df)
    plan = ctx.artifacts["repair_plan"]
    assert plan["repairs"][0]["suggested_transforms"] == ["date_validate"]
    assert "date_validate" in ctx.reasoning["repair_plan"]
