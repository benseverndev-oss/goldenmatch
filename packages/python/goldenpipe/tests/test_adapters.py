"""Tests for Golden Suite adapters (mocked)."""
import pytest
from unittest.mock import MagicMock, patch

from goldenpipe.models.context import PipeContext, StageStatus


class TestScanStage:
    def test_info(self):
        from goldenpipe.adapters.check import ScanStage
        s = ScanStage()
        assert s.info.name == "goldencheck.scan"
        assert "findings" in s.info.produces
        assert "df" in s.info.consumes

    @patch("goldenpipe.adapters.check.HAS_CHECK", False)
    def test_validate_raises_without_tool(self):
        from goldenpipe.adapters.check import ScanStage
        s = ScanStage()
        with pytest.raises(RuntimeError, match="not installed"):
            s.validate(PipeContext())

    @patch("goldenpipe.adapters.check.HAS_CHECK", True)
    def test_run_success(self, sample_df):
        from goldenpipe.adapters import check
        mock_result = MagicMock()
        mock_result.findings = [{"severity": "warning", "check": "nulls"}]
        with patch.object(check, "_scan", return_value=mock_result):
            from goldenpipe.adapters.check import ScanStage
            s = ScanStage()
            ctx = PipeContext(df=sample_df, metadata={"source": "test.csv"})
            result = s.run(ctx)
            assert result.status == StageStatus.SUCCESS
            assert "findings" in ctx.artifacts


class TestTransformStage:
    def test_info(self):
        from goldenpipe.adapters.flow import TransformStage
        s = TransformStage()
        assert s.info.name == "goldenflow.transform"
        assert "df" in s.info.produces

    @patch("goldenpipe.adapters.flow.HAS_FLOW", False)
    def test_validate_raises_without_tool(self):
        from goldenpipe.adapters.flow import TransformStage
        s = TransformStage()
        with pytest.raises(RuntimeError, match="not installed"):
            s.validate(PipeContext())


class TestDedupeStage:
    def test_info(self):
        from goldenpipe.adapters.match import DedupeStage
        s = DedupeStage()
        assert s.info.name == "goldenmatch.dedupe"
        assert "clusters" in s.info.produces
        assert "golden" in s.info.produces
        assert "df" in s.info.consumes

    @patch("goldenpipe.adapters.match.HAS_MATCH", False)
    def test_validate_raises_without_tool(self):
        from goldenpipe.adapters.match import DedupeStage
        s = DedupeStage()
        with pytest.raises(RuntimeError, match="not installed"):
            s.validate(PipeContext())
