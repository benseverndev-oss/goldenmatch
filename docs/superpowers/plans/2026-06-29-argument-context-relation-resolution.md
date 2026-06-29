# Argument-Context Relation-Resolution Experiment — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Test, locally and LLM-free, whether resolving predicates by ARGUMENT CONTEXT (the entities they connect + types + co-occurrence) clusters relational synonyms correctly — `works at` ≡ `is on staff at` merged while `acquired` ≠ `authored` (and the type-colliding `acquired` ≠ `part_of`) stay apart — the boundary every phrase-level method failed (Phase-2).

**Architecture:** One self-contained module `argctx_resolve.py` builds the experiment's GOLD structure directly (typed entities + co-occurrence, no docs/questions/LLM), derives per-phrasing argument-context features, resolves with two methods (deterministic distributional+type; goldenmatch-with-context-features), and scores B-cubed synonym recovery against the known `_REL_PHRASINGS → relation` ground truth. A runner prints the verdict. Everything is local, deterministic, and Modal-free.

**Tech Stack:** Python 3.12, pytest. Reuses `erkgbench.qa_e2e.engineered._load_entities` and `_REL_PHRASINGS`. The goldenmatch resolver lazily imports `goldenmatch` + `polars` (available in the venv; not needed for the deterministic path or its tests).

**Conventions (verified):**
- Tests live in `packages/python/goldenmatch/benchmarks/er-kg-bench/tests/` (pattern `test_qa_*.py`).
  This experiment's test file: `tests/test_argctx_resolve.py`.
- Run from `packages/python/goldenmatch/benchmarks/er-kg-bench`:
  `POLARS_SKIP_CPU_CHECK=1 GOLDENMATCH_NATIVE=0 D:/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest tests/test_argctx_resolve.py -q`
- `from erkgbench.qa_e2e.engineered import _REL_PHRASINGS, _load_entities` works; `_REL_PHRASINGS` has
  the 5 relations, `_load_entities()` returns **45 entities** (so ~9 per type across 5 types — keep
  `edges_per_rel` modest, ~15; the builder's attempt-cap degrades gracefully if a type pair is scarce).
- Deterministic: seed everything; same seed → identical output.

**Spec:** `docs/superpowers/specs/2026-06-29-argument-context-relation-resolution-experiment-design.md`

---

## File Structure

- **Create** `packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/qa_e2e/argctx_resolve.py` — the whole experiment: `RELATION_TYPES`, `_type_of`, `build_argctx_gold`, `argctx_features`, `resolve_distributional`, `resolve_gm`, `bcubed`, `must_pass_cases`, and a `main()` runner.
- **Create** `packages/python/goldenmatch/benchmarks/er-kg-bench/tests/test_argctx_resolve.py` — unit tests (the bench's `tests/` dir convention).

> **Design note (load-bearing):** `RELATION_TYPES` deliberately includes a TYPE-SIGNATURE COLLISION — `acquired` and `part_of` are BOTH `(org, org)` — so the type signature alone CANNOT separate them and the distributional (pair-set) signal is genuinely load-bearing. Without a collision, types trivially solve the task and the experiment proves nothing.

---

## Task 1: Typed gold builder

**Files:** Create `argctx_resolve.py`; Create `test_argctx_resolve.py`

- [ ] **Step 1: Write the failing test**

```python
# test_argctx_resolve.py
from erkgbench.qa_e2e.argctx_resolve import RELATION_TYPES, build_argctx_gold


def test_relation_types_have_a_deliberate_collision():
    # the distributional signal must be load-bearing: >=2 relations share a type signature
    sigs = list(RELATION_TYPES.values())
    assert len(set(sigs)) < len(sigs), "need a type-signature collision (e.g. acquired & part_of org->org)"


def test_gold_edges_respect_types_and_disjoint_pairs():
    obs = build_argctx_gold(seed=1, edges_per_rel=10, cooccur_frac=1.0)
    # every edge's endpoint types match its relation's signature
    for e in obs:
        assert (e["subj_type"], e["obj_type"]) == RELATION_TYPES[e["rel"]]
    # (subj,obj) pairs are disjoint across the whole corpus (so distinct relations don't share pairs)
    pairs = [(e["subj"], e["obj"]) for e in obs]
    assert len(pairs) == len(set(pairs))
```

- [ ] **Step 2: Run to verify it fails** (`ModuleNotFoundError`).

- [ ] **Step 3: Implement**

```python
# argctx_resolve.py
"""Argument-context relation resolution -- the Phase-2 de-risk experiment. Local, LLM-free, Modal-free:
build the GOLD typed/co-occurring edge structure, derive per-phrasing argument-context features, and
test whether resolving by that context clusters synonyms (works at == is on staff at) while keeping
distinct relations apart (acquired != authored, and the type-colliding acquired != part_of).

Spec: docs/superpowers/specs/2026-06-29-argument-context-relation-resolution-experiment-design.md
"""
from __future__ import annotations

import hashlib
import random
from collections import Counter, defaultdict

from .engineered import _REL_PHRASINGS, _load_entities

#: Coarse entity types. SMALL on purpose so signatures collide across relations.
_TYPES = ("person", "org", "place", "work", "thing")

#: Canonical (subject_type -> object_type) per relation. NOTE the DELIBERATE collision: `acquired` and
#: `part_of` are both (org, org), so the type signature alone cannot separate them -- the pair-set
#: (co-occurrence) signal must. Without a collision the experiment is trivially solved by types.
RELATION_TYPES: dict[str, tuple[str, str]] = {
    "works_at": ("person", "org"),
    "located_in": ("thing", "place"),
    "acquired": ("org", "org"),
    "authored": ("person", "work"),
    "part_of": ("org", "org"),
}


def _type_of(entity_id: str) -> str:
    """Deterministic coarse type for an entity (stable hash of its id)."""
    h = int(hashlib.sha256(entity_id.encode()).hexdigest()[:8], 16)
    return _TYPES[h % len(_TYPES)]


def build_argctx_gold(seed: int, edges_per_rel: int = 20, cooccur_frac: float = 1.0):
    """Gold edge observations: per relation, sample `edges_per_rel` (subj,obj) pairs whose endpoint
    TYPES match the relation's signature, with pairs DISJOINT across the whole corpus (distinct
    relations never share a pair). Each edge renders its relation with all phrasings when
    `cooccur_frac`>=1 (co-occurrence), else a single random phrasing. Returns list of dicts:
    {subj, obj, rel, subj_type, obj_type, phrasings}."""
    rng = random.Random(seed)
    by_type: dict[str, list[str]] = defaultdict(list)
    for e in _load_entities():
        by_type[_type_of(e.id)].append(e.id)
    used: set[tuple[str, str]] = set()
    obs: list[dict] = []
    for rel, (st, ot) in RELATION_TYPES.items():
        subs, objs = by_type.get(st, []), by_type.get(ot, [])
        if not subs or not objs:
            continue
        made, attempts = 0, 0
        while made < edges_per_rel and attempts < edges_per_rel * 50:
            attempts += 1
            s, o = rng.choice(subs), rng.choice(objs)
            if s == o or (s, o) in used:
                continue
            used.add((s, o))
            phr = list(_REL_PHRASINGS[rel])
            if rng.random() >= cooccur_frac:
                phr = [rng.choice(phr)]
            obs.append({"subj": s, "obj": o, "rel": rel,
                        "subj_type": st, "obj_type": ot, "phrasings": phr})
            made += 1
    return obs
```

- [ ] **Step 4: Run tests to verify pass.**
- [ ] **Step 5: Commit** — `git commit -m "feat(argctx): typed gold builder (type-respecting, disjoint pairs, collision)"`

---

## Task 2: Per-phrasing argument-context features

**Files:** Modify `argctx_resolve.py`; Modify test file

- [ ] **Step 1: Write the failing test**

```python
def test_argctx_features_pair_sets_and_type_sig():
    from erkgbench.qa_e2e.argctx_resolve import argctx_features, build_argctx_gold

    obs = build_argctx_gold(seed=1, edges_per_rel=10, cooccur_frac=1.0)
    feats = argctx_features(obs)
    # co-occurrence (frac=1.0): synonyms of one relation share the SAME pair set (rendered on same edges)
    works_phrasings = [p for p in feats if p in ("works at", "is employed at", "is on staff at")]
    assert len(works_phrasings) >= 2
    a, b = works_phrasings[0], works_phrasings[1]
    assert feats[a]["pairs"] == feats[b]["pairs"]
    # type signature is the relation's canonical pair
    assert feats["works at"]["types"].most_common(1)[0][0] == ("person", "org")
```

- [ ] **Step 2: Run to verify fail.**
- [ ] **Step 3: Implement**

```python
def argctx_features(obs):
    """Per surface phrasing -> {'pairs': set[(subj,obj)], 'types': Counter[(subj_type,obj_type)]}.
    The argument-context signature the resolvers cluster on. Derived from gold; no LLM."""
    feats: dict[str, dict] = defaultdict(lambda: {"pairs": set(), "types": Counter()})
    for e in obs:
        for p in e["phrasings"]:
            feats[p]["pairs"].add((e["subj"], e["obj"]))
            feats[p]["types"][(e["subj_type"], e["obj_type"])] += 1
    return dict(feats)
```

- [ ] **Step 4: Run tests to verify pass.**
- [ ] **Step 5: Commit** — `feat(argctx): per-phrasing argument-context features (pair-set + type signature)`

---

## Task 3: Deterministic distributional + type resolver

**Files:** Modify `argctx_resolve.py`; Modify test file

- [ ] **Step 1: Write the failing tests**

```python
def test_distributional_merges_synonyms_separates_distinct():
    from erkgbench.qa_e2e.argctx_resolve import (argctx_features, build_argctx_gold,
                                                 resolve_distributional)
    feats = argctx_features(build_argctx_gold(seed=1, edges_per_rel=15, cooccur_frac=1.0))
    clusters = resolve_distributional(feats)
    cmap = {p: i for i, c in enumerate(clusters) for p in c}
    # defensive: all five relations must have produced edges (45 entities / 5 types ~= 9 each; if a
    # type bucket were starved a phrasing could be absent -> fail legibly, not with a KeyError)
    for p in ("works at", "is on staff at", "acquired", "authored", "part of"):
        assert p in cmap, f"{p!r} absent -- a type bucket was starved; lower edges_per_rel or reseed"
    # must-pass: works-at synonyms merged
    assert cmap["works at"] == cmap["is on staff at"]
    # must-pass: distinct relations apart (different type sig)
    assert cmap["acquired"] != cmap["authored"]
    # the HARD case: acquired vs part_of share (org,org) type sig -> only pair-set separates them
    assert cmap["acquired"] != cmap["part of"]
```

- [ ] **Step 2: Run to verify fail.**
- [ ] **Step 3: Implement** (type signature as a recall-safe BLOCKER, pair-set Jaccard as the DECIDER)

```python
def resolve_distributional(feats, jaccard_threshold: float = 0.5):
    """Cluster phrasings by pair-set Jaccard overlap (synonyms connect the same pairs), with the
    dominant type signature as a blocker (only compare phrasings whose type sigs match). Union-find;
    deterministic (sorted order)."""
    phrases = sorted(feats)
    parent = {p: p for p in phrases}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def sig(p):
        return feats[p]["types"].most_common(1)[0][0] if feats[p]["types"] else None

    for i, a in enumerate(phrases):
        for b in phrases[i + 1:]:
            if sig(a) != sig(b):  # type blocker (recall-safe prune)
                continue
            pa, pb = feats[a]["pairs"], feats[b]["pairs"]
            union_ = pa | pb
            jac = len(pa & pb) / len(union_) if union_ else 0.0
            if jac >= jaccard_threshold:
                parent[find(a)] = find(b)
    groups: dict[str, list[str]] = defaultdict(list)
    for p in phrases:
        groups[find(p)].append(p)
    return [sorted(g) for g in groups.values()]
```

- [ ] **Step 4: Run tests to verify pass.**
- [ ] **Step 5: Commit** — `feat(argctx): deterministic distributional+type resolver`

---

## Task 4: B-cubed recovery metric + must-pass cases

**Files:** Modify `argctx_resolve.py`; Modify test file

- [ ] **Step 1: Write the failing tests**

```python
def test_bcubed_perfect_and_imperfect():
    from erkgbench.qa_e2e.argctx_resolve import bcubed
    gold = {"a": "R1", "b": "R1", "c": "R2"}
    assert bcubed([["a", "b"], ["c"]], gold) == (1.0, 1.0)
    # merge c into R1's cluster: precision drops, recall stays 1
    p, r = bcubed([["a", "b", "c"]], gold)
    assert p < 1.0 and r == 1.0


def test_must_pass_cases_helper():
    from erkgbench.qa_e2e.argctx_resolve import must_pass_cases
    ok, detail = must_pass_cases([["works at", "is on staff at"], ["acquired"], ["authored"],
                                  ["part of"]])
    assert ok is True
```

- [ ] **Step 2: Run to verify fail.**
- [ ] **Step 3: Implement**

```python
def bcubed(clusters, gold_phrase_to_relation):
    """B-cubed precision/recall of a clustering vs the gold phrase->relation map. Per phrasing:
    precision = fraction of its cluster sharing its true relation; recall = fraction of its true
    relation captured in its cluster. Returns (mean_precision, mean_recall) over phrasings present
    in BOTH the clustering and the gold map."""
    cluster_of = {p: frozenset(c) for c in clusters for p in c}
    rel_members = defaultdict(set)
    for p, r in gold_phrase_to_relation.items():
        rel_members[r].add(p)
    items = [p for p in gold_phrase_to_relation if p in cluster_of]
    if not items:
        return (0.0, 0.0)
    precs, recs = [], []
    for p in items:
        c = cluster_of[p] & set(gold_phrase_to_relation)  # ignore phrasings not in gold
        same = {q for q in c if gold_phrase_to_relation.get(q) == gold_phrase_to_relation[p]}
        precs.append(len(same) / len(c) if c else 0.0)
        recs.append(len(same) / len(rel_members[gold_phrase_to_relation[p]]))
    return (round(sum(precs) / len(precs), 4), round(sum(recs) / len(recs), 4))


_MUST_MERGE = ("works at", "is on staff at")
_MUST_SPLIT = [("acquired", "authored"), ("acquired", "part of")]


def must_pass_cases(clusters):
    """The two (binary, metric-independent) must-pass checks from the spec."""
    cmap = {p: i for i, c in enumerate(clusters) for p in c}
    detail = {}
    merged = cmap.get(_MUST_MERGE[0]) == cmap.get(_MUST_MERGE[1]) and _MUST_MERGE[0] in cmap
    detail["works_at_synonyms_merged"] = merged
    splits = {}
    for a, b in _MUST_SPLIT:
        splits[f"{a}!={b}"] = (a in cmap and b in cmap and cmap[a] != cmap[b])
    detail["distinct_kept_apart"] = splits
    ok = merged and all(splits.values())
    return ok, detail
```

> Build the `gold_phrase_to_relation` map in the runner from `_REL_PHRASINGS` (each phrasing -> its
> relation). Phrasings are unique across relations (verify; if any phrasing is shared by two relations
> in `_REL_PHRASINGS`, exclude it from the gold map and note it).

- [ ] **Step 4: Run tests to verify pass.**
- [ ] **Step 5: Commit** — `feat(argctx): B-cubed recovery metric + must-pass checks`

---

## Task 5: goldenmatch-with-context-features resolver

**Files:** Modify `argctx_resolve.py`; Modify test file

- [ ] **Step 1: Write the failing test** (goldenmatch available in the venv; this test is NOT wheel-free)

```python
def test_gm_resolver_runs_and_returns_clusters():
    from erkgbench.qa_e2e.argctx_resolve import argctx_features, build_argctx_gold, resolve_gm
    feats = argctx_features(build_argctx_gold(seed=1, edges_per_rel=10, cooccur_frac=1.0))
    clusters = resolve_gm(feats)
    # every phrasing appears in exactly one cluster (partition); fail-open never drops a phrasing
    flat = [p for c in clusters for p in c]
    assert sorted(flat) == sorted(feats)
```

- [ ] **Step 2: Run to verify fail.**
- [ ] **Step 3: Implement** (record schema per the spec: `type_sig` exact + `neighbors` fuzzy)

```python
def resolve_gm(feats):
    """Relation resolution via goldenmatch dedupe with ARGUMENT-CONTEXT features (not the bare phrase):
    type signature (exact) + connected-entity-name blob (fuzzy). Fixes the impoverished-features
    problem of the earlier gm-over-strings null. Fail-open: any error -> singletons."""
    import goldenmatch as gm
    import polars as pl

    phrases = sorted(feats)
    if len(phrases) < 2:
        return [[p] for p in phrases]
    rows = []
    for p in phrases:
        ts = feats[p]["types"].most_common(1)[0][0] if feats[p]["types"] else ("?", "?")
        names = sorted({n for pair in feats[p]["pairs"] for n in pair})
        rows.append({"type_sig": f"{ts[0]}>{ts[1]}", "neighbors": " | ".join(names), "phrase": p})
    df = pl.DataFrame({c: [r[c] for r in rows] for c in ("type_sig", "neighbors", "phrase")})
    try:
        result = gm.dedupe_df(df, exact=["type_sig"], fuzzy={"neighbors": 0.5},
                              confidence_required=False)
    except Exception:
        return [[p] for p in phrases]
    clusters, seen = [], set()
    for info in getattr(result, "clusters", {}).values():
        members = [phrases[int(i)] for i in info.get("members", ()) if 0 <= int(i) < len(phrases)]
        if members:
            clusters.append(sorted(set(members)))
            seen.update(members)
    clusters.extend([p] for p in phrases if p not in seen)
    return clusters
```

- [ ] **Step 4: Run test to verify pass.** (If `dedupe_df`'s `exact`/`fuzzy` kwargs differ, check the signature: `python -c "import goldenmatch,inspect;print(inspect.signature(goldenmatch.dedupe_df))"` and adjust.)
- [ ] **Step 5: Commit** — `feat(argctx): goldenmatch resolver with argument-context features`

---

## Task 6: Runner + RUN THE EXPERIMENT (the verdict)

**Files:** Modify `argctx_resolve.py`

- [ ] **Step 1: Implement `main()`** — build gold (best-case: `cooccur_frac=1.0`, typed), build the
  `_REL_PHRASINGS`-derived gold map, run BOTH resolvers, print B-cubed (p/r) + the must-pass detail for
  each, and the PASS/FAIL verdict (B-cubed >= 0.9/0.9 AND must-pass true).

```python
def _gold_map():
    m = {}
    for rel, phrasings in _REL_PHRASINGS.items():
        for p in phrasings:
            m[p] = m.get(p, rel)  # first relation wins; collisions excluded below
    # drop any phrasing claimed by >1 relation (ambiguous ground truth)
    counts = Counter(p for ps in _REL_PHRASINGS.values() for p in ps)
    return {p: r for p, r in m.items() if counts[p] == 1}


def main():
    obs = build_argctx_gold(seed=20260629, edges_per_rel=25, cooccur_frac=1.0)
    feats = argctx_features(obs)
    gold = _gold_map()
    for name, fn in (("distributional", resolve_distributional), ("goldenmatch", resolve_gm)):
        clusters = fn(feats)
        p, r = bcubed(clusters, gold)
        ok, detail = must_pass_cases(clusters)
        passed = p >= 0.9 and r >= 0.9 and ok
        print(f"[{name}] B-cubed P={p} R={r} | must_pass={ok} {detail} | VERDICT={'PASS' if passed else 'FAIL'}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: RUN IT** (the experiment):
  `POLARS_SKIP_CPU_CHECK=1 GOLDENMATCH_NATIVE=0 PYTHONIOENCODING=utf-8 D:/show_case/goldenmatch/.venv/Scripts/python.exe -m erkgbench.qa_e2e.argctx_resolve` (from the bench dir, `PYTHONPATH=.`).
  Record the two VERDICT lines.

- [ ] **Step 3: Interpret** against the gate:
  - **Distributional PASS** (B-cubed >= 0.9/0.9 + must-pass) → argument-context IS the lever; open-vocab is crossable. Record in the spec; the gm comparison shows whether the product's resolver also exploits it.
  - **Distributional FAIL** → even with perfect structure + best-case signal, argument-context doesn't resolve synonymy → a hard negative (record honestly; do NOT tune to pass).

- [ ] **Step 4: Commit** — `feat(argctx): experiment runner + recorded de-risk verdict`

---

## Task 7: Ablation (only if Task 6 distributional PASSES)

Attribute the win to the right signal with two clean, concrete knobs. First add a
`use_type_blocker: bool = True` parameter to `resolve_distributional` (when False, compare ALL phrasing
pairs regardless of type signature — isolates the pair-set/co-occurrence signal alone). Then run:

- **Co-occurrence WITHOUT types** — `cooccur_frac=1.0`, `resolve_distributional(..., use_type_blocker=False)`.
  Expectation: still passes (synonyms share identical pair-sets → Jaccard 1.0; distinct relations have
  disjoint pair-sets → 0). Shows pair-set/co-occurrence is sufficient on its own.
- **Types WITHOUT co-occurrence** — `cooccur_frac=0.0` (one phrasing per edge → synonyms get DISJOINT
  pairs). Expectation: FAILS to merge synonyms (Jaccard 0 even within a relation), and the type blocker
  can't rescue it (it prunes, the Jaccard still decides; and the `acquired`/`part_of` collision means
  type-sig alone couldn't separate them anyway). Shows types alone are insufficient.

The expected conclusion: **co-occurrence (the pair-set distributional signal) is the necessary+sufficient
lever; entity types are a helpful blocker but insufficient alone** (the collision proves it). Record the
two B-cubed numbers + this conclusion in the spec's validation section.

- [ ] Add `use_type_blocker` param, run both ablation configs, record numbers in the spec, commit.

---

## Done criteria

- `argctx_resolve.py` unit suite green (gold builder, features, distributional resolver, B-cubed,
  must-pass, gm resolver).
- The experiment runner produces a recorded PASS/FAIL verdict for both resolvers.
- The verdict + ablation recorded in the spec's validation section.
- No Modal, no LLM used. Deterministic (seed-stable).
