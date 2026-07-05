"""Alias parity for the goldenmatch MCP server. Box-safe: needs goldenmatch[mcp].
Run: POLARS_SKIP_CPU_CHECK=1 GOLDENMATCH_NATIVE=0 python -m pytest tests/test_mcp_aliases.py -v"""
import pytest
from goldenmatch.mcp import server as gm

EXPECTED_ALIASES = {
    "dedupe": "find_duplicates",
    "match": "match_record",
    "explain_pair": "explain_match",
    "profile": "profile_data",
    "explain_cluster": "agent_explain_cluster",
}


def test_alias_map_is_exactly_the_five_pairs():
    assert gm._MCP_TOOL_ALIASES == EXPECTED_ALIASES


def test_resolve_alias_maps_each_alias_to_canonical():
    for alias, canonical in EXPECTED_ALIASES.items():
        assert gm._resolve_alias(alias) == canonical
    assert gm._resolve_alias("find_duplicates") == "find_duplicates"
    assert gm._resolve_alias("nonexistent") == "nonexistent"


def test_aliases_are_advertised_in_the_base_component():
    base_names = {t.name for t in gm._BASE_TOOLS}
    assert set(EXPECTED_ALIASES) <= base_names


def test_aliases_appear_in_TOOLS_union():
    names = {t.name for t in gm.TOOLS}
    assert set(EXPECTED_ALIASES) <= names


def test_alias_schema_matches_canonical():
    by_name = {t.name: t for t in gm.TOOLS}
    for alias, canonical in EXPECTED_ALIASES.items():
        assert by_name[alias].inputSchema == by_name[canonical].inputSchema
        assert canonical in (by_name[alias].description or "")
