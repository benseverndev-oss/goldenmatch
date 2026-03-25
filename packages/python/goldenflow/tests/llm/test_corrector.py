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
