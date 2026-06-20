"""Tests for the LlamaIndex shim.

A deterministic stand-in for goldenmatch's decision is injected (see conftest.py)
so the tests assert the shim's binding/marshaling -- the canonical-name map and the
in-place node-name rewrite -- not goldenmatch's fuzzy accuracy (covered elsewhere).
The stub merges names sharing a first token ("Acme Corporation" + "Acme") with the
longest name as canonical, so the shorter mention really is rewritten.
"""
import pytest

_RESOLVE = "goldenmatch_kg.llamaindex._resolve"

_ITEMS = [
    ("1", "Acme Corporation", "org"),
    ("2", "Acme", "org"),
    ("3", "Globex", "org"),
]


def test_canonical_names_collapses_variants(patch_resolve):
    """canonical_names maps the two Acme mentions to one canonical name; Globex keeps its own."""
    patch_resolve(_RESOLVE)
    from goldenmatch_kg.llamaindex._resolve import canonical_names

    result = canonical_names(_ITEMS)
    assert result["1"] == result["2"] == "Acme Corporation"  # variants share the longest name
    assert result["3"] == "Globex"                           # distinct entity unchanged
    assert len(set(result.values())) == 2


def test_resolver_canonicalizes_entity_variants(patch_resolve):
    """GoldenMatchEntityResolver (real TransformComponent) rewrites EntityNode names to canonical."""
    pytest.importorskip("llama_index.core")
    patch_resolve(_RESOLVE)
    from goldenmatch_kg.llamaindex import GoldenMatchEntityResolver
    from llama_index.core.graph_stores.types import EntityNode

    nodes = [
        EntityNode(name="Acme Corporation", label="org"),
        EntityNode(name="Acme", label="org"),
        EntityNode(name="Globex", label="org"),
    ]
    out = GoldenMatchEntityResolver().resolve_nodes(nodes)
    names = {n.name for n in out}
    assert names == {"Acme Corporation", "Globex"}   # "Acme" was rewritten to the canonical
