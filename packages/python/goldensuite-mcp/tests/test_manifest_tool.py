"""Tests for the `suite_manifest` navigation meta-tool.

It serves progressive-disclosure slices of the generated agent manifest
(docs/agent-manifest.json) so an agent can look up config / CLI / MCP tools /
vocabularies / env knobs / source locations without grepping. These pin: it's
in the default listing, the overview enumerates every package, a package+section
slice and the rust_crates slice return data, and keyword search finds a known
vocab value with where it's defined.
"""
from __future__ import annotations

from goldensuite_mcp.manifest_tool import load_manifest, make_dispatch


def _have_manifest() -> bool:
    return load_manifest() is not None


def test_manifest_tool_in_default_listing(monkeypatch):
    monkeypatch.delenv("GOLDENSUITE_MCP_TOOLS", raising=False)
    from goldensuite_mcp.server import _aggregate, _apply_tool_filter

    tools, _ = _aggregate()
    listed = {t.name for t in _apply_tool_filter(tools)}
    assert "suite_manifest" in listed


def test_overview_lists_every_package():
    if not _have_manifest():
        return  # manifest only present in a repo checkout
    out = make_dispatch()("suite_manifest", {})
    assert set(out["packages"]) == {
        "goldenmatch", "goldencheck", "goldenflow", "goldenpipe", "infermap", "goldenanalysis"
    }
    assert out["rust_crates"] > 0
    assert out["packages"]["goldenmatch"]["mcp_tools"] > 0


def test_package_section_slice():
    if not _have_manifest():
        return
    out = make_dispatch()("suite_manifest", {"package": "goldenmatch", "section": "vocab"})
    titles = {v["title"] for v in out["data"]}
    assert "Scorers" in titles


def test_rust_crates_slice():
    if not _have_manifest():
        return
    out = make_dispatch()("suite_manifest", {"section": "rust_crates", "query": "score"})
    assert out["count"] > 0
    assert all("score" in c["name"].lower() or "score" in (c.get("path") or "").lower()
               for c in out["crates"])


def test_search_finds_vocab_value_with_location():
    if not _have_manifest():
        return
    out = make_dispatch()("suite_manifest", {"query": "jaro_winkler"})
    kinds = {h["kind"] for h in out["hits"]}
    assert "vocab_value" in kinds
    hit = next(h for h in out["hits"] if h["kind"] == "vocab_value" and h["value"] == "jaro_winkler")
    assert hit["package"] == "goldenmatch"
    assert hit["vocab"] == "Scorers"


def test_missing_manifest_is_graceful(monkeypatch, tmp_path):
    # Point at a nonexistent path and clear the cache -> a clean error, not a crash.
    import goldensuite_mcp.manifest_tool as mt

    monkeypatch.setenv("GOLDENSUITE_MANIFEST_PATH", str(tmp_path / "nope.json"))
    mt._CACHE.clear()
    out = make_dispatch()("suite_manifest", {})
    mt._CACHE.clear()
    assert "error" in out
