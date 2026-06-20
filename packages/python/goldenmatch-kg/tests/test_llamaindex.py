"""Tests for the LlamaIndex shim.

test_canonical_names_collapses_variants -- BASE-FREE test; imports only
    goldenmatch_kg.llamaindex._resolve (no llama_index dep).
    Runs locally wherever goldenmatch is installed.

test_resolver_canonicalizes_entity_variants -- gated by pytest.importorskip.
    Skips locally (llama-index-core not installed); runs in CI (Task 6 installs
    the llamaindex extra).
"""
import pytest

# ── Local / BASE-FREE test (no llama_index import) ───────────────────────────


def test_canonical_names_collapses_variants():
    """canonical_names maps Apple Inc + Apple to the same canonical name.

    This test imports ONLY goldenmatch_kg.llamaindex._resolve -- the helper
    that has no dependency on llama_index. It exercises the goldenmatch-backed
    name-canonicalization decision that the shim uses, and runs locally without
    the framework extra installed.
    """
    from goldenmatch_kg.llamaindex._resolve import canonical_names

    items = [
        ("1", "Apple Inc", "org"),
        ("2", "Apple", "org"),
        ("3", "Microsoft", "org"),
    ]
    result = canonical_names(items)

    # Both Apple variants must map to the same canonical name.
    assert result["1"] == result["2"], (
        f"Expected Apple Inc + Apple to share a canonical name; "
        f"got id 1 -> {result['1']!r}, id 2 -> {result['2']!r}"
    )
    # Microsoft must be distinct from the Apple canonical name.
    assert result["3"] != result["1"], (
        f"Expected Microsoft to have a different canonical name from Apple; "
        f"got {result['3']!r} == {result['1']!r}"
    )


# ── CI-only test (requires llama-index-core extra) ───────────────────────────


def test_resolver_canonicalizes_entity_variants():
    """GoldenMatchEntityResolver.resolve_nodes collapses Apple variants to one name.

    Skips locally (llama_index.core not installed); verified in CI (Task 6).
    Constructs real EntityNode instances, runs them through resolve_nodes (the
    test seam over the same _canonicalize path the transform pipeline uses),
    and asserts that:
      - The two Apple variants share a single canonical name post-transform.
      - Microsoft is unchanged (distinct name).
      - The set of distinct names across all output nodes is exactly 2.
    """
    pytest.importorskip(
        "llama_index.core",
        reason="llama-index-core extra not installed; skipping real-pipeline test",
    )

    from goldenmatch_kg.llamaindex import GoldenMatchEntityResolver
    from llama_index.core.graph_stores.types import EntityNode  # type: ignore[import]

    nodes = [
        EntityNode(name="Apple Inc", label="org"),
        EntityNode(name="Apple", label="org"),
        EntityNode(name="Microsoft", label="org"),
    ]

    out = GoldenMatchEntityResolver().resolve_nodes(nodes)

    # After canonicalization, distinct names should be exactly 2.
    names = {n.name for n in out}
    assert len(names) == 2, (
        f"Expected 2 distinct canonical names after resolving 3 nodes "
        f"(Apple Inc + Apple -> 1, Microsoft -> 1); got {names}"
    )
    # Microsoft must still be in the output under its own name.
    assert "Microsoft" in names, (
        f"Expected Microsoft to be unchanged; got names: {names}"
    )
