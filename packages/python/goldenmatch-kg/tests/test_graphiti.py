"""Tests for the Graphiti post-ingestion re-resolution shim.

test_propose_merges_groups_variants -- BASE-FREE (no graphiti_core dep).
    Imports goldenmatch_kg.graphiti._resolve directly and verifies that
    propose_merges returns a group containing both Apple variants and omits
    Microsoft (singletons are not returned). Runs locally.

test_reresolution_groups_existing_entity_variants -- SKIPPED locally.
    Requires graphiti_core (pytest.importorskip inside the test). Constructs
    real EntityNodes, calls the public propose_entity_merges seam, and asserts
    the same merge-group contract. Passes in CI once graphiti-core is installed.
"""
import pytest

# ── BASE-FREE test (always runs -- no graphiti_core dependency) ──────────────


def test_propose_merges_groups_variants():
    """propose_merges groups Apple variants together and omits Microsoft singleton.

    This test imports ONLY goldenmatch_kg.graphiti._resolve -- the base-free
    helper with no dependency on graphiti_core. It exercises the goldenmatch-backed
    merge-group decision that the full shim uses, and runs locally without the
    framework extra installed.
    """
    from goldenmatch_kg.graphiti._resolve import propose_merges

    groups = propose_merges([
        ("u1", "Apple Inc"),
        ("u2", "Apple"),
        ("u3", "Microsoft"),
    ])

    # Exactly one merge group (the two Apple variants); Microsoft is a singleton
    # and must NOT appear in any returned group.
    assert len(groups) == 1, f"expected 1 merge group, got {groups!r}"
    group_set = frozenset(groups[0])
    assert group_set == frozenset({"u1", "u2"}), (
        f"expected Apple variants {{u1, u2}} in the merge group, got {groups[0]!r}"
    )
    # u3 (Microsoft) must not appear in any group.
    all_uuids = {uid for g in groups for uid in g}
    assert "u3" not in all_uuids, "Microsoft should be a singleton -- not in any merge group"


# ── CI-only test (requires graphiti-core extra) ───────────────────────────────


def test_reresolution_groups_existing_entity_variants():
    """propose_entity_merges groups Apple EntityNode variants, leaves Microsoft alone.

    Skips locally (graphiti_core not installed); verified in CI (Task 6 installs
    the graphiti extra). Constructs real Graphiti EntityNode instances, runs them
    through propose_entity_merges (the pure-decision seam over real nodes), and
    asserts that:
      - The two Apple variants land in a single merge group.
      - Microsoft does not appear in any merge group (singleton).
    """
    pytest.importorskip(
        "graphiti_core",
        reason="graphiti-core extra not installed; skipping real-node integration test",
    )

    from goldenmatch_kg.graphiti import propose_entity_merges
    from graphiti_core.nodes import EntityNode  # noqa: PLC0415

    nodes = [
        EntityNode(name="Apple Inc", group_id=""),
        EntityNode(name="Apple", group_id=""),
        EntityNode(name="Microsoft", group_id=""),
    ]
    proposals = propose_entity_merges(nodes)
    merged = {frozenset(p) for p in proposals}

    # The two Apple variants should land in one merge group.
    assert any(len(g) == 2 for g in merged), (
        f"expected a 2-member merge group for Apple variants, got {proposals!r}"
    )
    # All groups must be non-empty.
    assert all(len(g) >= 1 for g in merged)

    # Microsoft must not appear in any merge group (it is a singleton).
    all_uuids = {uid for g in merged for uid in g}
    apple_uuids = {n.uuid for n in nodes if "apple" in n.name.lower()}
    microsoft_uuids = {n.uuid for n in nodes if "microsoft" in n.name.lower()}
    assert apple_uuids.issubset(all_uuids), "Apple variant uuids should be in a merge group"
    assert not microsoft_uuids.intersection(all_uuids), (
        "Microsoft uuid should not appear in any merge group"
    )
