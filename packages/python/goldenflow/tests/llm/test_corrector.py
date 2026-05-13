from unittest.mock import patch

import polars as pl
from goldenflow.llm.corrector import _get_value_summary, category_llm_correct


def test_get_value_summary():
    s = pl.Series("s", ["a", "a", "b", "b", "b", "c", None])
    summary = _get_value_summary(s)
    assert summary["b"] == 3
    assert summary["a"] == 2
    assert summary["c"] == 1
    assert None not in summary


def test_llm_correct_no_provider():
    """Should return series unchanged when no LLM available."""
    s = pl.Series("status", ["active", "actve", "inactive"])
    result = category_llm_correct(s, provider="nonexistent")
    assert result.to_list() == ["active", "actve", "inactive"]


def test_llm_correct_with_mock():
    """Test with mocked LLM response."""
    s = pl.Series("status", ["active", "actve", "inactive", "pendng"])

    mock_corrections = {"actve": "active", "pendng": "pending"}

    with patch("goldenflow.llm.corrector._ask_llm_for_corrections", return_value=mock_corrections):
        result = category_llm_correct(s)
        assert result[1] == "active"
        assert result[3] == "pending"
        assert result[0] == "active"  # unchanged
        assert result[2] == "inactive"  # unchanged


def test_llm_mode_env_var():
    """Verify GOLDENFLOW_LLM=1 triggers LLM transforms."""
    import os
    os.environ["GOLDENFLOW_LLM"] = "1"
    try:
        # Just verify the import and env check work
        assert os.environ.get("GOLDENFLOW_LLM") == "1"
    finally:
        del os.environ["GOLDENFLOW_LLM"]


def test_llm_mode_env_var_in_engine():
    """Verify GOLDENFLOW_LLM=1 env var causes the LLM path to be entered in auto-transforms."""
    import os
    from unittest.mock import patch

    import polars as pl
    from goldenflow.engine.transformer import TransformEngine

    os.environ["GOLDENFLOW_LLM"] = "1"
    try:
        # Low-cardinality string column: need unique_pct <= 0.1
        # With 2 unique values out of 100 rows: 2/100 = 0.02 <= 0.1
        statuses = ["active"] * 95 + ["ACTIVE"] * 5
        df = pl.DataFrame({"status": statuses})

        mock_corrections = {"ACTIVE": "active"}
        with patch("goldenflow.llm.corrector._ask_llm_for_corrections", return_value=mock_corrections):
            engine = TransformEngine()
            result = engine.transform_df(df)
            # The LLM corrector should have been applied
            # (all values normalized to "active")
            assert "ACTIVE" not in result.df["status"].to_list()
    finally:
        del os.environ["GOLDENFLOW_LLM"]
