"""Curated tool-listing tests for goldensuite-mcp.

The aggregator composes ~88 tools across six packages. A flat namespace that
large swamps LLM tool-selection, so ``list_tools`` is filtered to a curated
headline set by default. The ``GOLDENSUITE_MCP_TOOLS`` env var controls it:

- unset / ``curated`` -> the headline set (default)
- ``full``            -> every aggregated tool (no filtering)
- ``a,b,c``           -> exactly those names

Filtering only affects ``list_tools``; dispatch stays complete, so a hidden
tool is still callable by exact name.
"""
from __future__ import annotations


def _full_names() -> set[str]:
    from goldensuite_mcp.server import _aggregate

    tools, _ = _aggregate()
    return {t.name for t in tools}


def test_curated_is_default_and_smaller_than_full(monkeypatch):
    monkeypatch.delenv("GOLDENSUITE_MCP_TOOLS", raising=False)
    from goldensuite_mcp.server import _aggregate, _apply_tool_filter

    tools, _ = _aggregate()
    listed = _apply_tool_filter(tools)
    listed_names = {t.name for t in listed}

    full = _full_names()
    assert listed_names, "curated listing is empty"
    assert listed_names < full, "curated should be a strict subset of the full surface"
    # Sanity: the curated set is meaningfully smaller (the whole point).
    assert len(listed_names) <= len(full) // 2


def test_curated_surfaces_headline_tools(monkeypatch):
    """The primary verb of each package must be in the default listing."""
    monkeypatch.delenv("GOLDENSUITE_MCP_TOOLS", raising=False)
    from goldensuite_mcp.server import _aggregate, _apply_tool_filter

    tools, _ = _aggregate()
    listed = {t.name for t in _apply_tool_filter(tools)}
    # One anchor per package (intersected with what actually loaded).
    full = {t.name for t in tools}
    for anchor in ("analyze_data", "agent_deduplicate", "scan", "transform",
                   "run_pipeline", "apply", "analyze_frame"):
        if anchor in full:  # some tools gate behind optional extras
            assert anchor in listed, f"headline tool {anchor!r} was filtered out"


def test_curated_hides_advanced_tools(monkeypatch):
    monkeypatch.delenv("GOLDENSUITE_MCP_TOOLS", raising=False)
    from goldensuite_mcp.server import _aggregate, _apply_tool_filter

    tools, _ = _aggregate()
    listed = {t.name for t in _apply_tool_filter(tools)}
    full = {t.name for t in tools}
    # identity graph + memory admin are advanced surface -> hidden by default.
    for hidden in ("identity_resolve", "identity_merge", "memory_export", "rollback"):
        if hidden in full:
            assert hidden not in listed, f"{hidden!r} should be hidden in curated mode"


def test_full_profile_returns_everything(monkeypatch):
    monkeypatch.setenv("GOLDENSUITE_MCP_TOOLS", "full")
    from goldensuite_mcp.server import _aggregate, _apply_tool_filter

    tools, _ = _aggregate()
    listed = {t.name for t in _apply_tool_filter(tools)}
    assert listed == {t.name for t in tools}


def test_custom_list_profile(monkeypatch):
    monkeypatch.setenv("GOLDENSUITE_MCP_TOOLS", "scan, transform ,analyze_data")
    from goldensuite_mcp.server import _aggregate, _apply_tool_filter

    tools, _ = _aggregate()
    full = {t.name for t in tools}
    listed = {t.name for t in _apply_tool_filter(tools)}
    # Exactly the requested names that actually exist (whitespace tolerated).
    assert listed == ({"scan", "transform", "analyze_data"} & full)


def test_hidden_tool_still_dispatchable_by_name(monkeypatch):
    """Filtering is list-only: a hidden tool remains routable through dispatch."""
    monkeypatch.delenv("GOLDENSUITE_MCP_TOOLS", raising=False)
    from goldensuite_mcp.server import _aggregate, _apply_tool_filter

    tools, name_to_dispatch = _aggregate()
    listed = {t.name for t in _apply_tool_filter(tools)}
    if "identity_resolve" in name_to_dispatch:
        assert "identity_resolve" not in listed
        # Still present in the dispatch table -> callable by exact name.
        assert "identity_resolve" in name_to_dispatch


def test_curated_only_names_that_exist(monkeypatch):
    """Every curated name should be a real tool somewhere in the suite, so the
    headline list can't silently rot into dead references."""
    monkeypatch.setenv("GOLDENSUITE_MCP_TOOLS", "full")
    from goldensuite_mcp.server import CURATED_TOOLS, _aggregate

    tools, _ = _aggregate()
    full = {t.name for t in tools}
    # Allow a small tolerance for tools gated behind optional extras that aren't
    # installed in this environment (documents_*, upload_dataset, etc.), but the
    # bulk must resolve.
    unknown = CURATED_TOOLS - full
    assert len(unknown) <= 3, f"curated names not found in the suite: {sorted(unknown)}"
