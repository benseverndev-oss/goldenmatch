"""Tests for MCP server tools."""
import pytest

try:
    from goldenpipe.mcp.server import list_stages_tool, validate_pipeline_tool
    HAS_MCP = True
except ImportError:
    HAS_MCP = False

pytestmark = pytest.mark.skipif(not HAS_MCP, reason="mcp not installed")


class TestListStagesTool:
    def test_returns_dict(self):
        result = list_stages_tool()
        assert isinstance(result, dict)


class TestValidatePipelineTool:
    def test_empty_pipeline(self):
        result = validate_pipeline_tool(pipeline="test", stages=[])
        assert "valid" in result
