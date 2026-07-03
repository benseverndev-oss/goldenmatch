# Alias-injected STaRK ER-moat experiment — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Corrupt a real STaRK KB with alias duplicates that fragment each gold entity's text + edges, then show goldenmatch's resolver recovers retrieval quality that ad-hoc exact-match dedup loses — the ER moat vanilla STaRK cannot show.

**Architecture:** Reuse SP2/SP1 wholesale. A pure injector fragments gold entities into k variant-named alias nodes (split doc sentences + edges). A resolver clusters the aliases three ways (none / exact / goldenmatch). One Python clustering drives BOTH materializations: the `EntityIndex` (one merged-text embedding per cluster) and the store (a caller-side collapse to one node per cluster, edges remapped, then `bulk_load` the pre-merged graph — `bulk.py` unchanged). Scoring maps retrieved cluster ordinals → canonical originals (equivalence class) and reuses `stark_metrics`.

**Tech Stack:** Python 3.12, `goldengraph_native.PyStore` (real store, box-loadable), `goldengraph.entity_index.EntityIndex`, `goldenmatch.dedupe_df` + `polars` (the `er` resolver), numpy, Modal (run only).

**Spec:** `docs/superpowers/specs/2026-07-02-goldengraph-stark-alias-moat-design.md`

---

## Ground truth confirmed

- `goldengraph_native.PyStore` loads on the box (`append`/`as_of`/`history`/`snapshot`); `bulk_load` (`goldengraph/bulk.py`) is REUSED UNCHANGED — each collapsed cluster node passed as `(str(ordinal), name, typ)` gets `record_keys=[str(ordinal)]` (unique → passthrough, no store merge relied on).
- `goldenmatch.dedupe_df(pl.DataFrame(...))` → `result.clusters` is a dict `{cluster_id: {"members": [row_indices], ...}}` (pattern already in `ingest.py::_gm_cluster` lines 210-229). Model the `er` resolver on it.
- `as_of` mints view-local EntityIds in ascending StableId order, and StableIds are minted in **string-sorted record_key order**, so a store node's view-eid ≠ its ordinal. The ordinal rides through on `source_refs` (bulk_load sets `source_refs=[str(ordinal)]`); recover it exactly as SP2 recovered stark_id: `{int(e["source_refs"][0]): e["entity_id"] for e in slice.entities()}`.
- `EntityIndex.build` filters `literal:`-typed + empty-name rows — a cluster whose merged doc is empty just drops from the index (unretrievable anyway; fine).
- `stark_adapter.evaluate(index, slice, stark_to_eid, eid_to_stark, queries, embedder, *, arm, sample)` already maps view-eid↔the-id-on-source_refs. We add an `id_map` param so retrieved ids are canon-mapped before `metrics`.
- Clean reference (recall@20 dense 0.261 / graph 0.213) is CITED from PR #1402, not re-run (there ordinal = stark_id, identity map).

## File structure

- **Create** `packages/python/goldengraph/goldengraph/stark_inject.py` — the corruption: `inject_aliases`, `_variants`. Pure + seeded rng. No store, no goldenmatch.
- **Create** `packages/python/goldengraph/goldengraph/stark_moat.py` — the clustering→materialization: `build_clusters`, `collapse_for_index`, `collapse_for_store`. Pure except `collapse_for_store`'s consumer (bulk_load in the test). (No `canon_rank` — the single map+dedup helper is `_apply_id_map` in `stark_adapter`.)
- **Create** `packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/stark_resolve.py` — `resolve_aliases` (none/exact/er). Lazy goldenmatch import.
- **Modify** `packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/stark_adapter.py` — add `id_map=None` to `evaluate` + the `_apply_id_map` helper (canon-map retrieved ids before scoring).
- **Modify** `scripts/distill/modal_stark.py` — add `--inject` mode driver (integration).
- **Tests (5 files):** `goldengraph/tests/test_stark_inject.py`, `goldengraph/tests/test_build_clusters.py` (build_clusters, pure), `goldengraph/tests/test_alias_materialize.py` (real PyStore), `er-kg-bench/tests/test_stark_resolve.py`, `er-kg-bench/tests/test_alias_scoring.py` (`_apply_id_map` + metrics, pure). The two "scoring"-ish tests live in separate roots and test different functions — no clash.

## Box-safe test runners

goldengraph (real PyStore, worktree goldenmatch shadow):
```
cd packages/python/goldengraph
PYTHONPATH=/d/show_case/gg-local-llm/packages/python/goldenmatch POLARS_SKIP_CPU_CHECK=1 GOLDENMATCH_NATIVE=0 \
  /d/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest tests/<file> -q
```
erkgbench:
```
cd packages/python/goldenmatch/benchmarks/er-kg-bench
PYTHONPATH="$PWD:/d/show_case/gg-local-llm/packages/python/goldengraph" POLARS_SKIP_CPU_CHECK=1 GOLDENMATCH_NATIVE=0 \
  /d/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest tests/<file> -q
```
Reference skills: @superpowers:test-driven-development, @superpowers:subagent-driven-development. Auth: benzsevern (`unset GH_TOKEN` before push). Fixtures: surname-diverse per the synthetic-fixture guidance (avoid blocking hangs in dedupe).

---

## Task 1: `inject_aliases` + `_variants` — the corruption

**Files:**
- Create: `packages/python/goldengraph/goldengraph/stark_inject.py`
- Test: `packages/python/goldengraph/tests/test_stark_inject.py`

- [ ] **Step 1: Write the failing tests**

```python
"""SP-moat: alias injection fragments a gold entity into k variant-named nodes,
splitting its doc + edges. Pure + seeded; no store, no goldenmatch."""
from __future__ import annotations

from goldengraph.stark_inject import _variants, inject_aliases

# 3 nodes; N1 is the injection target. Docs are 3 sentences so k=3 splits cleanly.
_NODES = [("1", "Interleukin 6", "gene"), ("2", "aspirin", "drug"), ("3", "fever", "effect")]
_TEXTS = ["Interleukin 6 is a cytokine. It signals inflammation. It is a drug target.",
          "aspirin doc.", "fever doc."]
_EDGES = [("1", "associated_with", "3"), ("2", "treats", "3"), ("1", "targeted_by", "2")]


def test_variants_returns_k_distinct():
    vs = _variants("Interleukin 6", 3, seed=0)
    assert len(vs) == 3 and len(set(vs)) == 3          # distinct (anti-rig for exact dedup)


def test_target_fragmented_into_k_aliases_original_dropped():
    nodes2, texts2, edges2, canon = inject_aliases(_NODES, _TEXTS, _EDGES, {"1"}, k=3, seed=0)
    ids = {n[0] for n in nodes2}
    assert "1" not in ids                               # original dropped
    aliases = [i for i in ids if i.startswith("1#a")]
    assert len(aliases) == 3                            # k aliases
    assert {"2", "3"} <= ids                            # non-targets pass through


def test_canon_maps_aliases_to_original_identity_elsewhere():
    _, _, _, canon = inject_aliases(_NODES, _TEXTS, _EDGES, {"1"}, k=3, seed=0)
    assert canon["2"] == "2" and canon["3"] == "3"      # identity for non-targets
    assert all(canon[a] == "1" for a in canon if a.startswith("1#a"))


def test_doc_sentences_partitioned_no_alias_has_full():
    nodes2, texts2, _, _ = inject_aliases(_NODES, _TEXTS, _EDGES, {"1"}, k=3, seed=0)
    txt = dict(zip([n[0] for n in nodes2], texts2))
    alias_docs = [txt[i] for i in txt if i.startswith("1#a")]
    joined = " ".join(alias_docs)
    for sent in ["Interleukin 6 is a cytokine", "It signals inflammation", "It is a drug target"]:
        assert sent in joined                           # union preserves every sentence
    assert all(len(d) < len(_TEXTS[0]) for d in alias_docs)   # no alias has the full doc


def test_edges_distributed_across_aliases():
    _, _, edges2, _ = inject_aliases(_NODES, _TEXTS, _EDGES, {"1"}, k=3, seed=0)
    # N1 had 2 incident edges (->3, N1 targeted_by 2); they land on DIFFERENT aliases
    n1_edge_sources = {s for (s, _p, _o) in edges2 if s.startswith("1#a")}
    n1_edge_objs = {o for (_s, _p, o) in edges2 if o.startswith("1#a")}
    assert len(n1_edge_sources | n1_edge_objs) >= 2     # spread across >=2 aliases


def test_both_target_edge_remaps_both_ends():
    # inject BOTH endpoints of edge (1 -targeted_by-> 2): both must become aliases
    _, _, edges2, _ = inject_aliases(_NODES, _TEXTS, _EDGES, {"1", "2"}, k=2, seed=0)
    e = [(s, p, o) for (s, p, o) in edges2 if p == "targeted_by"][0]
    assert e[0].startswith("1#a") and e[2].startswith("2#a")


def test_determinism_same_seed():
    a = inject_aliases(_NODES, _TEXTS, _EDGES, {"1"}, k=3, seed=7)
    b = inject_aliases(_NODES, _TEXTS, _EDGES, {"1"}, k=3, seed=7)
    assert a == b
```

- [ ] **Step 2: Run to verify failure** (box runner) → `ModuleNotFoundError: goldengraph.stark_inject`.

- [ ] **Step 3: Implement**

```python
"""SP-moat: fragment gold entities into k alias nodes (variant names, split doc +
edges) so dense retrieval degrades and only a real resolver recovers it. Pure +
seeded (reproducible); no store, no goldenmatch here.
See docs/superpowers/specs/2026-07-02-goldengraph-stark-alias-moat-design.md.
"""
from __future__ import annotations

import random


def _variants(name: str, k: int, *, seed: int) -> list[str]:
    """k DISTINCT surface forms of `name` (abbreviation / word-order / truncation /
    punctuation-drop). Distinctness is load-bearing: if two variants collide, the
    ad-hoc exact-match baseline would merge them for free and rig the comparison.
    Falls back to a numbered suffix to guarantee k distinct forms."""
    rng = random.Random(f"{seed}:{name}")
    toks = name.split()
    cands: list[str] = []

    def _add(v: str) -> None:
        v = v.strip()
        if v and v not in cands:
            cands.append(v)

    _add(name)
    if len(toks) > 1:
        _add(" ".join(reversed(toks)))                       # word-order
        _add("".join(t[0].upper() for t in toks))            # abbreviation/initials
        _add(" ".join(toks[:-1]))                            # truncation
    _add(name.replace("-", " ").replace(",", ""))            # punctuation-drop
    _add(name.lower())
    rng.shuffle(cands)
    out = cands[:k]
    j = 0
    while len(out) < k:                                       # guarantee k distinct
        cand = f"{name} ({j})"
        if cand not in out:
            out.append(cand)
        j += 1
    return out


def _sentences(doc: str) -> list[str]:
    return [s.strip() for s in doc.split(". ") if s.strip()]


def inject_aliases(nodes, node_texts, edges, target_ids, *, k: int = 3, seed: int = 0):
    """Fragment each entity in `target_ids` into k alias nodes. Returns
    (nodes2, node_texts2, edges2, canon). See the spec / module docstring."""
    target_ids = set(target_ids)
    name_of = {n[0]: n[1] for n in nodes}
    typ_of = {n[0]: n[2] for n in nodes}
    text_of = dict(zip([n[0] for n in nodes], node_texts))

    # alias ids + variant names per target; canon maps every id -> original
    alias_ids: dict[str, list[str]] = {}
    canon: dict[str, str] = {}
    nodes2: list[tuple] = []
    texts2: list[str] = []
    for nid, name, typ in nodes:
        if nid not in target_ids:
            nodes2.append((nid, name, typ))
            texts2.append(text_of[nid])
            canon[nid] = nid
            continue
        vs = _variants(name, k, seed=seed)
        ids = [f"{nid}#a{j}" for j in range(k)]
        alias_ids[nid] = ids
        sents = _sentences(text_of.get(nid, "")) or [name]
        buckets: list[list[str]] = [[] for _ in range(k)]
        for i, s in enumerate(sents):
            buckets[i % k].append(s)                          # round-robin sentences
        for j, aid in enumerate(ids):
            nodes2.append((aid, vs[j], typ))
            texts2.append(". ".join(buckets[j]))
            canon[aid] = nid

    # remap edges: an endpoint that is a target -> one of its aliases (round-robin by
    # a per-target counter so its incident edges spread across aliases)
    ctr: dict[str, int] = {}

    def _remap(endpoint: str) -> str:
        if endpoint not in target_ids:
            return endpoint
        ids = alias_ids[endpoint]
        c = ctr.get(endpoint, 0)
        ctr[endpoint] = c + 1
        return ids[c % len(ids)]

    edges2 = [(_remap(s), p, _remap(o)) for (s, p, o) in edges]
    return nodes2, texts2, edges2, canon
```

- [ ] **Step 4: Run to verify pass** (box runner). Expected: 7 pass.

- [ ] **Step 5: Commit**

```bash
cd /d/show_case/gg-local-llm && unset GH_TOKEN
git add packages/python/goldengraph/goldengraph/stark_inject.py packages/python/goldengraph/tests/test_stark_inject.py
git commit -m "feat(goldengraph): alias injection for STaRK ER-moat (fragment gold entity text+edges)"
```

---

## Task 2: `resolve_aliases` — none / exact / goldenmatch

**Files:**
- Create: `packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/stark_resolve.py`
- Test: `packages/python/goldenmatch/benchmarks/er-kg-bench/tests/test_stark_resolve.py`

- [ ] **Step 1: Write the failing tests**

```python
"""SP-moat resolver: cluster injected alias nodes 3 ways. `er` (goldenmatch) must
merge variant surface forms of ONE entity that `exact` leaves split -- the moat in
miniature. Surname-diverse fixture to avoid dedupe blocking hangs."""
from __future__ import annotations

import sys
from pathlib import Path

_BENCH_ROOT = Path(__file__).resolve().parent.parent
if str(_BENCH_ROOT) not in sys.path:
    sys.path.insert(0, str(_BENCH_ROOT))

from erkgbench.stark_resolve import resolve_aliases  # noqa: E402


def _clusters_as_sets(clusters):
    return {frozenset(c) for c in clusters}


# two entities: A has 3 variant surface forms, B has 1. Distinct real surnames.
_ALIAS_NODES = [
    ("A#a0", "Interleukin 6"), ("A#a1", "IL 6"), ("A#a2", "Interleukin-6"),
    ("B#a0", "Metformin"),
]


def test_none_all_singletons():
    cl = resolve_aliases(_ALIAS_NODES, "none")
    assert _clusters_as_sets(cl) == {frozenset([i]) for i, _ in _ALIAS_NODES}


def test_exact_merges_only_identical_names():
    dup = _ALIAS_NODES + [("C#a0", "Metformin")]     # exact dup of B's name
    cl = resolve_aliases(dup, "exact")
    sets = _clusters_as_sets(cl)
    assert frozenset(["B#a0", "C#a0"]) in sets        # identical names merge
    assert frozenset(["A#a0"]) in sets                # variant forms stay split
    assert frozenset(["A#a1"]) in sets


def test_er_merges_variant_surface_forms():
    cl = resolve_aliases(_ALIAS_NODES, "er")
    sets = _clusters_as_sets(cl)
    # the 3 IL-6 variants land in ONE cluster; Metformin stays separate
    il6 = next(s for s in sets if "A#a0" in s)
    assert {"A#a0", "A#a1", "A#a2"} <= set(il6)        # moat: er merges what exact can't
    assert frozenset(["B#a0"]) in sets
```

- [ ] **Step 2: Run to verify failure** (erkgbench box runner) → module missing. (If `er` blocks/hangs, the fixture is too homogeneous — keep it tiny + surname-diverse.)

- [ ] **Step 3: Implement**

```python
"""SP-moat resolver: cluster injected alias nodes. `none`/`exact` are pure; `er`
routes through goldenmatch's zero-config dedupe (the suite's real ER engine) over
the alias names -- the same call `ingest.py::_gm_cluster` uses. goldenmatch + polars
import lazily so the pure paths need neither."""
from __future__ import annotations

from collections import defaultdict


def resolve_aliases(alias_nodes, method: str):
    """`alias_nodes`: [(alias_id, name)] (injected set only). Returns clusters:
    list[list[alias_id]]. method in {none, exact, er}."""
    ids = [a for a, _ in alias_nodes]
    if method == "none":
        return [[i] for i in ids]
    if method == "exact":
        groups: dict[str, list[str]] = defaultdict(list)
        for aid, name in alias_nodes:
            groups[name.lower().strip()].append(aid)
        return list(groups.values())
    if method == "er":
        import goldenmatch as gm
        import polars as pl

        if not alias_nodes:
            return []
        df = pl.DataFrame({"name": [name for _, name in alias_nodes]})
        result = gm.dedupe_df(df)
        seen: set[int] = set()
        clusters: list[list[str]] = []
        for info in result.clusters.values():
            members = [int(x) for x in info["members"]]
            seen.update(members)
            clusters.append([ids[m] for m in members])
        # dedupe_df only returns multi-member clusters; singletons are the rest
        for i, aid in enumerate(ids):
            if i not in seen:
                clusters.append([aid])
        return clusters
    raise ValueError(f"unknown method {method!r} (none|exact|er)")
```

- [ ] **Step 4: Run to verify pass** (erkgbench box runner). If `er` returns singletons for all (dedupe found nothing), the fixture names may be too dissimilar for the zero-config threshold — nudge the variants closer (`IL-6` / `IL 6` / `Interleukin 6`) so the real engine merges them; that IS the behavior under test. Expected: 3 pass.

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/stark_resolve.py packages/python/goldenmatch/benchmarks/er-kg-bench/tests/test_stark_resolve.py
git commit -m "feat(erkgbench): STaRK alias resolver (none/exact/goldenmatch dedupe_df)"
```

---

## Task 3: `build_clusters` — cluster ordinals + int canon map (pure)

**Files:**
- Create: `packages/python/goldengraph/goldengraph/stark_moat.py`
- Test: `packages/python/goldengraph/tests/test_build_clusters.py` (pure)

- [ ] **Step 1: Write the failing tests**

```python
"""SP-moat cluster assembly + INT canon scoring map (pure). ord2canon values are
INTS -- the original STaRK node ids -- so they match the int `gold` sets in
stark_metrics. (A str->int mismatch here would make every method score ~0.)"""
from __future__ import annotations

from goldengraph.stark_moat import build_clusters


def test_build_clusters_targets_grouped_nontargets_singleton():
    canon = {"1#a0": "1", "1#a1": "1", "2": "2"}
    method_clusters = [["1#a0", "1#a1"]]               # resolver merged the two aliases
    all_ids = ["1#a0", "1#a1", "2"]
    ordinal_of, ord2canon = build_clusters(canon, method_clusters, all_ids)
    assert ordinal_of["1#a0"] == ordinal_of["1#a1"]    # aliases share an ordinal
    assert ordinal_of["2"] != ordinal_of["1#a0"]        # non-target its own ordinal
    assert ord2canon[ordinal_of["1#a0"]] == 1           # INT canonical original (matches int gold)
    assert ord2canon[ordinal_of["2"]] == 2
    assert all(isinstance(v, int) for v in ord2canon.values())


def test_build_clusters_fragmented_all_singletons():
    canon = {"1#a0": "1", "1#a1": "1"}
    ordinal_of, ord2canon = build_clusters(canon, [["1#a0"], ["1#a1"]], ["1#a0", "1#a1"])
    assert ordinal_of["1#a0"] != ordinal_of["1#a1"]     # fragmented -> distinct ordinals
    assert ord2canon[ordinal_of["1#a0"]] == 1           # both still map to original 1 (int)
```

- [ ] **Step 2: Run to verify failure** (goldengraph box runner).

- [ ] **Step 3: Implement**

```python
"""SP-moat: turn a resolver's clusters into cluster-ordinal ids that drive BOTH
materializations (index + store), plus the canon scoring map. The clustering is the
single ER lever; the store's overlap-merge is NOT used (a batch reconciles only
against already-stored entities, so same-batch aliases would never merge -- we
collapse in Python instead). See the spec.
"""
from __future__ import annotations


def build_clusters(canon, method_clusters, all_ids):
    """`canon`: alias_id -> original id (STRING stark ids). `method_clusters`: resolver
    output over the INJECTED aliases (list[list[alias_id]]). `all_ids`: every node id in
    the injected graph (aliases + passthrough non-targets). Returns (ordinal_of, ord2canon):
      ordinal_of: id -> cluster_ordinal (int, deterministic)
      ord2canon:  cluster_ordinal -> canonical original id, **as INT** (STaRK node ids
                  are integers; the int match is what makes scoring against the int
                  `gold` sets work -- a str value here scores ~0 for every method).
    Injected aliases group by `method_clusters`; every other id is its own singleton
    cluster. Ordinals assigned in sorted order for determinism."""
    ordinal_of: dict[str, int] = {}
    ord2canon: dict[int, int] = {}
    clustered: set[str] = set()
    # deterministic order: sort clusters by their lexicographically smallest member
    ordered = sorted((sorted(c) for c in method_clusters), key=lambda c: c[0])
    ordinal = 0
    for members in ordered:
        for m in members:
            ordinal_of[m] = ordinal
            clustered.add(m)
        # canonical = the original the cluster's members map to (first by sort); a
        # resolver error mixing two originals deterministically picks the smallest.
        ord2canon[ordinal] = int(canon.get(members[0], members[0]))
        ordinal += 1
    for nid in sorted(set(all_ids) - clustered):        # singletons (non-targets etc.)
        ordinal_of[nid] = ordinal
        ord2canon[ordinal] = int(canon.get(nid, nid))
        ordinal += 1
    return ordinal_of, ord2canon
```

- [ ] **Step 4: Run to verify pass** (goldengraph box runner). Expected: 2 pass.

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldengraph/goldengraph/stark_moat.py packages/python/goldengraph/tests/test_build_clusters.py
git commit -m "feat(goldengraph): stark_moat cluster ordinals + int canon scoring map"
```

---

## Task 4: `collapse_for_index` + `collapse_for_store` (+ real-PyStore materialize)

**Files:**
- Modify: `packages/python/goldengraph/goldengraph/stark_moat.py`
- Test: `packages/python/goldengraph/tests/test_alias_materialize.py` (real PyStore)

- [ ] **Step 1: Write the failing tests**

```python
"""SP-moat materialization: one index entry + one store node per cluster; the store
holds the PRE-MERGED graph (edges unioned by Python collapse, not store merge)."""
from __future__ import annotations

import json

import pytest

ggn = pytest.importorskip("goldengraph_native")
from goldengraph.bulk import bulk_load                       # noqa: E402
from goldengraph.stark_moat import collapse_for_index, collapse_for_store  # noqa: E402

_BIG = 1 << 62


def _store():
    from goldengraph_native import _native as gg
    return gg.PyStore()


def test_collapse_for_index_merges_docs_per_cluster():
    nodes2 = [("1#a0", "IL6", "gene"), ("1#a1", "IL 6", "gene"), ("2", "aspirin", "drug")]
    texts2 = ["sentence one", "sentence two", "aspirin doc"]
    ordinal_of = {"1#a0": 0, "1#a1": 0, "2": 1}              # aliases merged into ord 0
    ents = collapse_for_index(nodes2, texts2, ordinal_of)
    by_ord = {e["entity_id"]: e for e in ents}
    assert set(by_ord) == {0, 1}                             # one entry per cluster
    assert "sentence one" in by_ord[0]["canonical_name"] and "sentence two" in by_ord[0]["canonical_name"]


def test_collapse_for_store_unions_neighborhood():
    # aliases 1#a0,1#a1 (cluster 0) each hold one edge to distinct neighbors 2,3
    nodes2 = [("1#a0", "IL6", "gene"), ("1#a1", "IL 6", "gene"),
              ("2", "aspirin", "drug"), ("3", "fever", "effect")]
    edges2 = [("1#a0", "targets", "2"), ("1#a1", "assoc", "3")]
    ordinal_of = {"1#a0": 0, "1#a1": 0, "2": 1, "3": 2}
    coll_nodes, coll_edges = collapse_for_store(nodes2, edges2, ordinal_of)
    assert len(coll_nodes) == 3                              # 3 clusters -> 3 nodes
    store = _store()
    bulk_load(store, coll_nodes, coll_edges)
    g = store.as_of(_BIG, _BIG)
    ord_to_eid = {int(e["source_refs"][0]): e["entity_id"] for e in g.entities()}
    eid0 = ord_to_eid[0]                                     # view-eid != ordinal; map via source_refs
    neighbors = {e["obj"] for e in g.query([eid0], 1)["edges"] if e["subj"] == eid0}
    assert neighbors == {ord_to_eid[1], ord_to_eid[2]}      # UNIONED: both aliases' edges on cluster 0


def test_collapse_drops_intra_cluster_self_loops():
    nodes2 = [("1#a0", "IL6", "gene"), ("1#a1", "IL 6", "gene")]
    edges2 = [("1#a0", "same", "1#a1")]                      # both ends in cluster 0
    ordinal_of = {"1#a0": 0, "1#a1": 0}
    _, coll_edges = collapse_for_store(nodes2, edges2, ordinal_of)
    assert coll_edges == []                                  # self-loop dropped
```

- [ ] **Step 2: Run to verify failure** (goldengraph box runner) → functions missing.

- [ ] **Step 3: Implement** (append to `stark_moat.py`)

```python
def collapse_for_index(nodes2, node_texts2, ordinal_of):
    """One index entry per cluster: entity_id = ordinal, canonical_name = the joined
    member docs (the MERGED text -- where dense-ER recovery happens), typ from the
    first member. Docs joined in stable (ordinal, id) order."""
    text_of = dict(zip([n[0] for n in nodes2], node_texts2))
    typ_of = {n[0]: n[2] for n in nodes2}
    members: dict[int, list[str]] = {}
    for nid, _name, _typ in nodes2:
        members.setdefault(ordinal_of[nid], []).append(nid)
    out = []
    for ordv, ids in sorted(members.items()):
        ids_sorted = sorted(ids)
        doc = " ".join(text_of.get(i, "") for i in ids_sorted).strip()
        out.append({"entity_id": ordv, "canonical_name": doc, "typ": typ_of[ids_sorted[0]]})
    return out


def collapse_for_store(nodes2, edges2, ordinal_of):
    """One store node per cluster (id = str(ordinal)) + edges remapped endpoint ->
    its cluster ordinal, dropping intra-cluster self-loops. Feed to bulk_load
    UNCHANGED -- each node's unique key = str(ordinal) => passthrough, so the store
    holds exactly this pre-merged graph (no store-side merge)."""
    name_of = {n[0]: n[1] for n in nodes2}
    typ_of = {n[0]: n[2] for n in nodes2}
    seen: dict[int, tuple] = {}
    for nid, name, typ in nodes2:
        ordv = ordinal_of[nid]
        seen.setdefault(ordv, (str(ordv), name, typ))       # first member names the node
    coll_nodes = [seen[o] for o in sorted(seen)]
    coll_edges = []
    for s, p, o in edges2:
        so, oo = ordinal_of[s], ordinal_of[o]
        if so == oo:
            continue                                        # intra-cluster self-loop
        coll_edges.append((str(so), p, str(oo)))
    return coll_nodes, coll_edges
```

- [ ] **Step 4: Run to verify pass** (goldengraph box runner). Expected: 3 pass. The union test is the load-bearing graph-arm check.

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldengraph/goldengraph/stark_moat.py packages/python/goldengraph/tests/test_alias_materialize.py
git commit -m "feat(goldengraph): stark_moat collapse-for-index/store (Python edge-union, bulk_load unchanged)"
```

---

## Task 5: `evaluate(id_map=...)` — canon-mapped scoring + pure scoring test

**Files:**
- Modify: `packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/stark_adapter.py`
- Test: `packages/python/goldenmatch/benchmarks/er-kg-bench/tests/test_alias_scoring.py`

- [ ] **Step 1: Write the failing test** (pure — `_apply_id_map` semantics against `metrics`)

```python
"""SP-moat scoring: canon-mapped ranked list -> equivalence-class hit."""
from __future__ import annotations

import sys
from pathlib import Path

_BENCH_ROOT = Path(__file__).resolve().parent.parent
if str(_BENCH_ROOT) not in sys.path:
    sys.path.insert(0, str(_BENCH_ROOT))

from erkgbench.stark_metrics import metrics                 # noqa: E402
from erkgbench.stark_adapter import _apply_id_map           # noqa: E402


def test_id_map_canon_then_hit_on_any_alias():
    # retrieved cluster ordinals; ord2canon maps ords 10,11 -> gold entity 1
    ranked = _apply_id_map([12, 10, 11], {10: 1, 11: 1, 12: 2})
    assert ranked == [2, 1]                                  # dedup first-seen
    m = metrics(ranked, {1})                                 # gold entity 1 retrieved via an alias
    assert m["hit@5"] == 1.0 and m["recall@20"] == 1.0
```

- [ ] **Step 2: Run to verify failure** (erkgbench box runner) → `_apply_id_map` missing.

- [ ] **Step 3: Implement** — add `_apply_id_map` + thread `id_map` through `evaluate`

In `stark_adapter.py`, add:
```python
def _apply_id_map(retrieved, id_map):
    """Map retrieved ids through `id_map` (cluster_ordinal -> canonical original),
    dedup first-seen. `id_map=None` -> passthrough (dedup only)."""
    seen, out = set(), []
    for r in retrieved:
        v = r if id_map is None else id_map.get(r)
        if v is not None and v not in seen:
            seen.add(v)
            out.append(v)
    return out
```
Then in `evaluate(...)` add an `id_map=None` kwarg and replace each arm's
`ranked = [...]` scoring input so the retrieved ids are passed through
`_apply_id_map(ranked, id_map)` before `metrics(ranked, gold)`. The dense arm's
`index.query` already returns ordinals; the graph arm's neighbor mapping already
returns the-id-on-source_refs (the ordinal). So `id_map = ord2canon` for the moat,
`id_map = None` for the clean/plain STaRK path (identity — unchanged behavior).

- [ ] **Step 4: Run to verify pass** (erkgbench box runner) + re-run the existing `test_stark_metrics.py` to confirm no regression. Expected: green.

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/stark_adapter.py packages/python/goldenmatch/benchmarks/er-kg-bench/tests/test_alias_scoring.py
git commit -m "feat(erkgbench): evaluate(id_map=) for canon-equivalence moat scoring"
```

---

## Task 6: Modal `--inject` driver (integration, run-only)

**Files:**
- Modify: `scripts/distill/modal_stark.py`

Not box-TDD'd (needs HF + embed + Modal). Add an `inject: bool = False`, `k: int = 3`, `seed: int = 0` path to `_stark_impl` / `run_stark` / `main`.

- [ ] **Step 1: Implement the `--inject` branch**

Inside `_stark_impl`, when `inject`:
1. `nodes, edges, queries, node_texts = load_stark_kb(kb, split="test", limit_queries=sample, with_text=True)`
2. `target_ids = {str(g) for _q, gold in queries for g in gold}`  # sampled queries' gold entities
3. `nodes2, texts2, edges2, canon = inject_aliases(nodes, node_texts, edges, target_ids, k=k, seed=seed)`
4. `alias_nodes = [(nid, name) for nid, name, _typ in nodes2 if nid in canon and canon[nid] != nid]`  # injected aliases only
5. For `method in ("none", "exact", "er")`:
   - `clusters = resolve_aliases(alias_nodes, method)`
   - `ordinal_of, ord2canon = build_clusters(canon, clusters, [n[0] for n in nodes2])`
   - `index = EntityIndex.build(collapse_for_index(nodes2, texts2, ordinal_of), embedder, top_k=50)`
   - `coll_nodes, coll_edges = collapse_for_store(nodes2, edges2, ordinal_of)`; `store = PyStore(); bulk_load(store, coll_nodes, coll_edges)`
   - `slice = store.as_of(_BIG, _BIG)`; build `stark_to_eid`/`eid_to_stark` over `source_refs` (these are ordinals now)
   - `gold_int = queries` unchanged (original stark ids as ints)
   - for `arm in ("dense", "graph")`: `evaluate(index, slice, stark_to_eid, eid_to_stark, queries, embedder, arm=arm, id_map=ord2canon)`
   - collect `row(method, arm)`
6. Print a table: per (method × arm) recall@20/hit@1/hit@5/mrr, plus the **ER−adhoc** and **ER−fragmented** margins on dense recall@20. Cite the clean row from PR #1402 (dense 0.261 / graph 0.213) in the header — not re-run.
7. Persist to `/cache/results/stark_{kb}_inject.md`.

Imports inside the function (after the sys.path shim): `from goldengraph.stark_inject import inject_aliases`, `from goldengraph.stark_moat import build_clusters, collapse_for_index, collapse_for_store`, `from erkgbench.stark_resolve import resolve_aliases`.

`main`/`run_stark` gain `--inject`, `--k`, `--seed`; result filename `stark_{kb}_inject.md`; SPAWNED line updated.

- [ ] **Step 2: Run PRIME inject on Modal** (foreground through build to SPAWNED, then poll — bg pollers get killed):
```
export PYTHONIOENCODING=utf-8 PYTHONUTF8=1
P=a99885f0-c5af-4ae1-9dc8-255cc60aa129
export MODAL_TOKEN_ID=$(infisical.cmd secrets get MODAL_TOKEN_ID --projectId $P --env dev --plain)
export MODAL_TOKEN_SECRET=$(infisical.cmd secrets get MODAL_TOKEN_SECRET --projectId $P --env dev --plain)
modal run --detach scripts/distill/modal_stark.py --kb prime --sample 200 --inject --k 3 --spawn
```
No new pip deps → image cached → fast spawn. Then poll the volume for `stark_prime_inject.md` + crash-detect the app.

- [ ] **Step 3: Verdict report** `docs/superpowers/reports/2026-07-03-stark-alias-moat.md`: the method×arm table, the ER−adhoc margin, and the honest call — **moat CONFIRMED** (ER-dense recovers recall@20 toward clean 0.261 while adhoc stays depressed) or **REFUTED/weak** (ER≈adhoc, or fragmented barely dropped from clean → injection too weak, note the knob to change). Either is publishable.

- [ ] **Step 4: Commit** the driver + report.

---

## Wrap-up

- [ ] `#1402` is MERGED, so rebase this branch onto main: `git fetch origin main && git rebase --onto origin/main <fulltext-branch-tip> feat/goldengraph-stark-alias-moat` (or plain `git rebase origin/main` if no overlap remains). Verify `git diff --stat origin/main HEAD` shows only the moat files.
- [ ] Push (benzsevern), open PR, arm `gh pr merge --auto --squash`, STOP (no CI poll).
- [ ] Update memory `project_stark_retrieval_scale.md`: the moat verdict (confirmed/refuted + the numbers), and what it means for the program.
- [ ] Doc-surface sweep only if a new PUBLIC API surfaced (these are bench-internal modules — likely none).

## Notes / risks

- **The union test (Task 4) is load-bearing** — it proves the graph arm gets the merged neighborhood via the Python collapse (the store cannot merge same-batch aliases). Do not weaken it.
- **`er` resolver on the real PRIME alias set** (a few thousand nodes) may be slow or over/under-merge at goldenmatch's zero-config threshold. That is itself a finding — record the cluster count vs the true target count. Keep the box fixture tiny + surname-diverse to avoid dedupe blocking hangs.
- **If fragmented-dense barely drops from clean 0.261**, the injection is too weak (docs didn't fragment enough, or gold entities have short docs) — the experiment is inconclusive, not a moat refutation. The report must check the fragmented-vs-clean drop FIRST before interpreting ER−adhoc.
- **`evaluate` id_map default None** keeps the plain STaRK path (Tasks from PR #1399/#1402) byte-identical — re-run `test_stark_metrics.py` to confirm no regression.
