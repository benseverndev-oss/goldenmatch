# goldenmatch-kg

Drop-in [goldenmatch](https://pypi.org/project/goldenmatch/) entity resolution for
knowledge-graph frameworks.

**What this is:** goldenmatch is an entity-resolution engine, not a KG builder. KG
pipelines (neo4j-graphrag, LlamaIndex, Graphiti, ...) ingest text, extract entities and
relationships, **resolve/dedupe the entities**, and write a graph. This package drops
goldenmatch in as that resolve stage, where each framework exposes a seam:

- **neo4j-graphrag** -- a real `GoldenMatchResolver` you pass into the pipeline (true
  in-pipeline plugin; replaces the built-in `FuzzyMatchResolver`).
- **LlamaIndex PropertyGraphIndex** -- a `GoldenMatchEntityResolver` transform that
  canonicalizes entity names before upsert. LlamaIndex ships no fuzzy resolver of its
  own (its default is exact name+label upsert), so this is additive: exact-only to real ER.
- **Graphiti** -- a post-ingestion `propose_entity_merges` pass over the graph's existing
  entity nodes (Graphiti exposes no public resolver seam, so this runs as a maintenance
  step, not in-line).

One framework-agnostic core (`resolve_entities`) does all the goldenmatch work; each
adapter just marshals its framework's entities in and the merge decision out.

## Install

```bash
pip install goldenmatch-kg                        # core only
pip install "goldenmatch-kg[neo4j-graphrag]"      # + neo4j-graphrag adapter
pip install "goldenmatch-kg[llamaindex]"          # + LlamaIndex adapter
pip install "goldenmatch-kg[graphiti]"            # + Graphiti adapter
```

## The lift (measured, not asserted)

These adapters deliver goldenmatch's resolution through each framework's pipeline. The
size of the win is measured by [ER-KG-Bench](https://github.com/benseverndev-oss/goldenmatch/tree/main/packages/python/goldenmatch/benchmarks/er-kg-bench),
which scores each framework's own default entity resolution against goldenmatch on real
corpora. On the self-sourced **ghsuite** corpus (`RESULTS_ghsuite.md`):

| Framework      | its default ER (F1) | goldenmatch (F1) |
| -------------- | ------------------- | ---------------- |
| neo4j-graphrag | 0.322 (fuzzy resolver, real-inproc) | **0.969** |
| LlamaIndex PGI | exact upsert only (exact-match family scores 0.0 to 0.24) | **0.969** |
| Graphiti       | 0.379 (deterministic floor, real-inproc) | **0.969** |

goldenmatch (F1) is the `goldenmatch(auto+fields)` row (P 0.963 / R 0.975 / F1 0.969),
deterministic, zero LLM calls. The lift is the resolution you get by swapping goldenmatch
in as the ER stage; it is read off the existing bench board, not re-scored here. The
same shape holds on the external Wikidata/RxNorm corpus (`RESULTS.md`).

This is an ER-stage win, not "goldenmatch builds your KG": you still run the rest of your
pipeline (extraction, relationship building, graph store). goldenmatch resolves the entities.

## Per-framework guides

- [neo4j-graphrag](docs/neo4j-graphrag.md)
- [LlamaIndex](docs/llamaindex.md)
- [Graphiti](docs/graphiti.md)

## License

MIT
