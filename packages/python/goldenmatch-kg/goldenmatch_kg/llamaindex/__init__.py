"""LlamaIndex GoldenMatchEntityResolver: drops goldenmatch in as the entity-resolution
stage of LlamaIndex PropertyGraphIndex.

IMPORTING THIS MODULE REQUIRES the `llama-index-core` extra to be installed. The
base class (TransformComponent) and EntityNode are imported at module top so that
GoldenMatchEntityResolver can be a real subclass at class-definition time. This is
intentional and standard: users import `goldenmatch_kg.llamaindex` ONLY after
`pip install goldenmatch-kg[llamaindex]`.

Seam (confirmed shape, to be pinned against installed version in CI - Task 6):
    LlamaIndex PropertyGraphIndex ships NO built-in fuzzy entity resolver.
    The default behaviour is exact name+label upsert at the graph store, so
    variant mentions ("Apple" vs "Apple Inc") each get their own node.

    The integration point is llama_index.core.indices.property_graph via
    transformations: a list of TransformComponent instances that process
    extracted nodes before upsert. Each TransformComponent implements:

        async def acall(self, nodes, **kwargs) -> list[BaseNode]:
            ...

    and the sync equivalent __call__ (via the base class's default delegation).

    GoldenMatchEntityResolver is a TransformComponent that:
      1. Extracts the EntityNode subset from the incoming node list.
      2. Groups them by label (type).
      3. Calls goldenmatch via canonical_names() to identify the canonical
         name for each entity mention.
      4. Rewrites each EntityNode's .name to the canonical name in-place.
         NOTE: node ids are NOT rewritten (rewriting ids can orphan edges
         that reference those ids in other transform stages). Name-only
         canonicalization is sufficient: the downstream exact name+label upsert
         at the graph store collapses all variants that now share a name.
      5. Returns the full node list (EntityNode + any other node types).

    EntityNode constructor (confirmed by plan; to be verified against installed
    version in CI - Task 6):
        EntityNode(name: str, label: str = "node", properties: dict = {})
        Attributes: .name (str), .label (str), .id (str, auto-generated uuid),
                    .properties (dict)

    TransformComponent (llama_index.core.schema):
        Abstract base for pipeline transforms. Subclasses implement __call__
        (sync) and/or acall (async). The base provides default delegation.

    # TODO(CI): confirm EntityNode constructor + TransformComponent base
    # against the installed llama-index-core version in Task 6 CI.

Usage::

    from goldenmatch_kg.llamaindex import GoldenMatchEntityResolver
    from llama_index.core.graph_stores.types import EntityNode

    nodes = [
        EntityNode(name="Apple Inc", label="org"),
        EntityNode(name="Apple",     label="org"),
        EntityNode(name="Microsoft", label="org"),
    ]
    resolver = GoldenMatchEntityResolver()
    out = resolver.resolve_nodes(nodes)
    # All Apple* nodes now share the same .name ("Apple Inc" -- longest form).
    # Microsoft is unchanged. {n.name for n in out} == {"Apple Inc", "Microsoft"}.
"""
from __future__ import annotations

from goldenmatch_kg.llamaindex._resolve import canonical_names

try:
    from llama_index.core.graph_stores.types import (
        EntityNode,  # pyright: ignore[reportMissingImports]
    )
    from llama_index.core.schema import (  # pyright: ignore[reportMissingImports]
        TransformComponent,
    )

    _LLAMA_INDEX_AVAILABLE = True
except ImportError:
    _LLAMA_INDEX_AVAILABLE = False


def _canonicalize_nodes(nodes: list, entity_node_cls: type) -> list:
    """Rewrite EntityNode names to canonical forms using goldenmatch.

    Shared implementation used by both GoldenMatchEntityResolver (when
    llama-index-core is installed) and resolve_nodes (standalone seam).

    Args:
        nodes: mixed list of LlamaIndex node objects (EntityNode + others).
        entity_node_cls: the EntityNode class to isinstance-check against.

    Returns:
        The same list with EntityNode .name fields rewritten to their
        group's canonical name. Non-EntityNode objects pass through unchanged.
        Node ids and relationships are preserved (name-only rewrite).
    """
    entity_nodes = [n for n in nodes if isinstance(n, entity_node_cls)]
    if not entity_nodes:
        return nodes

    items: list[tuple[str, str, str]] = [
        (n.id, n.name, n.label) for n in entity_nodes
    ]
    cnames = canonical_names(items)

    for node in entity_nodes:
        canonical = cnames.get(node.id)
        if canonical is not None:
            node.name = canonical

    return nodes


if _LLAMA_INDEX_AVAILABLE:

    class GoldenMatchEntityResolver(TransformComponent):  # type: ignore[misc]
        """Replaces LlamaIndex's default exact-name upsert with goldenmatch canonicalization.

        Add this to the `transformations` list of a PropertyGraphIndex (or the
        extractor pipeline) to run before the graph-store upsert. It rewrites each
        EntityNode's `.name` to the canonical name for its variant group, so that
        the subsequent exact name+label upsert at the graph store collapses all
        surface-form variants into a single node.

        Only EntityNode instances are touched; other node types pass through
        unchanged.

        Example::

            from goldenmatch_kg.llamaindex import GoldenMatchEntityResolver

            index = PropertyGraphIndex.from_documents(
                docs,
                transformations=[
                    ...,                          # extractor(s)
                    GoldenMatchEntityResolver(),  # canonicalize before upsert
                ],
            )
        """

        def __call__(self, nodes: list, **kwargs) -> list:
            """Sync transform: canonicalize EntityNode names before upsert.

            Args:
                nodes: list of LlamaIndex node objects from prior pipeline stages.
                **kwargs: forwarded from the pipeline runner (ignored).

            Returns:
                The node list with EntityNode names canonicalized.
            """
            return _canonicalize_nodes(nodes, EntityNode)

        async def acall(self, nodes: list, **kwargs) -> list:  # type: ignore[override]
            """Async transform: canonicalize EntityNode names before upsert.

            goldenmatch is synchronous; wraps __call__ for async pipelines.
            """
            return _canonicalize_nodes(nodes, EntityNode)

        def resolve_nodes(self, nodes: list) -> list:
            """Test seam: run name-canonicalization in-process.

            Exercises the SAME goldenmatch-backed canonicalization path that the
            transform pipeline uses, without requiring a live LlamaIndex index.
            Useful for unit testing without setting up a full PropertyGraphIndex.

            Args:
                nodes: list of EntityNode (or mixed node types).

            Returns:
                The same list with EntityNode .name fields canonicalized.
            """
            return _canonicalize_nodes(nodes, EntityNode)

else:

    class GoldenMatchEntityResolver:  # type: ignore[no-redef]
        """Stub raised when llama-index-core is not installed.

        Install with: pip install goldenmatch-kg[llamaindex]
        """

        def __init__(self, *args, **kwargs) -> None:
            raise ImportError(
                "GoldenMatchEntityResolver requires llama-index-core. "
                "Install with: pip install goldenmatch-kg[llamaindex]"
            )
