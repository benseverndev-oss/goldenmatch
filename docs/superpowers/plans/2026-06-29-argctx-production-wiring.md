# Argument-Context Resolution — Production Wiring (Stage 1) — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the validated argument-context resolver into the LIVE goldengraph discovery pipeline as a `GOLDENGRAPH_DISCOVER_RESOLVE=argctx` backend (clustering the 7B's extracted predicates by the surface entity pairs they connect), plus a co-occurrence corpus mode so the live pipeline has the signal — then prove it via the argctx-vs-default delta on the same corpus.

**Architecture:** `_cluster_predicates_argctx(by_phrase)` reuses the experiment's proven pair-set Jaccard logic over the surface pairs `discover_schema` already collects, wired as a new value of the existing `GOLDENGRAPH_DISCOVER_RESOLVE` switch. `engineered.py` gains a gated co-occurrence mode that renders each edge with multiple phrasings; the multi-hop questions stay byte-identical via base-doc-keeps-base-id + a side-rng for the extra docs. Canonicalizer and ingest control flow are untouched.

**Tech Stack:** Python 3.12, pytest. `schema_discovery.py` already imports `_norm` and has the `GOLDENGRAPH_DISCOVER_RESOLVE` dispatch + the `by_phrase` map. `engineered.py` has `_REL_PHRASINGS`, `_render_relation`, `_render_mention`, `_edge_doc_id`.

**Conventions (verified):**
- goldengraph tests: from `packages/python/goldengraph`,
  `PYTHONPATH=. GOLDENGRAPH_NATIVE=0 POLARS_SKIP_CPU_CHECK=1 D:/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest tests/test_schema_discovery.py -q`
- bench tests: from `packages/python/goldenmatch/benchmarks/er-kg-bench`,
  `POLARS_SKIP_CPU_CHECK=1 GOLDENMATCH_NATIVE=0 D:/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest tests/test_qa_engineered_cooccur.py -q`
- `schema_discovery._norm(s)` lowercases + maps `_`↔space + collapses whitespace (reuse it; do not re-derive).

**Spec:** `docs/superpowers/specs/2026-06-29-argctx-production-wiring-design.md`

---

## File Structure

- **Modify** `packages/python/goldengraph/goldengraph/schema_discovery.py` — add `_cluster_predicates_argctx` + the `argctx` branch in `discover_schema`'s `GOLDENGRAPH_DISCOVER_RESOLVE` dispatch.
- **Modify** `packages/python/goldengraph/tests/test_schema_discovery.py` — argctx unit tests.
- **Modify** `packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/qa_e2e/engineered.py` — gated co-occurrence rendering.
- **Create** `packages/python/goldenmatch/benchmarks/er-kg-bench/tests/test_qa_engineered_cooccur.py` — corpus invariant tests.

---

## Task 1: `_cluster_predicates_argctx` + dispatch

**Files:** Modify `schema_discovery.py`; Modify `tests/test_schema_discovery.py`

- [ ] **Step 1: Write the failing tests** (append to `test_schema_discovery.py`)

```python
def test_argctx_clusters_by_shared_pairs():
    from goldengraph.schema_discovery import _cluster_predicates_argctx

    # two phrasings of the same relation connect the SAME pairs -> merge; a distinct-pair predicate
    # stays apart; a predicate sharing no pairs stays a singleton.
    by_phrase = {
        "works at": [("Jo", "works at", "Acme", "s"), ("Mae", "works at", "Globex", "s")],
        "is on staff at": [("Jo", "is on staff at", "Acme", "s"), ("Mae", "is on staff at", "Globex", "s")],
        "located in": [("Acme", "located in", "Reno", "s")],
        "spurious rel": [("X", "spurious rel", "Y", "s")],
    }
    clusters = _cluster_predicates_argctx(by_phrase)
    cmap = {p: i for i, c in enumerate(clusters) for p in c}
    assert cmap["works at"] == cmap["is on staff at"]      # shared pairs -> merged
    assert cmap["works at"] != cmap["located in"]          # disjoint pairs -> apart
    assert len([c for c in clusters if "spurious rel" in c][0]) == 1  # no shared pairs -> singleton


def test_argctx_normalizes_surfaces():
    from goldengraph.schema_discovery import _cluster_predicates_argctx

    # case/space differences in surfaces must not split the pair
    by_phrase = {
        "works at": [("Jo", "works at", "Acme", "s")],
        "employed at": [("jo", "employed at", "ACME", "s")],
    }
    clusters = _cluster_predicates_argctx(by_phrase)
    assert len(clusters) == 1  # same pair after _norm -> merged
```

- [ ] **Step 2: Run to verify fail** (`_cluster_predicates_argctx` undefined).

- [ ] **Step 3: Implement** (add near the other `_cluster_predicates*` in `schema_discovery.py`)

```python
def _cluster_predicates_argctx(by_phrase, jaccard_threshold: float | None = None):
    """Argument-context relation resolution (the live backend): cluster predicates by the SURFACE
    entity pairs they connect. Synonyms on a co-occurrence corpus connect the same (subj,obj) pairs ->
    high Jaccard -> merge; distinct relations connect disjoint pairs -> apart; a predicate sharing no
    pairs stays a singleton. The proven (gold-experiment) distributional method, over live-extracted
    surface pairs (`_norm`'d). Env-tunable `GOLDENGRAPH_ARGCTX_JACCARD` (default 0.3)."""
    if jaccard_threshold is None:
        jaccard_threshold = float(os.environ.get("GOLDENGRAPH_ARGCTX_JACCARD", "0.3"))
    preds = sorted(by_phrase)
    pair_set = {
        p: {(_norm(s), _norm(o)) for (s, _pred, o, _src) in by_phrase[p]} for p in preds
    }
    parent = {p: p for p in preds}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, a in enumerate(preds):
        for b in preds[i + 1:]:
            pa, pb = pair_set[a], pair_set[b]
            union = pa | pb
            if union and len(pa & pb) / len(union) >= jaccard_threshold:
                parent[find(a)] = find(b)
    groups: dict[str, list[str]] = {}
    for p in preds:
        groups.setdefault(find(p), []).append(p)
    return [sorted(g) for g in groups.values()]
```

> `os` and `_norm` are already imported in `schema_discovery.py`. `defaultdict` is NOT imported — the
> snippet above deliberately uses a plain dict + `setdefault` (matching the existing `_cluster_predicates`
> style) so no new import is needed.

- [ ] **Step 4: Run the two tests to verify pass.**

- [ ] **Step 5: Wire the dispatch.** In `discover_schema`, find the backend dispatch:

```python
    resolve = os.environ.get("GOLDENGRAPH_DISCOVER_RESOLVE", "").strip().lower()
    if resolve == "gm":
        clusters = _cluster_predicates_gm(list(by_phrase))
    else:
        clusters = _cluster_predicates(list(by_phrase), embedder)
```

Add an `argctx` branch BEFORE the `else`:

```python
    elif resolve == "argctx":
        clusters = _cluster_predicates_argctx(by_phrase)
```

(Order: `gm`, `argctx`, then `else`. The `llm_map` post-step below the dispatch is unaffected — argctx
produces clusters like any other backend.)

- [ ] **Step 6: Add a fail-soft test + dispatch test**

```python
def test_discover_schema_argctx_backend(monkeypatch):
    from goldengraph.schema import canonicalize_extraction
    from goldengraph.schema_discovery import discover_schema

    monkeypatch.setenv("GOLDENGRAPH_DISCOVER_RESOLVE", "argctx")
    # two synonyms co-occurring on the same pair; sources arg unused by argctx (uses surfaces)
    exts = [_ext(["Jo", "Acme"], [(0, "works at", 1)]),
            _ext(["Jo", "Acme"], [(0, "is on staff at", 1)])]
    sch = discover_schema(exts, ["Jo works at Acme.", "Jo is on staff at Acme."], _StubEmbedder())
    # both phrasings map to ONE canonical relation
    m1 = sch.match("works at"); m2 = sch.match("is on staff at")
    assert m1 is not None and m2 is not None and m1[0] == m2[0]
```

> `RelationSchema.match(predicate)` returns `(canonical_relation, flip) | None` (see `schema.py`), so
> `m[0]` is the canonical relation label — the assertion checks both phrasings map to the same relation.

- [ ] **Step 7: Run the full discovery suite, then commit.**

```bash
git add packages/python/goldengraph/goldengraph/schema_discovery.py packages/python/goldengraph/tests/test_schema_discovery.py
git commit -m "feat(goldengraph): argctx discovery backend -- cluster predicates by surface entity pairs"
```

---

## Task 2: Co-occurrence corpus rendering

**Files:** Modify `engineered.py`; Create `tests/test_qa_engineered_cooccur.py`

The renderer must (a) add extra docs (one per phrasing) so synonyms co-occur on each pair, (b) keep the
BASE doc-id so question gold-support resolves, and (c) leave the MAIN `rng` consumption identical so the
later question sampling is byte-identical. (c) is achieved with a per-edge SIDE rng for the extra docs.

- [ ] **Step 1: Write the failing tests** (`tests/test_qa_engineered_cooccur.py`)

```python
import os
from erkgbench.qa_e2e.engineered import generate_engineered


def _gen(**env):
    old = {k: os.environ.get(k) for k in env}
    os.environ.update({k: str(v) for k, v in env.items()})
    try:
        return generate_engineered(seed=7, n_questions=20, ambiguity=0.0)
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_cooccur_questions_byte_identical_to_paraphrase_corpus():
    # cooccur composes with paraphrase; questions must be byte-identical (gold support still resolves)
    base = _gen(GOLDENGRAPH_BENCH_REL_PARAPHRASE="1", GOLDENGRAPH_BENCH_COOCCUR="0")
    co = _gen(GOLDENGRAPH_BENCH_REL_PARAPHRASE="1", GOLDENGRAPH_BENCH_COOCCUR="1")
    bq = [(q.question, q.gold_answer, tuple(q.gold_supporting_fact_ids)) for q in base.questions]
    cq = [(q.question, q.gold_answer, tuple(q.gold_supporting_fact_ids)) for q in co.questions]
    assert bq == cq


def test_cooccur_doc_set_is_strict_superset_with_unique_ids():
    base = _gen(GOLDENGRAPH_BENCH_REL_PARAPHRASE="1", GOLDENGRAPH_BENCH_COOCCUR="0")
    co = _gen(GOLDENGRAPH_BENCH_REL_PARAPHRASE="1", GOLDENGRAPH_BENCH_COOCCUR="1")
    base_ids = {d.id for d in base.documents}
    co_ids = [d.id for d in co.documents]
    assert len(co_ids) == len(set(co_ids))           # unique
    assert base_ids <= set(co_ids)                   # every base doc-id still present (gold resolves)
    assert len(co.documents) > len(base.documents)   # strictly more docs (the co-occurrence)
```

> Check the `QAItem` field names first (`grep -n "class QAItem" erkgbench/qa_e2e/corpora.py`) and adjust
> `q.question` / `q.gold_answer` / `q.gold_supporting_fact_ids` to the real attributes if they differ.

- [ ] **Step 2: Run to verify fail.**

- [ ] **Step 3: Implement** — replace the doc-rendering loop (`engineered.py` ~128-140) with:

```python
    import os as _os
    _cooccur = _os.environ.get("GOLDENGRAPH_BENCH_COOCCUR", "") not in ("", "0", "false")
    documents: list[Document] = []
    for src_id in ids:
        for rel, dst_id in edges[src_id].items():
            # BASE doc -- IDENTICAL main-rng consumption to the non-cooccur path (so the questions,
            # sampled later on the same rng, stay byte-identical).
            s = _render_mention(by_id[src_id], rng, ambiguity)
            o = _render_mention(by_id[dst_id], rng, ambiguity)
            documents.append(Document(
                id=_edge_doc_id(src_id, rel, dst_id),
                text=f"{s} {_render_relation(rel, rng)} {o}.",
                src_surface=s, dst_surface=o,
            ))
            if _cooccur:
                # EXTRA docs: one per phrasing, so all synonyms co-occur on this (subj,obj) pair.
                # Rendered on a deterministic per-edge SIDE rng so the MAIN rng (and the questions) is
                # untouched. Base id stays unsuffixed (above); extras get ::<i>.
                side = random.Random(f"{seed}:{src_id}:{rel}:{dst_id}")
                for i, phrase in enumerate(_REL_PHRASINGS.get(rel, ()), start=1):
                    s2 = _render_mention(by_id[src_id], side, ambiguity)
                    o2 = _render_mention(by_id[dst_id], side, ambiguity)
                    documents.append(Document(
                        id=f"{_edge_doc_id(src_id, rel, dst_id)}::{i}",
                        text=f"{s2} {phrase} {o2}.",
                        src_surface=s2, dst_surface=o2,
                    ))
```

> `random` is already imported at the top of `engineered.py`. `random.Random(str)` seeds
> deterministically across runs (unlike `hash()`, which is salted) — required for reproducibility.

- [ ] **Step 4: Run the cooccur tests to verify pass.** If `test_cooccur_questions_byte_identical_*`
  fails, the MAIN rng was perturbed — confirm the base-doc block uses `rng` (not `side`) and the extra
  block uses `side` exclusively.

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/qa_e2e/engineered.py packages/python/goldenmatch/benchmarks/er-kg-bench/tests/test_qa_engineered_cooccur.py
git commit -m "feat(er-kg-bench): co-occurrence corpus rendering (extra phrasing docs, base id + questions preserved)"
```

---

## Task 3: Validation — argctx-vs-default delta on the co-occurrence corpus (Modal)

Not a unit test — the live-pipeline measurement. Two e2e runs on the SAME co-occurrence corpus; the
delta proves the argctx backend carries the validated signal into live, noisy extraction.

- [ ] **Step 1: argctx backend.** Dispatch (fire-and-forget, per the Modal-ops pattern):
```bash
modal run --detach scripts/distill/modal_bench.py --engine goldengraph --eval end_to_end \
  --n 60 --ambiguity 0.0 --spawn \
  --opts $'GOLDENGRAPH_QA_MODE=auto\nGOLDENGRAPH_SCHEMA_DISCOVER=1\nGOLDENGRAPH_DISCOVER_RESOLVE=argctx\nGOLDENGRAPH_BENCH_REL_PARAPHRASE=1\nGOLDENGRAPH_BENCH_COOCCUR=1'
```
Pull from the `gg-bench-cache` Volume; read the headline + the `[schema-discover]` dump (synonyms should
cluster into the right relations).

- [ ] **Step 2: default backend (control), SAME corpus.** Same dispatch but DROP
  `GOLDENGRAPH_DISCOVER_RESOLVE=argctx` (keeps the default string backend). Expected to fragment (~0.2).
  (Clear the volume result between runs; the filename is shared — pull arg #1's result before dispatching #2.)

- [ ] **Step 3: Verdict.** PASS = argctx clearly beats the default on the same corpus AND argctx ≥ ~0.55
  (in range of closed-vocab discovery, 0.655, allowing live-extraction noise). Record both numbers + the
  delta in the spec's validation section.
  - If argctx ≈ default (~0.2): the live 7B isn't producing usable co-occurrence (extraction too noisy /
    surface pairs don't align). Diagnose via the `[schema-discover]` dump (did synonyms cluster?). Do NOT
    tune to pass — it's a real finding about live extraction quality.

- [ ] **Step 4: Commit** the recorded verdict into the spec.

---

## Done criteria

- `_cluster_predicates_argctx` unit suite green (shared-pair merge, disjoint-pair split, singleton
  isolation, surface normalization, the `argctx` dispatch + discover_schema integration).
- Co-occurrence renderer: questions byte-identical to the paraphrase corpus; doc set a strict superset
  with unique ids.
- Validation: argctx-vs-default delta recorded in the spec (argctx ≥ ~0.55 and clearly > default, or an
  honest diagnosed null).
- Non-argctx / non-cooccur paths byte-unchanged.
