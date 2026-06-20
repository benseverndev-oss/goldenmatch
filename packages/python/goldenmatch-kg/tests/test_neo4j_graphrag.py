"""Tests for the neo4j-graphrag shim.

test_resolve_records_groups_variants -- BASE-FREE test; imports only
    goldenmatch_kg.neo4j_graphrag._resolve (no neo4j_graphrag dep).
    Runs locally wherever goldenmatch is installed.

test_goldenmatch_resolver_real_pipeline -- gated by pytest.importorskip.
    Skips locally (neo4j-graphrag not installed); runs in CI (Task 6 installs
    the neo4j-graphrag extra).
"""
import pytest

# ── Local / BASE-FREE test (no neo4j_graphrag import) ────────────────────────

def test_resolve_records_groups_variants():
    """resolve_records puts Apple Inc + Apple together and Microsoft apart.

    This test imports ONLY goldenmatch_kg.neo4j_graphrag._resolve -- the helper
    that has no dependency on neo4j_graphrag. It exercises the goldenmatch-backed
    clustering decision that the shim uses, and runs locally without the framework
    extra installed.
    """
    from goldenmatch_kg.neo4j_graphrag._resolve import resolve_records

    items = [
        ("1", "Apple Inc", "org"),
        ("2", "Apple", "org"),
        ("3", "Microsoft", "org"),
    ]
    groups = resolve_records(items)

    # resolve_records returns only multi-member groups.
    # Apple Inc and Apple should merge; Microsoft stays alone (no multi-member group).
    multi = {frozenset(g) for g in groups}
    assert frozenset({"1", "2"}) in multi, (
        f"Expected Apple Inc + Apple to merge; got groups: {groups}"
    )
    # Microsoft must NOT appear in any multi-member group.
    for g in groups:
        assert "3" not in g, f"Microsoft should be a singleton; got group: {g}"


# ── CI-only test (requires neo4j_graphrag extra) ─────────────────────────────

neo4j_graphrag = pytest.importorskip(
    "neo4j_graphrag",
    reason="neo4j-graphrag extra not installed; skipping real-pipeline test",
)


def test_goldenmatch_resolver_real_pipeline():
    """GoldenMatchResolver.resolve_entities_for_test merges variants correctly.

    Skips locally (neo4j_graphrag not installed); verified in CI (Task 6).
    The driver is I/O only for the clustering decision and is MagicMock()'d
    here, matching the pattern in real_resolvers.py::neo4j_graphrag_fuzzy_clusters.
    """
    from unittest.mock import MagicMock

    from goldenmatch_kg.neo4j_graphrag import GoldenMatchResolver

    resolver = GoldenMatchResolver(driver=MagicMock())
    groups = resolver.resolve_entities_for_test([
        ("1", "Apple Inc", "org"),
        ("2", "Apple", "org"),
        ("3", "Microsoft", "org"),
    ])

    norm = {frozenset(g) for g in groups}
    assert frozenset({"1", "2"}) in norm, (
        f"Expected Apple Inc + Apple to merge; got groups: {groups}"
    )
    assert frozenset({"3"}) in norm, (
        f"Expected Microsoft to be a singleton; got groups: {groups}"
    )
