"""Tests for the neo4j-graphrag shim.

Both tests inject a deterministic stand-in for goldenmatch's decision (see
conftest.py) and assert the shim's binding/marshaling -- NOT goldenmatch's fuzzy
accuracy on toy data (covered by test_core.py's real-dedupe_df parity test and by
ER-KG-Bench). The stub merges names that share a first token ("Acme Corporation" +
"Acme") with the longest name as canonical, which is stable across goldenmatch
versions and processes.
"""
import pytest

_RESOLVE = "goldenmatch_kg.neo4j_graphrag._resolve"

_ITEMS = [
    ("1", "Acme Corporation", "org"),
    ("2", "Acme", "org"),
    ("3", "Globex", "org"),
]


def test_resolve_records_groups_variants(patch_resolve):
    """resolve_records merges same-entity mentions per label and emits multi-member groups."""
    patch_resolve(_RESOLVE)
    from goldenmatch_kg.neo4j_graphrag._resolve import resolve_records

    groups = resolve_records(_ITEMS)
    multi = {frozenset(g) for g in groups}
    assert frozenset({"1", "2"}) in multi          # the two Acme mentions merge
    for g in groups:
        assert "3" not in g                        # Globex stays out (singletons omitted)


def test_goldenmatch_resolver_real_pipeline(patch_resolve):
    """GoldenMatchResolver (real neo4j-graphrag subclass) returns the resolution as a full partition."""
    pytest.importorskip("neo4j_graphrag")
    patch_resolve(_RESOLVE)
    from unittest.mock import MagicMock

    from goldenmatch_kg.neo4j_graphrag import GoldenMatchResolver

    resolver = GoldenMatchResolver(driver=MagicMock())  # driver is I/O only; unused for the decision
    groups = resolver.resolve_entities_for_test(_ITEMS)
    norm = {frozenset(g) for g in groups}
    assert frozenset({"1", "2"}) in norm           # Acme mentions merged
    assert frozenset({"3"}) in norm                # Globex is a singleton (full partition)
