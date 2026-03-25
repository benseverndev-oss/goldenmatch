from goldenflow.mcp.server import TOOLS


def test_mcp_tools_defined():
    assert len(TOOLS) >= 4
    names = [t["name"] for t in TOOLS]
    assert "transform" in names
    assert "map" in names
    assert "profile" in names
    assert "learn" in names
