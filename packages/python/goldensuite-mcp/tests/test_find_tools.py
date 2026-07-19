"""Tests for the `suite_find_tools` discovery meta-tool.

`list_tools` shows only the curated headline set, which would leave the ~80
non-headline tools undiscoverable. `suite_find_tools` returns the full catalog
(optionally filtered) so a client can find a hidden tool and call it by exact
name. These tests pin: it's in the default listing, it enumerates the full
surface, keyword/package filters work, hidden tools show up in it, and it does
not list itself.
"""
from __future__ import annotations


def test_find_tools_is_in_default_listing(monkeypatch):
    monkeypatch.delenv("GOLDENSUITE_MCP_TOOLS", raising=False)
    from goldensuite_mcp.server import _aggregate, _apply_tool_filter

    tools, _ = _aggregate()
    listed = {t.name for t in _apply_tool_filter(tools)}
    assert "suite_find_tools" in listed


def test_find_tools_dispatch_returns_full_catalog(monkeypatch):
    from goldensuite_mcp.server import _aggregate

    tools, dispatch = _aggregate()
    # The catalog is every REAL tool -- not the suite-native meta-tools
    # (suite_find_tools lists itself never; suite_manifest is navigation, not a
    # callable data tool, and registers after this snapshot).
    real_tool_names = {t.name for t in tools} - {"suite_find_tools", "suite_manifest"}
    out = dispatch["suite_find_tools"]("suite_find_tools", {})
    returned = {r["name"] for r in out["tools"]}
    assert out["count"] == len(real_tool_names)
    assert returned == real_tool_names
    assert "suite_find_tools" not in returned
    assert "suite_manifest" not in returned


def test_find_tools_entries_carry_schema(monkeypatch):
    from goldensuite_mcp.server import _aggregate

    tools, dispatch = _aggregate()
    out = dispatch["suite_find_tools"]("suite_find_tools", {})
    entry = out["tools"][0]
    assert set(entry) == {"name", "package", "description", "inputSchema"}
    assert isinstance(entry["inputSchema"], dict)


def test_find_tools_keyword_filter(monkeypatch):
    from goldensuite_mcp.server import _aggregate

    tools, dispatch = _aggregate()
    full = {t.name for t in tools}
    if "identity_resolve" not in full:  # gated behind optional extras
        return
    out = dispatch["suite_find_tools"]("suite_find_tools", {"query": "identity"})
    names = {r["name"] for r in out["tools"]}
    assert names, "expected identity_* matches"
    assert all("identity" in n or "identity" in (r["description"] or "").lower()
               for n, r in ((r["name"], r) for r in out["tools"]))
    assert "identity_resolve" in names


def test_find_tools_package_filter(monkeypatch):
    from goldensuite_mcp.server import _aggregate

    tools, dispatch = _aggregate()
    out = dispatch["suite_find_tools"]("suite_find_tools", {"package": "goldencheck"})
    assert out["tools"], "expected goldencheck tools"
    assert all(r["package"] == "goldencheck" for r in out["tools"])
    assert "scan" in {r["name"] for r in out["tools"]}


def test_find_tools_surfaces_hidden_tools(monkeypatch):
    """A tool hidden from the curated listing must still be discoverable here."""
    monkeypatch.delenv("GOLDENSUITE_MCP_TOOLS", raising=False)
    from goldensuite_mcp.server import _aggregate, _apply_tool_filter

    tools, dispatch = _aggregate()
    listed = {t.name for t in _apply_tool_filter(tools)}
    out = dispatch["suite_find_tools"]("suite_find_tools", {})
    discoverable = {r["name"] for r in out["tools"]}
    full = {t.name for t in tools}
    if "identity_merge" in full:
        assert "identity_merge" not in listed  # hidden from list_tools
        assert "identity_merge" in discoverable  # but found via search
