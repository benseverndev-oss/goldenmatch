# Goldengraph Hybrid Path-Filter Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a chain-safe, default-off relevance filter that prunes the hybrid-mode subgraph to seeds + anchor-to-anchor shortest paths + a 1-hop halo before synthesis, to remove the off-topic dilution measured in the bench (hybrid 0.420 < passage-RAG 0.520).

**Architecture:** One new pure function `filter_subgraph_to_paths(subgraph, seeds, *, halo=1)` in a new module `goldengraph/subgraph_filter.py` (BFS over the `{entities, edges}` dict, no native/store change). It is applied in `ask()` **only inside the `mode == "hybrid"` branch**, gated by env `GOLDENGRAPH_HYBRID_FILTER=path` read lazily at call time (mirrors `synthesize._literals_enabled`). `local`/`global` modes are byte-identical to before. A bench leg is enabled by one new `workflow_dispatch` input that exports the env; no bench-adapter code change is needed because `ask()` reads the env directly in-process.

**Tech Stack:** Python 3.11, pytest (offline — no LLM/embeddings/native/network), GitHub Actions (`bench-graphrag-qa.yml`).

**Spec:** `docs/superpowers/specs/2026-06-26-goldengraph-hybrid-path-filter-design.md`

---

## File Structure

- **Create** `packages/python/goldengraph/goldengraph/subgraph_filter.py` — the pure path-preserving filter (`filter_subgraph_to_paths` + a private `_shortest_path` BFS). One responsibility: prune a subgraph dict to the chain-relevant core.
- **Create** `packages/python/goldengraph/tests/test_subgraph_filter.py` — unit tests for the pure function (keep-chain, halo, determinism, degenerate cases).
- **Modify** `packages/python/goldengraph/goldengraph/answer.py` — add `_hybrid_filter_mode()` lazy-env reader; apply the filter inside the `mode == "hybrid"` branch.
- **Modify** `packages/python/goldengraph/tests/test_hybrid_synthesis.py` — two `ask()` integration tests toggling the env (filtered vs. full).
- **Modify** `.github/workflows/bench-graphrag-qa.yml` — add `goldengraph_hybrid_filter` input + `GOLDENGRAPH_HYBRID_FILTER` env line in the goldengraph job.

### Test environment note

These tests are pure offline (no native, no LLM, no network) so they run locally, BUT importing `goldengraph` pulls `goldenmatch` which imports `polars` — on this Windows box that hangs on the CPU-check WMI query. Run every test command in this plan with the polars guard prefixed:

```
POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8 python -m pytest ...
```

Run from `packages/python/goldengraph` (so `conftest.py` and the `goldengraph` package resolve). Do NOT run the full suite (`pytest` with no path) — it OOMs the box; run only the two target files named below.

---

## Task 1: The pure path-preserving filter

**Files:**
- Create: `packages/python/goldengraph/goldengraph/subgraph_filter.py`
- Test: `packages/python/goldengraph/tests/test_subgraph_filter.py`

Use @superpowers:test-driven-development — write each failing test first, watch it fail, then implement.

- [ ] **Step 1: Write the failing tests**

Create `packages/python/goldengraph/tests/test_subgraph_filter.py`:

```python
"""Pure tests for the path-preserving hybrid subgraph filter (no native, no LLM,
no network). The filter must be CHAIN-SAFE: every entity on a path between two
anchors survives; only off-path leaves the wide ball dragged in are dropped --
the property the 2026-06-22 topology-blind predicate-focus revert lacked."""

from __future__ import annotations

from goldengraph.subgraph_filter import filter_subgraph_to_paths


def _ent(i, name="n"):
    return {"entity_id": i, "canonical_name": f"{name}{i}", "typ": "concept"}


def _sub(entity_ids, edges):
    """edges: list of (subj, obj) -- predicate filled in."""
    return {
        "entities": [_ent(i) for i in entity_ids],
        "edges": [{"subj": s, "predicate": "rel", "obj": o} for (s, o) in edges],
    }


def _ids(sub):
    return sorted(e["entity_id"] for e in sub["entities"])


def test_keeps_anchor_to_anchor_chain_drops_offtopic_leaves():
    # chain 0->1->2->3 between anchors 0 and 3; 1 also has off-topic leaves 4,5.
    sub = _sub([0, 1, 2, 3, 4, 5], [(0, 1), (1, 2), (2, 3), (1, 4), (4, 5)])
    out = filter_subgraph_to_paths(sub, [0, 3], halo=0)
    assert _ids(out) == [0, 1, 2, 3]  # bridges 1,2 kept; leaves 4,5 dropped
    # edges fully inside the kept set survive; edges touching a dropped node go
    assert {(e["subj"], e["obj"]) for e in out["edges"]} == {(0, 1), (1, 2), (2, 3)}


def test_follows_edges_undirected():
    # answer edge points 3->2 (reverse); path 0->1->2<-3 must still connect 0 and 3.
    sub = _sub([0, 1, 2, 3], [(0, 1), (1, 2), (3, 2)])
    out = filter_subgraph_to_paths(sub, [0, 3], halo=0)
    assert _ids(out) == [0, 1, 2, 3]


def test_halo_keeps_direct_neighbor_of_single_seed():
    # one seed (0) with neighbor 1 and a 2-hop node 2.
    sub = _sub([0, 1, 2], [(0, 1), (1, 2)])
    out1 = filter_subgraph_to_paths(sub, [0], halo=1)
    assert _ids(out1) == [0, 1]  # halo=1 keeps the direct neighbor, not the 2-hop
    out0 = filter_subgraph_to_paths(sub, [0], halo=0)
    assert _ids(out0) == [0]  # halo=0 keeps only the seed


def test_determinism_equal_length_paths_lowest_id_next_hop():
    # two equal-length paths 0->1->3 and 0->2->3; deterministic pick keeps the
    # lowest-id next hop (1), not 2.
    sub = _sub([0, 1, 2, 3], [(0, 1), (0, 2), (1, 3), (2, 3)])
    out = filter_subgraph_to_paths(sub, [0, 3], halo=0)
    assert _ids(out) == [0, 1, 3]
    # stable across repeated calls
    assert _ids(filter_subgraph_to_paths(sub, [0, 3], halo=0)) == [0, 1, 3]


def test_no_seeds_is_noop():
    sub = _sub([0, 1], [(0, 1)])
    assert filter_subgraph_to_paths(sub, [], halo=1) is sub


def test_empty_subgraph_no_crash():
    out = filter_subgraph_to_paths({"entities": [], "edges": []}, [0], halo=1)
    assert out["entities"] == [] and out["edges"] == []


def test_disconnected_seed_pair_no_error():
    # seeds 0 and 2 are in different components; no path -> keep = seeds (+halo).
    sub = _sub([0, 1, 2, 3], [(0, 1), (2, 3)])
    out = filter_subgraph_to_paths(sub, [0, 2], halo=0)
    assert _ids(out) == [0, 2]


def test_isolated_seed_not_in_adjacency():
    # seed 9 has no incident edge -> must not KeyError on adjacency lookup.
    sub = _sub([9, 0, 1], [(0, 1)])
    out = filter_subgraph_to_paths(sub, [9], halo=1)
    assert _ids(out) == [9]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run (from `packages/python/goldengraph`):
```
POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8 python -m pytest tests/test_subgraph_filter.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'goldengraph.subgraph_filter'`.

- [ ] **Step 3: Implement the filter**

Create `packages/python/goldengraph/goldengraph/subgraph_filter.py`:

```python
"""Path-preserving relevance filter for the hybrid synthesis subgraph.

The bench measured hybrid (passages + the FULL seeded ball) BELOW passages-alone:
the off-topic leaves the wide ball drags in dilute the gold passages in the prompt.
This filter removes that dilution WITHOUT stranding the answer chain -- the failure
mode of the 2026-06-22 topology-blind predicate-focus revert (see the
`_retrieve_local` docstring in answer.py). It keeps only:

  * the seed (anchor) entities,
  * every entity on a shortest path between two anchors (the bridges -- by
    construction, so a multi-hop chain is never cut), and
  * each anchor's `halo`-hop neighbourhood (a single anchor's answer is usually a
    direct neighbour, not a node between two anchors).

Edges are treated as UNDIRECTED because hybrid synthesis follows them either way.
Pure Python over the `{entities, edges}` dict -- no native/store dependency, so the
hybrid experiment stays wheel-free.
"""

from __future__ import annotations

from collections import deque


def _shortest_path(adj: dict[int, set[int]], src: int, dst: int) -> list[int]:
    """BFS shortest path `src`->`dst` over undirected `adj`. Neighbours are
    explored in ascending id order, so when several shortest paths exist the one
    via the lowest-id next hop wins -- deterministic. Returns the node list
    (inclusive of both ends), or `[]` if `dst` is unreachable from `src`."""
    if src == dst:
        return [src]
    prev: dict[int, int | None] = {src: None}
    q: deque[int] = deque([src])
    while q:
        u = q.popleft()
        for v in sorted(adj.get(u, ())):
            if v in prev:
                continue
            prev[v] = u
            if v == dst:
                path = [v]
                while prev[path[-1]] is not None:
                    path.append(prev[path[-1]])  # type: ignore[arg-type]
                return list(reversed(path))
            q.append(v)
    return []


def filter_subgraph_to_paths(
    subgraph: dict, seeds: list[int], *, halo: int = 1
) -> dict:
    """Prune `subgraph` (a `{entities, edges}` dict) to the chain-relevant core for
    the given anchor `seeds`. See module docstring for the kept-set definition.

    No seeds, or an empty entity list -> the subgraph is returned UNCHANGED (there
    is nothing to anchor a filter on; mirrors `_retrieve_local`'s `if not seeds`).
    The result is never empty when the input is non-empty: seeds + halo always
    survive."""
    ents = subgraph.get("entities", [])
    edges = subgraph.get("edges", [])
    seed_ids = list(dict.fromkeys(seeds))  # dedup, preserve order, deterministic
    if not seed_ids or not ents:
        return subgraph

    adj: dict[int, set[int]] = {}
    for e in edges:
        a, b = e["subj"], e["obj"]
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)

    keep: set[int] = set(seed_ids)
    # anchor-to-anchor shortest paths -- the bridges of the multi-hop chain.
    for i in range(len(seed_ids)):
        for j in range(i + 1, len(seed_ids)):
            keep.update(_shortest_path(adj, seed_ids[i], seed_ids[j]))
    # halo-hop neighbourhood of each seed (a lone anchor's answer is often a
    # direct neighbour, on no anchor-to-anchor path).
    for s in seed_ids:
        frontier = {s}
        for _ in range(max(halo, 0)):
            nxt: set[int] = set()
            for u in frontier:
                nxt |= adj.get(u, set())
            nxt -= keep
            keep |= nxt
            frontier = nxt
            if not frontier:
                break

    ents2 = [e for e in ents if e["entity_id"] in keep]
    edges2 = [e for e in edges if e["subj"] in keep and e["obj"] in keep]
    return {**subgraph, "entities": ents2, "edges": edges2}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run:
```
POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8 python -m pytest tests/test_subgraph_filter.py -v
```
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldengraph/goldengraph/subgraph_filter.py packages/python/goldengraph/tests/test_subgraph_filter.py
git commit -m "feat(goldengraph): path-preserving subgraph filter (pure)"
```

---

## Task 2: Wire the filter into `ask()` (hybrid only, env-gated)

**Files:**
- Modify: `packages/python/goldengraph/goldengraph/answer.py`
- Test: `packages/python/goldengraph/tests/test_hybrid_synthesis.py`

- [ ] **Step 1: Write the failing integration tests**

Append to `packages/python/goldengraph/tests/test_hybrid_synthesis.py` (the `_FakeGraph`/`_FakeStore`/`_FakePassages`/`StubEmbedder`/`RecordingLLM` fixtures already exist in that file). These use a 3-node chain `Start(0)-works_at->A(1)-acquired->Zeta(2)` plus an off-topic leaf so the filter has something to drop:

```python
def test_ask_hybrid_filter_path_prunes_offtopic_from_synthesis(monkeypatch):
    # Graph: Start(0)-works_at->A(1)-acquired->Zeta(2) chain + off-topic leaf
    # Noise(3) hanging off ZETA (a NON-seed node), NOT off a seed -- so it sits
    # outside every seed's 1-hop halo and the filter drops it. (If Noise hung off
    # the seed A instead, halo=1 would legitimately KEEP it -- see review note.)
    names = ["Start", "A", "Zeta", "Noise"]
    edges = [(0, "works_at", 1), (1, "acquired", 2), (2, "mentions", 3)]
    llm = RecordingLLM()
    store = _FakeStore(_FakeGraph(names, edges))
    embedder = StubEmbedder({"Start": 0, "A": 1, "Zeta": 2, "Noise": 3})
    passages = _FakePassages(["Start works at A.", "A acquired Zeta in 1990."])
    monkeypatch.setenv("GOLDENGRAPH_HYBRID_FILTER", "path")
    ask(
        "Start", store, llm=llm, embedder=embedder, valid_t=1, tx_t=1,
        mode="hybrid", k=2, hops=4, node_budget=64,
        passages=passages, passage_k=7,
    )
    prompt = llm.prompts[-1]
    # seeds = top-2 by cosine = Start(0), A(1) (one-hot). Production default halo=1:
    # path 0-1 keeps {Start,A}; A's halo keeps Zeta(2); Noise(3) hangs off the
    # non-seed Zeta, beyond any seed's 1-hop halo -> DROPPED.
    assert "Start -[works_at]-> A" in prompt
    assert "Noise" not in prompt
    # passages are untouched by the filter (ground-truth context stays whole)
    assert "A acquired Zeta in 1990." in prompt


def test_ask_hybrid_filter_off_keeps_full_ball(monkeypatch):
    names = ["Start", "A", "Zeta", "Noise"]
    edges = [(0, "works_at", 1), (1, "acquired", 2), (2, "mentions", 3)]
    llm = RecordingLLM()
    store = _FakeStore(_FakeGraph(names, edges))
    embedder = StubEmbedder({"Start": 0, "A": 1, "Zeta": 2, "Noise": 3})
    monkeypatch.delenv("GOLDENGRAPH_HYBRID_FILTER", raising=False)
    ask(
        "Start", store, llm=llm, embedder=embedder, valid_t=1, tx_t=1,
        mode="hybrid", k=2, hops=4, node_budget=64, passages=None,
    )
    # control: with the flag off the off-topic leaf is still present (current 0.420)
    assert "Noise" in llm.prompts[-1]


def test_ask_local_mode_ignores_filter_flag(monkeypatch):
    # The filter must touch hybrid ONLY -- local stays byte-identical.
    names = ["Start", "A", "Zeta", "Noise"]
    edges = [(0, "works_at", 1), (1, "acquired", 2), (2, "mentions", 3)]
    llm = RecordingLLM()
    store = _FakeStore(_FakeGraph(names, edges))
    embedder = StubEmbedder({"Start": 0, "A": 1, "Zeta": 2, "Noise": 3})
    monkeypatch.setenv("GOLDENGRAPH_HYBRID_FILTER", "path")
    ask(
        "Start", store, llm=llm, embedder=embedder, valid_t=1, tx_t=1,
        mode="local", k=2, hops=4, node_budget=64,
    )
    assert "Noise" in llm.prompts[-1]  # local ball is unfiltered regardless of flag
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:
```
POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8 python -m pytest tests/test_hybrid_synthesis.py -v -k "filter or local_mode_ignores"
```
Expected: FAIL — `test_ask_hybrid_filter_path_prunes_offtopic_from_synthesis` asserts `"Noise" not in prompt` but the unfiltered ball still contains Noise.

- [ ] **Step 3: Implement the env reader + apply the filter**

In `packages/python/goldengraph/goldengraph/answer.py`:

(a) Add a lazy-env reader near the top of the module (after the imports), mirroring `synthesize._literals_enabled`:

```python
def _hybrid_filter_mode() -> str:
    """Hybrid subgraph filter selector, read at call time. "" / "none" / unset =
    off (pass the full ball; the measured 0.420 control). "path" = path-preserving
    prune (`subgraph_filter.filter_subgraph_to_paths`)."""
    import os

    return os.environ.get("GOLDENGRAPH_HYBRID_FILTER", "").strip().lower()
```

(b) In `ask()`, inside the existing `if mode == "hybrid":` block, apply the filter to `subgraph` BEFORE retrieving passages and calling `synthesize_hybrid`. `seed_names`/`id_to_name` are derived ABOVE the branch (lines ~81-84) and seeds always survive the filter, so they stay identical — do NOT move that derivation. Change:

```python
    if mode == "hybrid":
        passage_texts = (
            list(passages.retrieve(query, passage_k)) if passages is not None else []
        )
        return synthesize_hybrid(
            query, subgraph, passage_texts, llm, seed_names=seed_names
        )
```

to:

```python
    if mode == "hybrid":
        if _hybrid_filter_mode() == "path":
            from .subgraph_filter import filter_subgraph_to_paths

            subgraph = filter_subgraph_to_paths(subgraph, seeds)
        passage_texts = (
            list(passages.retrieve(query, passage_k)) if passages is not None else []
        )
        return synthesize_hybrid(
            query, subgraph, passage_texts, llm, seed_names=seed_names
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run:
```
POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8 python -m pytest tests/test_hybrid_synthesis.py -v
```
Expected: PASS (all existing hybrid tests + the 3 new ones).

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldengraph/goldengraph/answer.py packages/python/goldengraph/tests/test_hybrid_synthesis.py
git commit -m "feat(goldengraph): apply path-filter in ask() hybrid branch (env-gated, default off)"
```

---

## Task 3: Bench leg wiring

**Files:**
- Modify: `.github/workflows/bench-graphrag-qa.yml`

No bench-adapter (`engines/goldengraph.py`) change is needed: `ask()` reads
`GOLDENGRAPH_HYBRID_FILTER` from the environment in-process, so exporting the env
in the workflow is sufficient.

- [ ] **Step 1: Add the `workflow_dispatch` input**

In `.github/workflows/bench-graphrag-qa.yml`, after the `goldengraph_passage_k` input (the block ending `default: "10"` at ~line 74), add:

```yaml
      goldengraph_hybrid_filter:
        description: "goldengraph hybrid only: subgraph relevance filter -- none (full ball) | path (keep seeds + anchor-to-anchor paths + 1-hop halo)"
        default: "none"
```

- [ ] **Step 2: Export the env in the goldengraph job**

In the goldengraph job's `env:` block, after the `GOLDENGRAPH_QA_PASSAGE_K` line (~line 155), add:

```yaml
          GOLDENGRAPH_HYBRID_FILTER: ${{ inputs.goldengraph_hybrid_filter }}
```

- [ ] **Step 3: Validate the workflow YAML parses**

Run (from repo root):
```
python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/bench-graphrag-qa.yml')); print('ok')"
```
Expected: `ok`.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/bench-graphrag-qa.yml
git commit -m "ci(bench): goldengraph_hybrid_filter input for the path-prune A/B leg"
```

---

## Done criteria

- `test_subgraph_filter.py` (8) and `test_hybrid_synthesis.py` (existing + 3 new) pass with the polars guard.
- `GOLDENGRAPH_HYBRID_FILTER` unset → `ask` hybrid output is byte-identical to the current 0.420 control; `local`/`global` paths untouched.
- Workflow exposes `goldengraph_hybrid_filter` (default `none`) and exports `GOLDENGRAPH_HYBRID_FILTER`.
- The decision-rule leg (spec §Goal) can be dispatched: `engine=goldengraph qa_mode=hybrid goldengraph_hybrid_filter=path`, same seed (musique, gpt-4o-mini, N=50, amb=0.0) as the 0.420 control and the 0.520 `goldenmatch_rag` baseline.

## Not in scope (YAGNI)

ANN index, persisted embedding sidecar, `local`-mode filtering, multi-path keep, surfacing `halo` as a separate env. If the leg wins → productize passage persistence on the Rust node (separate spec).
