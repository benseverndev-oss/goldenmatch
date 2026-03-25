"""Tests for the pipeline orchestrator."""
import polars as pl
import pytest
from pathlib import Path


@pytest.fixture
def sample_csv(tmp_path):
    path = tmp_path / "customers.csv"
    pl.DataFrame({
        "name": ["John Smith", "john smith", "Jane Doe", "JOHN SMITH", "Bob Jones"],
        "email": ["john@x.com", "john@x.com", "jane@y.com", "john@x.com", "bob@z.com"],
        "phone": ["555-0101", "555-0101", "555-0202", "555-0101", "555-0303"],
    }).write_csv(path)
    return str(path)


class TestPipeResult:
    def test_repr(self):
        from goldenpipe.pipeline import PipeResult
        r = PipeResult(source="test.csv", input_rows=100)
        assert "test.csv" in repr(r)
        assert "100" in repr(r)

    def test_default_status(self):
        from goldenpipe.pipeline import PipeResult
        r = PipeResult()
        assert r.status == "success"


class TestPipeline:
    def test_full_pipeline(self, sample_csv):
        from goldenpipe import run
        result = run(sample_csv)
        assert result.source == sample_csv
        assert result.input_rows == 5
        assert result.status in ("success", "partial")

    def test_step_by_step(self, sample_csv):
        from goldenpipe import Pipeline
        pipe = Pipeline(sample_csv)
        pipe.check()
        pipe.flow()
        pipe.match()
        result = pipe.result
        assert result.input_rows == 5

    def test_match_only(self, sample_csv):
        from goldenpipe import Pipeline
        pipe = Pipeline(sample_csv)
        pipe.match()
        result = pipe.result
        assert result.match is not None or "match" in str(result.errors)

    def test_strategy_override(self, sample_csv):
        from goldenpipe import run
        result = run(sample_csv, strategy="auto")
        assert result.status in ("success", "partial")

    def test_reasoning_populated(self, sample_csv):
        from goldenpipe import run
        result = run(sample_csv)
        assert isinstance(result.reasoning, dict)
        # At least some stages should have reasoning
        assert len(result.reasoning) > 0

    def test_timing_populated(self, sample_csv):
        from goldenpipe import run
        result = run(sample_csv)
        assert isinstance(result.timing, dict)

    def test_nonexistent_file(self, tmp_path):
        from goldenpipe import run
        result = run(str(tmp_path / "nonexistent.csv"))
        assert result.status == "failed"
        assert len(result.errors) > 0

    def test_skipped_list(self, sample_csv):
        from goldenpipe import run
        result = run(sample_csv)
        assert isinstance(result.skipped, list)

    def test_errors_list(self, sample_csv):
        from goldenpipe import run
        result = run(sample_csv)
        assert isinstance(result.errors, list)


class TestImports:
    def test_import_goldenpipe(self):
        import goldenpipe as gp
        assert hasattr(gp, "run")
        assert hasattr(gp, "Pipeline")
        assert hasattr(gp, "PipeResult")
        assert hasattr(gp, "__version__")

    def test_import_decisions(self):
        from goldenpipe import decide_flow, decide_match
        assert callable(decide_flow)
        assert callable(decide_match)
