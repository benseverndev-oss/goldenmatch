"""Tests for MCP server tools."""
import polars as pl
import pytest

try:
    from goldenpipe.mcp.server import (
        _jail_path,
        _result_to_dict,
        _summarize_output,
        explain_pipeline_tool,
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


class TestPathJail:
    """The run_pipeline `source`/`config_path` reads are jailed to the working
    dir (network-exposed MCP hardening) -- a path escaping the root is rejected."""

    def test_jail_allows_paths_inside_cwd(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        f = tmp_path / "data.csv"
        f.write_text("x\n")
        assert _jail_path("data.csv") == str(f.resolve())
        assert _jail_path(str(f)) == str(f.resolve())

    def test_jail_rejects_escape(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with pytest.raises(ValueError):
            _jail_path("/etc/passwd")
        with pytest.raises(ValueError):
            _jail_path("../../etc/passwd")

    def test_jail_rejects_nul_byte(self):
        with pytest.raises(ValueError):
            _jail_path("a\x00b")

    def test_jail_honors_allowed_root_env(self, tmp_path, monkeypatch):
        root = tmp_path / "allowed"
        root.mkdir()
        monkeypatch.setenv("GOLDENPIPE_ALLOWED_ROOT", str(root))
        assert _jail_path(str(root / "ok.csv")) == str((root / "ok.csv").resolve())
        with pytest.raises(ValueError):
            _jail_path(str(tmp_path / "outside.csv"))

    def test_escape_message_is_generic(self, tmp_path, monkeypatch):
        # Public unauthenticated endpoint: the error must NOT leak the resolved
        # absolute path or the server's root directory.
        monkeypatch.chdir(tmp_path)
        try:
            _jail_path("/etc/passwd")
            raise AssertionError("expected ValueError")
        except ValueError as exc:
            msg = str(exc)
        assert msg == "path is outside the allowed root"
        assert "/etc/passwd" not in msg
        assert str(tmp_path) not in msg

    def test_run_pipeline_rejects_escaping_source(self, tmp_path, monkeypatch):
        # A traversal `source` must be refused as data (no file read), not opened.
        monkeypatch.chdir(tmp_path)
        out = run_pipeline_tool(source="/etc/passwd")
        assert "error" in out
        assert "outside the allowed root" in out["error"]

    def test_run_pipeline_rejects_escaping_config_path(self, tmp_path, monkeypatch):
        # `config_path` is jailed too -- a traversal is refused as data, not opened.
        monkeypatch.chdir(tmp_path)
        out = run_pipeline_tool(config_path="/etc/shadow")
        assert "error" in out
        assert "outside the allowed root" in out["error"]

    def test_run_pipeline_missing_config_returns_error(self, tmp_path, monkeypatch):
        # A jailed-but-nonexistent config must return an error payload, not crash
        # the request (load_config raises FileNotFoundError past the jail check).
        monkeypatch.chdir(tmp_path)
        out = run_pipeline_tool(config_path="nonexistent.yml")
        assert out.get("error") == "failed to load config"

    def test_explain_pipeline_rejects_escaping_config_path(self, tmp_path, monkeypatch):
        # explain_pipeline reads config_path too -- it must be jailed the same way.
        monkeypatch.chdir(tmp_path)
        out = explain_pipeline_tool(config_path="/etc/passwd")
        assert "error" in out
        assert "outside the allowed root" in out["error"]

    def test_explain_pipeline_missing_config_returns_error(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        out = explain_pipeline_tool(config_path="nonexistent.yml")
        assert out.get("error") == "failed to load config"
