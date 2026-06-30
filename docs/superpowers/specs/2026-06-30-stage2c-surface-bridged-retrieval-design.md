# Stage-2-C: Surface-Bridged Retrieval — Design

**Status:** Approved (brainstorm), pending implementation plan.
**Date:** 2026-06-30
**Context:** goldengraph real-corpus (stage-2) quality. Follows two diagnostics that redirected the
lever twice:
1. Stage-2-A verdict named SYNTHESIS the top fixable bucket (18 retrieved-but-wrong).
2. Stage-2-B self-consistency = HONEST-NULL (7B errors systematic, not variance).
3. Stage-2-C diagnostic #1: the SYNTHESIS bucket is **contaminated by connectivity failures** —
   6-of-15 traced cases have `same_component=False` (gold answer in the ball but in a DIFFERENT
   connected component from the seeds → unreachable, not a reasoning miss).
4. Stage-2-C diagnostic #2: turning on the cross-doc-link flag (`GOLDENGRAPH_CROSS_DOC_LINK=1`) did
   NOT fix it (N=20: islands persist 4-of-7, fragmentation ~5.6 ent/component, answer_match 0.10 — no
   lift) and **did not complete N=50 under the 90-min cap** (O(N) per-doc store matching). Ruled out:
   ineffective + too expensive.

## Goal

Connect under-merged graph components **at retrieval time** by unioning same-NAME siblings as the ball
grows, so a multi-hop answer stranded behind a split bridge-entity enters the retrieved subgraph
connected to the seeds. **Opt-in, measure-first**; default off = today's `_retrieve_local`, byte-for-byte.

## Why this lever (and why it differs from the ruled-out one)

The graph the 7B builds on real prose is fragmented: the same entity mentioned across paragraphs gets
SEPARATE nodes (under-merge), so the bridge entity that should connect seed→answer is split into a
sink-copy (no out-edge) and a source-copy (owns the next hop) — different ids, not connected. The
embedding-based cross-doc-link flag (diagnostic #2) tries to fix this at BUILD time and failed
(ineffective + too expensive). This lever attacks a DIFFERENT mechanism — **name-based** under-merge at
RETRIEVAL time, zero build cost — reusing `_bridge_surfaces`, the proven `trace_chain` mechanism
(measured: 27-of-29 multi-hop walk deaths landed on an under-merge sink-copy).

## Architecture

Two pieces, both in `goldengraph/answer.py`.

### 1. `_retrieve_local_bridged(slice_graph, seeds, *, max_hops, node_budget)` (new)

An iterative-frontier variant of `_retrieve_local`. The current `_retrieve_local` re-expands
`query(seeds, h)` from the FIXED seed set, so a split bridge-entity strands the walk. The bridged
variant instead accumulates a ball hop-by-hop and, **at each hop, bridges the reached frontier across
same-name siblings** via the existing `_bridge_surfaces`:

```
frontier = seeds
ents: dict[entity_id -> entity]   # dedup by entity_id
edges: list ; seen: set            # dedup edges by the (subj, predicate, obj) tuple
for hop in range(max_hops):
    sub = slice_graph.query(list(frontier), 1)
    id_to_name = {e["entity_id"]: e["canonical_name"] for e in sub["entities"]}
    for e in sub["entities"]: ents.setdefault(e["entity_id"], e)
    for ed in sub["edges"]:
        k = (ed["subj"], ed["predicate"], ed["obj"])
        if k not in seen: seen.add(k); edges.append(ed)
    if len(ents) >= node_budget: break
    reached = set(id_to_name)                                   # the entity ids in sub
    frontier = _bridge_surfaces(slice_graph, reached, id_to_name)  # union same-name siblings
return {"entities": list(ents.values()), "edges": edges}        # accumulated, connectivity-closed ball
```

Edge dedup uses the `(subj, predicate, obj)` tuple (edges are plain dicts with no natural id);
entity dedup uses `entity_id`.

So the source-copy of a split bridge-entity joins the next expansion, its out-edge becomes reachable,
and the answer enters the ball connected. `node_budget` bounds the accumulation (early-exit), so a
popular-name frontier cannot blow the ball up.

### 2. Gate in `ask`

`GOLDENGRAPH_RETRIEVAL_BRIDGE` (default off, read at call time). When set, `ask` routes BOTH the local
and hybrid paths through `_retrieve_local_bridged` instead of `_retrieve_local`. One env check at the
call site (`answer.py` ~line 287, `subgraph = _retrieve_local(...)`); everything downstream (synthesis,
seed-name handoff, provenance collection) is unchanged.

## Data flow

```
ask -> seed_by_query -> (RETRIEVAL_BRIDGE? _retrieve_local_bridged : _retrieve_local) -> synthesis
```

The bridged ball is a connectivity-superset of the plain ball: it contains the same seed neighborhood
plus the same-name siblings' neighborhoods that close the under-merge gaps.

## Error handling

- Empty seeds → same fallback as `_retrieve_local` (`slice_graph.query(seeds, max_hops)`).
- Default-off path is byte-identical to today (the gate selects the unchanged `_retrieve_local`).
- `_bridge_surfaces` already handles missing names (`id_to_name.get(i)`), so a nameless id just
  contributes itself.

## Testing

Pure, box-safe (stub graph; no LLM, no native).

- **Coupling proof (the mechanism):** reuse the existing `_split_graph` fixture from
  `test_chain_retrieval.py` — `A -acquired-> B(id1, sink)`, `B(id4) -part_of-> C`, same name "B",
  different ids, NOT connected. Two asserts:
  - the **plain** `_retrieve_local` ball seeded at A does NOT contain `C` (strands at the sink-copy);
  - the **bridged** `_retrieve_local_bridged` ball seeded at A DOES contain `C` (per-hop bridging unions
    `B(id1)`↔`B(id4)`, so `part_of→C` enters the ball).
  - **Plan note:** assert-1 (plain ball lacks C) holds *because the test stub's `query` ignores `hops`
    and re-expands the fixed seed* — that is exactly what makes the sink-copy strand reproduce in the
    fixture. Do NOT "fix" the stub to honor `hops`; the fixture proves the BRIDGING mechanism, not the
    depth-growth of the real `query`.
- **No-strand baseline:** on a CONNECTED graph (no under-merge), bridged and plain balls both contain
  the answer entity (bridging doesn't break the easy case).
- **Budget bound:** a graph with a popular name (many same-name siblings) still respects `node_budget`
  (early-exit fires; the ball does not explode).
- **Empty seeds:** falls back identically to `_retrieve_local`.
- **Integration validation** = the N=20 MuSiQue run.

## Scope / YAGNI

- **Default off** — opt-in `GOLDENGRAPH_RETRIEVAL_BRIDGE`; the plain path stays the shipped default,
  byte-identical.
- **No extraction / synthesis / cross-doc-link changes** — purely a retrieval-ball variant.
- **No frontier re-ranking or relation-focusing** (relation-focusing was already measured WORSE — see
  the `_retrieve_local` docstring). Just bridge + expand + budget.

## Validation gate + honest-null readiness

- **Run:** N=20 MuSiQue, `GOLDENGRAPH_RETRIEVAL_BRIDGE=1` (cheap — retrieval-time, so it completes under
  the cap, unlike cross-doc-link).
- **Falsifiable, two outcomes:**
  - **`same_component=False` islands drop AND answer_match rises** → SUCCESS: the disconnection was
    name-based under-merge; bridging fixes it. Ship as the opt-in lever, record the win.
  - **Flat** → the disconnection is GENUINE (not name-based under-merge), and the **construction-ceiling
    finding is earned**: real-corpus multi-hop is bottlenecked by 7B graph-construction quality, and the
    cheap post-hoc levers (voting, linking, bridging) are exhausted. Record it plainly; the remaining fix
    is a bigger program (stronger extractor / hybrid-passage). **No tuning to force a number.**

This is the last cheap connectivity lever; the gate makes either outcome a clean result.

## Files

- Modify: `packages/python/goldengraph/goldengraph/answer.py` (`_retrieve_local_bridged` + the
  `RETRIEVAL_BRIDGE` gate at the `_retrieve_local` call site in `ask`).
- Create: `packages/python/goldengraph/tests/test_retrieval_bridge.py` (coupling + baseline + budget +
  empty-seeds tests).
- Validation: existing `scripts/distill/modal_bench.py --corpus musique` (env opt; no bench change).
- Report: `docs/superpowers/reports/2026-06-30-stage2c-surface-bridged-retrieval.md` (win or
  construction-ceiling null).
