"""Graphiti post-ingestion re-resolution pass using goldenmatch.

Graphiti exposes no public resolver injection point (entity dedup is handled
internally via private dedup_helpers). This shim is a MAINTENANCE PASS applied
after ingestion: query the existing EntityNodes in the live graph, run goldenmatch
over them, and merge the duplicates that Graphiti's floor + LLM missed.

No private Graphiti symbols are used.

Seam (confirmed from erkgbench/real_resolvers.py; to be pinned against the
installed graphiti-core version in CI -- Task 6):

    EntityNode import:
        from graphiti_core.nodes import EntityNode
    Constructor (confirmed from bench code):
        EntityNode(name: str, group_id: str = "")
        Attributes: .uuid (str, auto-generated), .name (str), .group_id (str)

    Public client node-list API:
        Graphiti client (graphiti_core.Graphiti) provides an async method
        get_nodes_by_query / node search. The exact method name and signature
        is to be confirmed in CI (Task 6) against the installed version.
        # TODO(CI): pin the exact client node-list method and signature.

    Public merge API:
        As of graphiti-core >=0.10.0 (pre-release target), no standalone
        public "merge two EntityNodes" method was confirmed in the bench code.
        resolve_existing_entities therefore RETURNS the proposed merge groups
        as data (list[list[str]] of uuids) rather than applying them. The caller
        is responsible for re-pointing edges and deleting the duplicate nodes.
        # TODO(CI): check whether a public merge method exists in the installed
        # version and upgrade to apply-in-place if it does.

Install with: pip install goldenmatch-kg[graphiti]

Usage::

    from goldenmatch_kg.graphiti import propose_entity_merges
    from graphiti_core.nodes import EntityNode

    nodes = [
        EntityNode(name="Apple Inc", group_id=""),
        EntityNode(name="Apple", group_id=""),
        EntityNode(name="Microsoft", group_id=""),
    ]
    merge_groups = propose_entity_merges(nodes)
    # merge_groups = [["<uuid-apple-inc>", "<uuid-apple>"]]
    # Each group is a list of uuids that should be merged into one entity.
"""
from __future__ import annotations

from goldenmatch_kg.graphiti._resolve import propose_merges

try:
    import graphiti_core.nodes  # pyright: ignore[reportMissingImports]  # noqa: F401

    _GRAPHITI_AVAILABLE = True
except ImportError:
    _GRAPHITI_AVAILABLE = False


def propose_entity_merges(nodes: list) -> list[list[str]]:
    """Identify duplicate EntityNodes using goldenmatch.

    Pure decision function: takes a list of Graphiti EntityNode objects and
    returns the merge groups (no DB access). This is the core seam used by
    resolve_existing_entities after it queries the live graph.

    Args:
        nodes: list of graphiti_core.nodes.EntityNode objects.

    Returns:
        list of merge groups; each group is a list of node uuids. Only
        multi-member groups are returned (singletons need no action).

    Raises:
        ImportError: if graphiti-core is not installed.
    """
    if not _GRAPHITI_AVAILABLE:
        raise ImportError(
            "propose_entity_merges requires graphiti-core. "
            "Install with: pip install goldenmatch-kg[graphiti]"
        )

    items = [(node.uuid, node.name) for node in nodes]
    return propose_merges(items)


async def resolve_existing_entities(
    client,
    group_id: str | None = None,
) -> list[list[str]]:
    """Query the live Graphiti graph and propose merges for duplicate entities.

    Post-ingestion maintenance pass: queries existing EntityNodes from the
    Graphiti graph store (optionally scoped to a group_id), runs goldenmatch
    over the names, and returns the merge proposals.

    NOTE: This function returns the proposed merge groups as data. No merges
    are applied automatically because graphiti-core (>=0.10.0) does not expose
    a standalone public node-merge method. The caller is responsible for
    re-pointing edges and deleting duplicate nodes.
    # TODO(CI): upgrade to apply merges in-place if a public merge method is
    # confirmed in the installed graphiti-core version.

    Args:
        client: a graphiti_core.Graphiti client instance.
        group_id: optional group_id to scope the query to a specific subgraph.
            When None, all entity nodes in the graph are considered.

    Returns:
        list of merge groups (list[list[str]] of uuids). Apply each group by
        selecting one uuid as the canonical node and re-pointing all edges from
        the others to it, then deleting the duplicate nodes.

    Raises:
        ImportError: if graphiti-core is not installed.
        # TODO(CI): pin the exact client node-list method and update the query call.
    """
    if not _GRAPHITI_AVAILABLE:
        raise ImportError(
            "resolve_existing_entities requires graphiti-core. "
            "Install with: pip install goldenmatch-kg[graphiti]"
        )

    # TODO(CI): pin the exact public method for listing entity nodes from the client.
    # The bench code uses graphiti_core.nodes.EntityNode directly (constructing nodes
    # from raw data), not a client query. Confirm the public node-list API against the
    # installed version and replace the stub below.
    #
    # Candidate (to verify in CI):
    #   nodes = await client.get_nodes_by_query(
    #       query="", group_ids=[group_id] if group_id is not None else None
    #   )
    #
    # For now, raise NotImplementedError to make it clear this path needs CI wiring.
    raise NotImplementedError(
        "resolve_existing_entities requires a confirmed public client node-list API. "
        "See the TODO(CI) comment in goldenmatch_kg/graphiti/__init__.py. "
        "Use propose_entity_merges(nodes) with nodes you have already fetched "
        "from the graph, then apply the returned merge groups yourself."
    )
