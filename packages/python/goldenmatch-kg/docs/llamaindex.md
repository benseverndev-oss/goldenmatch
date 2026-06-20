# goldenmatch-kg: LlamaIndex PropertyGraphIndex

A `GoldenMatchEntityResolver` transform that canonicalizes extracted entity names with
goldenmatch before they are upserted into the graph store. LlamaIndex PropertyGraphIndex
ships **no** fuzzy entity resolver -- its default is exact name+label upsert, so variant
mentions ("Apple" vs "Apple Inc") each become their own node. This transform resolves the
variants and rewrites each node's `.name` to its group's canonical form, so the downstream
exact upsert then collapses them into one node. The integration is therefore additive:
exact-only to real ER. Node ids and relationships are left untouched (only names change).

## Install

```bash
pip install "goldenmatch-kg[llamaindex]"
```

## Use

```python
from goldenmatch_kg.llamaindex import GoldenMatchEntityResolver
from llama_index.core import PropertyGraphIndex

index = PropertyGraphIndex.from_documents(
    documents,
    kg_extractors=[...],                  # your entity/relation extractor(s)
    transformations=[GoldenMatchEntityResolver()],  # canonicalize entities before upsert
)
```

`GoldenMatchEntityResolver` is a `TransformComponent` (sync `__call__` + async `acall`); only
`EntityNode`s are touched, other node types pass through unchanged. For a unit test without a
full index, `GoldenMatchEntityResolver().resolve_nodes(nodes)` runs the same canonicalization
in-process.

## The lift

LlamaIndex's real default resolves only byte-identical names, so on real surface-form
variation it behaves like the exact-match family, which scores **F1 0.0 to 0.24** on the
ER-KG-Bench ghsuite corpus (mem0 0.0, MS-GraphRAG 0.119, Cognee 0.244; the bench's modeled
LlamaIndex-PGI row scores 0.095). goldenmatch zero-config scores **F1 0.969** on the same
corpus. This transform adds that real ER to a LlamaIndex pipeline that otherwise has none.
See `RESULTS_ghsuite.md` in the bench.
