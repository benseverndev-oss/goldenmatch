# goldenmatch-kg: Graphiti

A post-ingestion re-resolution pass: run goldenmatch over Graphiti's existing entity nodes
to find and merge the duplicates its deterministic floor + LLM missed. Graphiti exposes no
public resolver injection point (its dedup lives in private helpers), so this is a
maintenance step over public node objects, not an in-line plugin -- which keeps it robust to
Graphiti's internal churn.

## Install

```bash
pip install "goldenmatch-kg[graphiti]"
```

## Use

```python
from goldenmatch_kg.graphiti import propose_entity_merges

# `nodes` is a list of graphiti_core.nodes.EntityNode you have fetched from the graph.
merge_groups = propose_entity_merges(nodes)
# -> [["<uuid-apple-inc>", "<uuid-apple>"], ...]  (each group = node uuids that are one entity)

# Apply each group: pick one uuid as canonical, re-point the other nodes' edges to it,
# and delete the duplicates (via your Graphiti client / driver).
```

`propose_entity_merges` is the goldenmatch-backed decision (name-only ER over the node
names) and is the supported v1 API. A convenience `resolve_existing_entities(client)` that
also fetches the nodes from a live Graphiti client is a near-term follow-up: Graphiti's
public node-list API is pinned against the installed version in CI before it ships, so for
now you fetch the entity nodes yourself and pass them to `propose_entity_merges`.

## The lift

On the ER-KG-Bench ghsuite corpus, Graphiti's real deterministic dedup floor (run in-process)
scores **F1 0.379**; goldenmatch zero-config scores **F1 0.969**. Re-resolving Graphiti's
entities goldenmatch's way recovers about **+59pp** of that gap, at zero LLM calls (Graphiti's
own full path escalates unresolved nodes to an LLM). See `RESULTS_ghsuite.md` in the bench.
