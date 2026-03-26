from goldenflow.mcp.server import TOOLS, handle_tool


def test_mcp_tools_defined():
    assert len(TOOLS) >= 4
    names = [t["name"] for t in TOOLS]
    assert "transform" in names
    assert "map" in names
    assert "profile" in names
    assert "learn" in names


def test_mcp_expanded_tools():
    """Verify all 10 tools are defined."""
    assert len(TOOLS) == 10
    names = [t["name"] for t in TOOLS]
    assert "diff" in names
    assert "validate" in names
    assert "list_transforms" in names
    assert "explain_transform" in names
    assert "list_domains" in names
    assert "select_from_findings" in names


def test_all_tools_have_schemas():
    """Every tool must have name, description, and inputSchema."""
    for tool in TOOLS:
        assert "name" in tool
        assert "description" in tool
        assert "inputSchema" in tool
        assert tool["inputSchema"]["type"] == "object"


def test_list_transforms_tool():
    """list_transforms returns a list of transform dicts."""
    import json
    result = json.loads(handle_tool("list_transforms", {}))
    assert isinstance(result, list)
    assert len(result) > 0
    assert "name" in result[0]
    assert "mode" in result[0]


def test_list_domains_tool():
    """list_domains returns domain pack info."""
    import json
    result = json.loads(handle_tool("list_domains", {}))
    assert isinstance(result, list)


def test_explain_transform_known():
    """explain_transform returns info for a known transform."""
    import json
    # Get a transform name from list_transforms
    transforms = json.loads(handle_tool("list_transforms", {}))
    if transforms:
        name = transforms[0]["name"]
        result = json.loads(handle_tool("explain_transform", {"transform_name": name}))
        assert result["name"] == name


def test_explain_transform_unknown():
    """explain_transform returns error for unknown transform."""
    import json
    result = json.loads(handle_tool("explain_transform", {"transform_name": "nonexistent_xyz"}))
    assert "error" in result


def test_unknown_tool():
    import json
    result = json.loads(handle_tool("unknown_tool_xyz", {}))
    assert "error" in result
