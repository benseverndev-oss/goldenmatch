"""Tests for Golden Suite adapters (mocked)."""
from unittest.mock import MagicMock, patch

import pytest
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
    def test_run_scans_frame_not_file(self, sample_df):
        # With a frame present, the stage scans ctx.df via scan_dataframe --
        # NOT the source path via scan_file (the redundant-re-read seam).
        from goldenpipe.adapters import check
        findings = [{"severity": "warning", "check": "nulls"}]
        profile = MagicMock()
        with patch.object(check, "_scan_df", return_value=(findings, profile)) as m_df, \
                patch.object(check, "_scan") as m_file:
            from goldenpipe.adapters.check import ScanStage
            s = ScanStage()
            ctx = PipeContext(df=sample_df, metadata={"source": "test.csv"})
            result = s.run(ctx)
            assert result.status == StageStatus.SUCCESS
            assert ctx.artifacts["findings"] == findings
            assert ctx.artifacts["profile"] is profile
            # scanned the frame, not the path
            m_df.assert_called_once()
            assert m_df.call_args.args[0] is sample_df
            assert m_df.call_args.kwargs.get("file_path") == "test.csv"
            m_file.assert_not_called()

    @patch("goldenpipe.adapters.check.HAS_CHECK", True)
    def test_run_dataframe_source_still_scans(self, sample_df):
        # Regression: a "<DataFrame>" source is not a readable path, so the old
        # scan_file(source) produced no profile. Scanning ctx.df fixes it.
        from goldenpipe.adapters import check
        with patch.object(check, "_scan_df", return_value=([], MagicMock())) as m_df, \
                patch.object(check, "_scan") as m_file:
            from goldenpipe.adapters.check import ScanStage
            s = ScanStage()
            ctx = PipeContext(df=sample_df, metadata={"source": "<DataFrame>"})
            result = s.run(ctx)
            assert result.status == StageStatus.SUCCESS
            assert ctx.artifacts["profile"] is not None
            m_df.assert_called_once()
            m_file.assert_not_called()

    @patch("goldenpipe.adapters.check.HAS_CHECK", True)
    def test_run_forwards_stage_config(self, sample_df):
        from goldenpipe.adapters import check
        with patch.object(check, "_scan_df", return_value=([], MagicMock())) as m_df:
            from goldenpipe.adapters.check import ScanStage
            s = ScanStage()
            ctx = PipeContext(df=sample_df, metadata={"source": "test.csv"},
                              stage_config={"deep": True})
            s.run(ctx)
            assert m_df.call_args.kwargs.get("deep") is True

    @patch("goldenpipe.adapters.check.HAS_CHECK", True)
    def test_run_falls_back_to_scan_file_without_frame(self):
        # No ctx.df (defensive path) -> scan_file(source).
        from goldenpipe.adapters import check
        with patch.object(check, "_scan", return_value=([], MagicMock())) as m_file, \
                patch.object(check, "_scan_df") as m_df:
            from goldenpipe.adapters.check import ScanStage
            s = ScanStage()
            ctx = PipeContext(df=None, metadata={"source": "test.csv"})
            result = s.run(ctx)
            assert result.status == StageStatus.SUCCESS
            m_file.assert_called_once()
            m_df.assert_not_called()


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


try:
    import goldenmatch.config.schemas  # noqa: F401
    HAS_GOLDENMATCH = True
except ImportError:
    HAS_GOLDENMATCH = False


@pytest.mark.skipif(not HAS_GOLDENMATCH, reason="goldenmatch not installed")
class TestBuildConfigFromContexts:
    """Tests for _build_config_from_contexts geo-compound blocking."""

    def test_geo_columns_compound_with_name_blocking(self):
        """When geo columns exist with NAME-type contexts, blocking should compound geo + name."""
        from goldenpipe.adapters.match import _build_config_from_contexts
        from goldenpipe.models.column_context import ColumnType

        contexts = [
            MagicMock(name="last_name", inferred_type=ColumnType.NAME, is_identifier=True),
            MagicMock(name="state", inferred_type=ColumnType.GEO, is_identifier=False),
        ]
        contexts[0].name = "last_name"
        contexts[1].name = "state"

        import polars as pl
        states = ["AL", "CA", "NY", "TX", "FL"]
        names = ["Smith", "Jones", "Doe"]
        rows = [{"last_name": n, "state": s} for s in states for n in names]
        df = pl.DataFrame(rows)

        config = _build_config_from_contexts(contexts, df)
        assert config is not None
        primary_fields = config.blocking.keys[0].fields
        assert "state" in primary_fields, f"Expected 'state' in blocking keys, got {primary_fields}"

    def test_geo_compounds_with_string_fallback(self):
        """When no NAME columns but STRING + GEO exist, blocking should compound geo + string."""
        from goldenpipe.adapters.match import _build_config_from_contexts
        from goldenpipe.models.column_context import ColumnType

        # Simulate hospital data: facility_name is STRING (not NAME), state is GEO
        contexts = [
            MagicMock(name="facility_name", inferred_type=ColumnType.STRING, is_identifier=False),
            MagicMock(name="state", inferred_type=ColumnType.GEO, is_identifier=False),
            MagicMock(name="address", inferred_type=ColumnType.STRING, is_identifier=False),
        ]
        contexts[0].name = "facility_name"
        contexts[1].name = "state"
        contexts[2].name = "address"

        import polars as pl
        # Need enough unique facility names to pass cardinality filter (min 50)
        states = ["AL", "CA", "NY", "TX", "FL"]
        names = [f"HOSPITAL {i}" for i in range(60)]
        rows = [{"facility_name": n, "state": s, "address": f"123 {s} ST"}
                for s in states for n in names]
        df = pl.DataFrame(rows)

        config = _build_config_from_contexts(contexts, df)
        assert config is not None
        primary_fields = config.blocking.keys[0].fields
        assert "state" in primary_fields, f"Expected 'state' in blocking keys, got {primary_fields}"
        assert "facility_name" in primary_fields

    def test_no_geo_columns_stays_name_only(self):
        """Without geo columns, blocking should remain name-only."""
        from goldenpipe.adapters.match import _build_config_from_contexts
        from goldenpipe.models.column_context import ColumnType

        contexts = [
            MagicMock(name="last_name", inferred_type=ColumnType.NAME, is_identifier=True),
            MagicMock(name="email", inferred_type=ColumnType.EMAIL, is_identifier=True),
        ]
        contexts[0].name = "last_name"
        contexts[1].name = "email"

        import polars as pl
        df = pl.DataFrame({"last_name": ["Smith", "Jones", "Doe"], "email": ["a@b", "c@d", "e@f"]})

        config = _build_config_from_contexts(contexts, df)
        assert config is not None
        # Should use last_name soundex, no geo compound
        primary_fields = config.blocking.keys[0].fields
        assert primary_fields == ["last_name"]

    def test_high_null_geo_excluded(self):
        """Geo columns with >20% nulls should not be used for compound blocking."""
        from goldenpipe.adapters.match import _build_config_from_contexts
        from goldenpipe.models.column_context import ColumnType

        contexts = [
            MagicMock(name="last_name", inferred_type=ColumnType.NAME, is_identifier=True),
            MagicMock(name="state", inferred_type=ColumnType.GEO, is_identifier=False),
        ]
        contexts[0].name = "last_name"
        contexts[1].name = "state"

        import polars as pl
        # 40% nulls in state
        df = pl.DataFrame({
            "last_name": ["Smith", "Jones", "Doe", "Brown", "Lee"],
            "state": ["AL", "CA", None, None, "NY"],
        })
        config = _build_config_from_contexts(contexts, df)
        assert config is not None
        # Should fall back to name-only since geo has high null rate
        primary_fields = config.blocking.keys[0].fields
        assert primary_fields == ["last_name"]

    def test_lowest_cardinality_geo_selected(self):
        """When multiple geo columns exist, lowest cardinality should be picked."""
        from goldenpipe.adapters.match import _build_config_from_contexts
        from goldenpipe.models.column_context import ColumnType

        contexts = [
            MagicMock(name="facility_name", inferred_type=ColumnType.STRING, is_identifier=False),
            MagicMock(name="state", inferred_type=ColumnType.GEO, is_identifier=False),
            MagicMock(name="citytown", inferred_type=ColumnType.GEO, is_identifier=False),
        ]
        contexts[0].name = "facility_name"
        contexts[1].name = "state"
        contexts[2].name = "citytown"

        import polars as pl
        # state has 5 unique, citytown has 60 unique — state should win (lowest cardinality)
        names = [f"HOSPITAL {i}" for i in range(60)]
        states = ["AL", "CA", "NY", "TX", "FL"]
        cities = [f"CITY_{i}" for i in range(60)]
        rows = [{"facility_name": names[i], "state": states[i % 5], "citytown": cities[i]}
                for i in range(60)]
        df = pl.DataFrame(rows)

        config = _build_config_from_contexts(contexts, df)
        assert config is not None
        primary_fields = config.blocking.keys[0].fields
        assert "state" in primary_fields, f"Expected 'state' (lowest cardinality), got {primary_fields}"

    def test_soundex_not_applied_to_geo(self):
        """Soundex passes should only apply to name fields, not geo columns."""
        from goldenpipe.adapters.match import _build_config_from_contexts
        from goldenpipe.models.column_context import ColumnType

        contexts = [
            MagicMock(name="last_name", inferred_type=ColumnType.NAME, is_identifier=True),
            MagicMock(name="state", inferred_type=ColumnType.GEO, is_identifier=False),
        ]
        contexts[0].name = "last_name"
        contexts[1].name = "state"

        import polars as pl
        rows = [{"last_name": f"Name{i}", "state": s}
                for s in ["AL", "CA", "NY"] for i in range(20)]
        df = pl.DataFrame(rows)

        config = _build_config_from_contexts(contexts, df)
        assert config is not None
        # Check that soundex passes only apply to name field, not compound geo+name
        for p in config.blocking.passes:
            if "soundex" in p.transforms:
                assert p.fields == ["last_name"], \
                    f"Soundex should only apply to name field, got fields={p.fields}"
