"""Tests for MCP server tools."""
import polars as pl
import pytest

try:
    from goldenpipe.mcp.server import (
        _result_to_dict,
        _summarize_output,
        list_stages_tool,
        run_pipeline_tool,
        validate_pipeline_tool,
    )
    HAS_MCP = True
except ImportError:
    HAS_MCP = False

pytestmark = pytest.mark.skipif(not HAS_MCP, reason="mcp not installed")

try:
    import goldenmatch  # noqa: F401
    HAS_MATCH = True
except ImportError:
    HAS_MATCH = False

# Four records; the two Smiths are the same person (name variant, same email).
_DUP_RECORDS = [
    {"first_name": "Jon", "last_name": "Smith", "email": "jsmith@x.com", "city": "NYC"},
    {"first_name": "John", "last_name": "Smith", "email": "jsmith@x.com", "city": "NYC"},
    {"first_name": "Mary", "last_name": "Jones", "email": "mjones@y.com", "city": "LA"},
    {"first_name": "Bob", "last_name": "Lee", "email": "blee@z.com", "city": "SF"},
]


class TestListStagesTool:
    def test_returns_dict(self):
        result = list_stages_tool()
        assert isinstance(result, dict)


class TestValidatePipelineTool:
    def test_empty_pipeline(self):
        result = validate_pipeline_tool(pipeline="test", stages=[])
        assert "valid" in result


class TestResultSerialization:
    """Pure serialization — hermetic, no sibling tools needed."""

    def test_result_to_dict_shape(self):
        from goldenpipe.models.context import (
            PipeResult,
            PipeStatus,
            StageResult,
            StageStatus,
        )
        res = PipeResult(
            status=PipeStatus.SUCCESS, source="x", input_rows=3,
            stages={
                "load": StageResult(status=StageStatus.SUCCESS),
                "d": StageResult(status=StageStatus.FAILED, error="boom"),
            },
            reasoning={"d": "why"}, timing={"d": 0.12345678},
            skipped=["s"], errors=["e"],
        )
        out = _result_to_dict(res)
        assert out["status"] == "success"
        assert out["input_rows"] == 3
        assert out["stages"]["load"] == {"status": "success"}
        assert out["stages"]["d"] == {"status": "failed", "error": "boom"}
        assert out["timing"]["d"] == 0.1235  # rounded to 4 dp
        assert out["reasoning"] == {"d": "why"}
        assert out["skipped"] == ["s"]
        assert out["errors"] == ["e"]
        assert "output" not in out  # no artifacts -> no output block

    def test_summarize_output_from_frames(self):
        arts = {
            "golden": pl.DataFrame({"a": ["x", "y"]}),
            "unique": pl.DataFrame({"a": ["z"]}),
            "dupes": pl.DataFrame({"a": ["x2", "y2"]}),
            "match_stats": {"match_rate": 0.5},
            "clusters": [1, 2, 3],
        }
        out = _summarize_output(arts, preview_rows=1)
        assert out["golden_records"] == 2
        assert out["golden_preview"] == [{"a": "x"}]
        assert out["unique_records"] == 1
        assert out["duplicate_records"] == 2
        assert out["match_stats"] == {"match_rate": 0.5}
        assert out["cluster_count"] == 3

    def test_preview_rows_bounded_and_zero(self):
        arts = {"golden": pl.DataFrame({"a": list("abcde")})}
        # over-large preview is clamped to the frame height (<=100)
        assert len(_summarize_output(arts, preview_rows=1000)["golden_preview"]) == 5
        # zero preview -> count only, no preview key
        zero = _summarize_output(arts, preview_rows=0)
        assert zero["golden_records"] == 5
        assert "golden_preview" not in zero


class TestRunPipelineInputs:
    def test_no_input_returns_error(self):
        out = run_pipeline_tool()
        assert "error" in out

    def test_explicit_empty_stages_is_load_only(self):
        # stages=[] is an explicit empty pipeline (just the load stage) — hermetic,
        # exercises the inline-records path without any sibling tool.
        out = run_pipeline_tool(records=[{"a": 1}, {"a": 2}], stages=[])
        assert out["status"] == "success"
        assert out["input_rows"] == 2
        assert "load" in out["stages"]
        assert "output" not in out  # load produces no golden/unique

    def test_csv_text_input(self):
        out = run_pipeline_tool(csv_text="a,b\n1,2\n3,4\n", stages=[])
        assert out["status"] == "success"
        assert out["input_rows"] == 2


class TestRunPipelineDedupe:
    @pytest.mark.skipif(not HAS_MATCH, reason="goldenmatch not installed")
    def test_inline_dedupe_returns_output(self):
        out = run_pipeline_tool(
            records=_DUP_RECORDS, stages=["goldenmatch.dedupe"], preview_rows=5,
        )
        assert out["status"] == "success"
        assert out["input_rows"] == 4
        assert out["stages"]["goldenmatch.dedupe"]["status"] == "success"
        # The reason this enrichment exists: the deduped output is returned.
        output = out["output"]
        assert output["golden_records"] >= 1
        assert isinstance(output["golden_preview"], list)
        assert output["golden_preview"]  # non-empty
        assert output["match_stats"]["total_records"] == 4
