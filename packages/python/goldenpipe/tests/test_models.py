"""Tests for goldenpipe.models.context."""
import polars as pl
from goldenpipe.models.context import (
    PipeContext, StageResult, Decision, PipeResult,
    StageStatus, PipeStatus,
)


class TestStageStatus:
    def test_values(self):
        assert StageStatus.SUCCESS == "success"
        assert StageStatus.SKIPPED == "skipped"
        assert StageStatus.FAILED == "failed"

    def test_is_str(self):
        assert isinstance(StageStatus.SUCCESS, str)


class TestPipeStatus:
    def test_values(self):
        assert PipeStatus.SUCCESS == "success"
        assert PipeStatus.PARTIAL == "partial"
        assert PipeStatus.FAILED == "failed"


class TestPipeContext:
    def test_defaults(self):
        ctx = PipeContext()
        assert ctx.df is None
        assert ctx.artifacts == {}
        assert ctx.metadata == {}
        assert ctx.timing == {}
        assert ctx.reasoning == {}

    def test_with_df(self, sample_df):
        ctx = PipeContext(df=sample_df)
        assert ctx.df is not None
        assert len(ctx.df) == 5

    def test_artifacts_independent(self):
        ctx1 = PipeContext()
        ctx2 = PipeContext()
        ctx1.artifacts["key"] = "value"
        assert "key" not in ctx2.artifacts


class TestStageResult:
    def test_success(self):
        r = StageResult(status=StageStatus.SUCCESS)
        assert r.status == StageStatus.SUCCESS
        assert r.decision is None
        assert r.error is None

    def test_failed_with_error(self):
        r = StageResult(status=StageStatus.FAILED, error="boom")
        assert r.error == "boom"

    def test_with_decision(self):
        d = Decision(skip=["match"], reason="PII detected")
        r = StageResult(status=StageStatus.SUCCESS, decision=d)
        assert r.decision.skip == ["match"]


class TestDecision:
    def test_defaults(self):
        d = Decision()
        assert d.skip == []
        assert d.abort is False
        assert d.insert == []
        assert d.reason == ""

    def test_skip(self):
        d = Decision(skip=["flow", "match"])
        assert d.skip == ["flow", "match"]

    def test_abort(self):
        d = Decision(abort=True, reason="fatal")
        assert d.abort is True

    def test_insert(self):
        d = Decision(skip=["match"], insert=["match_pprl"], reason="PII")
        assert d.insert == ["match_pprl"]

    def test_independent_lists(self):
        d1 = Decision()
        d2 = Decision()
        d1.skip.append("x")
        assert "x" not in d2.skip


class TestPipeResult:
    def test_success(self):
        r = PipeResult(
            status=PipeStatus.SUCCESS,
            source="test.csv",
            input_rows=5,
            stages={},
            artifacts={},
            skipped=[],
            errors=[],
            reasoning={},
            timing={},
        )
        assert r.status == PipeStatus.SUCCESS
        assert r.source == "test.csv"
        assert r.input_rows == 5

    def test_repr_contains_source(self):
        r = PipeResult(
            status=PipeStatus.SUCCESS, source="test.csv", input_rows=5,
            stages={}, artifacts={}, skipped=[], errors=[], reasoning={}, timing={},
        )
        assert "test.csv" in repr(r)

    def test_repr_html(self):
        r = PipeResult(
            status=PipeStatus.SUCCESS, source="test.csv", input_rows=5,
            stages={}, artifacts={}, skipped=[], errors=[], reasoning={}, timing={},
        )
        html = r._repr_html_()
        assert "<table" in html
        assert "test.csv" in html
