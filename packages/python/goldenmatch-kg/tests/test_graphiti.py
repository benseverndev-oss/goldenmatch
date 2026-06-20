"""Tests for the Graphiti post-ingestion re-resolution shim.

A deterministic stand-in for goldenmatch's decision is injected (see conftest.py)
so the tests assert the shim's binding/marshaling (uuid extraction + multi-member
group emission), not goldenmatch's fuzzy accuracy (covered elsewhere). The stub
merges names sharing a first token ("Acme Corporation" + "Acme").
"""
import pytest

_RESOLVE = "goldenmatch_kg.graphiti._resolve"


def test_propose_merges_groups_variants(patch_resolve):
    """propose_merges returns the duplicate uuids as a multi-member group, omitting singletons."""
    patch_resolve(_RESOLVE)
    from goldenmatch_kg.graphiti._resolve import propose_merges

    groups = propose_merges([
        ("u1", "Acme Corporation"),
        ("u2", "Acme"),
        ("u3", "Globex"),
    ])
    merged = {frozenset(g) for g in groups}
    assert frozenset({"u1", "u2"}) in merged                 # the Acme mentions merge
    assert all("u3" not in g for g in groups)                # Globex omitted (singleton)


def test_reresolution_groups_existing_entity_variants(patch_resolve):
    """propose_entity_merges over real Graphiti EntityNodes returns the duplicate uuids together."""
    pytest.importorskip("graphiti_core")
    patch_resolve(_RESOLVE)
    from goldenmatch_kg.graphiti import propose_entity_merges
    from graphiti_core.nodes import EntityNode

    nodes = [
        EntityNode(name="Acme Corporation", group_id=""),
        EntityNode(name="Acme", group_id=""),
        EntityNode(name="Globex", group_id=""),
    ]
    proposals = propose_entity_merges(nodes)
    merged = {frozenset(p) for p in proposals}
    # The two Acme nodes' uuids land in one merge group; Globex is not proposed.
    acme_uuids = frozenset(n.uuid for n in nodes[:2])
    assert acme_uuids in merged
    assert all(nodes[2].uuid not in p for p in proposals)
