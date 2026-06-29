# GoldenGraph Schema Discovery Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Discover a canonical relation schema (vocabulary + per-relation direction) from a corpus's open extractions, so the schema-constrained ingest win (0.672 on the engineered corpus) survives without a human hand-feeding the relation list.

**Architecture:** A new `goldengraph/schema_discovery.py` produces a `RelationSchema` — the exact object the existing `schema.py::canonicalize_extraction` already consumes — by (a) clustering raw predicate strings into canonical relations, (b) deciding each surface phrase's canonical direction from source word-order + passive markers, and (c) an optional bounded LLM tie-break. `ingest_corpus` gains a one-pass discovery flow gated `GOLDENGRAPH_SCHEMA_DISCOVER=1`: extract-all → discover → canonicalize-all → resolve/store. Discovery only *replaces* the hand-coded `default_schema(vocab)`; the canonicalizer is untouched.

**Tech Stack:** Python 3.12, pytest, numpy. Reuses `goldengraph/schema.py` (`RelationSchema`, `canonicalize_extraction`, `_norm`), `goldengraph/extract.py` (`Extraction`, `Mention`, `Relationship`), the run's embedder (`GoldenmatchEmbedder.embed(list[str]) -> list[list[float]]`). Spec: `docs/superpowers/specs/2026-06-29-goldengraph-schema-discovery-design.md`.

**Conventions for this codebase:**
- Tests are wheel-free + key-free: no native build, no real LLM. Use plain Python stubs.
- Run tests from `packages/python/goldengraph`:
  `PYTHONPATH=. GOLDENGRAPH_NATIVE=0 POLARS_SKIP_CPU_CHECK=1 D:/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest tests/<file> -q`
- `_norm(s)` lowercases + maps `_`↔space + collapses whitespace. Canonical relation labels are underscored (`works_at`); alias sets hold `_norm`'d (spaced) phrases for exact `.match()` membership.

---

## File Structure

- **Create** `packages/python/goldengraph/goldengraph/schema_discovery.py` — the discovery unit. Pure-ish (deterministic backbone + optional 1 LLM call). Public: `discover_schema(extractions, sources, embedder, llm=None) -> RelationSchema` and `schema_discover_enabled()`. Private helpers: `_collect_edges`, `_cluster_predicates`, `_phrase_is_reverse`, `_assemble_schema`, `_llm_consolidate`.
- **Create** `packages/python/goldengraph/tests/test_schema_discovery.py` — unit tests (clustering, direction, end-to-end recovery, LLM tie-break stub).
- **Modify** `packages/python/goldengraph/goldengraph/ingest.py` — add the gated one-pass discovery flow into `ingest_corpus`.
- **Modify** `packages/python/goldenmatch/benchmarks/er-kg-bench/tests/` — a recovery-check helper used by the Phase-1 gate (optional; see Task 6).

---

## Task 1: Module skeleton + edge collection

**Files:**
- Create: `packages/python/goldengraph/goldengraph/schema_discovery.py`
- Test: `packages/python/goldengraph/tests/test_schema_discovery.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_schema_discovery.py
from goldengraph.extract import Extraction, Mention, Relationship
from goldengraph.schema_discovery import _collect_edges


def _ext(mentions, rels):
    return Extraction(mentions=[Mention(name=n, typ="concept") for n in mentions],
                      relationships=[Relationship(*r) for r in rels])


def test_collect_edges_pairs_surfaces_predicate_and_source():
    ext = _ext(["A", "B"], [(0, "acquired", 1)])
    edges = _collect_edges([ext], ["A acquired B."])
    # one edge: (subj_surface, predicate, obj_surface, source)
    assert edges == [("A", "acquired", "B", "A acquired B.")]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `PYTHONPATH=. GOLDENGRAPH_NATIVE=0 POLARS_SKIP_CPU_CHECK=1 D:/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest tests/test_schema_discovery.py::test_collect_edges_pairs_surfaces_predicate_and_source -q`
Expected: FAIL — `ModuleNotFoundError: goldengraph.schema_discovery`.

- [ ] **Step 3: Write minimal implementation**

```python
# goldengraph/schema_discovery.py
"""Discover a RelationSchema (vocabulary + direction) from open extractions, so the
schema-constrained ingest win generalizes to corpora where the schema is unknown.
Produces the SAME RelationSchema the hand-coded default_schema does; the canonicalizer
(schema.py) is unchanged. Deterministic backbone + one optional bounded LLM call."""
from __future__ import annotations

import os

from .extract import Extraction
from .schema import RelationSchema, _norm


def _collect_edges(extractions, sources):
    """Flatten (extraction, source_text) pairs into edge tuples:
    (subj_surface, predicate, obj_surface, source_text). Drops edges whose endpoints are
    out of range (defensive; extraction already validates, but discovery must not crash)."""
    edges = []
    for ext, src in zip(extractions, sources):
        n = len(ext.mentions)
        for r in ext.relationships:
            if 0 <= r.subj < n and 0 <= r.obj < n:
                edges.append((ext.mentions[r.subj].name, r.predicate,
                              ext.mentions[r.obj].name, src or ""))
    return edges
```

- [ ] **Step 4: Run test to verify it passes**

Run: same command as Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldengraph/goldengraph/schema_discovery.py packages/python/goldengraph/tests/test_schema_discovery.py
git commit -m "feat(goldengraph): schema_discovery skeleton + edge collection"
```

---

## Task 2: Predicate clustering (deterministic vocabulary)

Cluster raw predicate strings into canonical relations. Two predicates join a cluster when they are near-identical by string (one `_norm` is a substring of the other, or they differ only by a passive `by` tail) OR semantically close by embedding cosine. Union-find over the pairwise matches (mirrors `ingest.py::_embed_cluster`). The most frequent member (by `_norm`) names the cluster; the canonical label is its underscored form.

**Files:**
- Modify: `goldengraph/schema_discovery.py`
- Test: `tests/test_schema_discovery.py`

- [ ] **Step 1: Write the failing tests**

```python
from goldengraph.schema_discovery import _cluster_predicates


class _StubEmbedder:
    """Deterministic toy embedder: vector = per-token presence over a tiny vocab, so
    'works at' and 'is employed at' share the 'work/employ' axis only if we map synonyms.
    For unit tests we keep it SIMPLE -- identical-after-strip strings embed identically,
    everything else orthogonal -- so clustering is driven by the STRING rules here, and
    the embedding axis is exercised separately in Step via a synonym map."""
    def embed(self, texts):
        import numpy as np
        vocab = ["work", "employ", "acquir", "buy", "author", "wrote", "locat", "part"]
        out = []
        for t in texts:
            v = np.array([1.0 if stem in t.lower() else 0.0 for stem in vocab])
            out.append((v / (np.linalg.norm(v) + 1e-9)).tolist())
        return out


def test_cluster_merges_passive_and_substring_variants():
    preds = ["acquired", "acquired by", "was acquired by", "authored", "was authored by"]
    clusters = _cluster_predicates(preds, _StubEmbedder())
    # acquired-family is one cluster, authored-family another
    fam = {frozenset(c) for c in clusters}
    assert frozenset({"acquired", "acquired by", "was acquired by"}) in fam
    assert frozenset({"authored", "was authored by"}) in fam


def test_cluster_merges_embedding_synonyms():
    # 'acquired' and 'bought' share the 'acquir'/'buy' is not enough alone; the embedder
    # gives them distinct axes, so they only merge if cosine>=T is met. Here they are NOT
    # synonyms in the toy embedder -> stay separate. Guards against over-merging.
    clusters = _cluster_predicates(["acquired", "located in"], _StubEmbedder())
    assert len(clusters) == 2
```

- [ ] **Step 2: Run to verify they fail** (`_cluster_predicates` undefined).

- [ ] **Step 3: Implement**

```python
def _passive_strip(p: str) -> str:
    """Normalized predicate with a leading 'was/were/is/are/been' and/or a trailing 'by'
    removed -- so 'was acquired by' and 'acquired' share a stem for string-clustering."""
    toks = _norm(p).split()
    while toks and toks[0] in ("was", "were", "is", "are", "been", "being"):
        toks = toks[1:]
    if toks and toks[-1] == "by":
        toks = toks[:-1]
    return " ".join(toks)


def _string_close(a: str, b: str) -> bool:
    """True if two predicates are the same relation by STRING: equal after passive-strip,
    or one normalized form is a token-substring of the other."""
    sa, sb = _passive_strip(a), _passive_strip(b)
    if sa and sa == sb:
        return True
    na, nb = _norm(a), _norm(b)
    return bool(na) and bool(nb) and (na in nb or nb in na)


def _cluster_predicates(predicates, embedder, cosine_threshold: float = 0.82):
    """Union-find clustering of distinct raw predicates. Edge when _string_close OR
    embedding cosine >= threshold. Deterministic: predicates processed in sorted order."""
    import numpy as np

    uniq = sorted({p for p in predicates if _norm(p)})
    n = len(uniq)
    if n == 0:
        return []
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        parent[find(a)] = find(b)

    vecs = np.asarray(embedder.embed(uniq), dtype=float)
    ok = vecs.ndim == 2 and vecs.shape[0] == n
    if ok:
        unit = vecs / (np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-12)
        sim = unit @ unit.T
    for i in range(n):
        for j in range(i + 1, n):
            if _string_close(uniq[i], uniq[j]) or (ok and sim[i, j] >= cosine_threshold):
                union(i, j)
    groups: dict[int, list[str]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(uniq[i])
    return [sorted(g) for g in groups.values()]
```

- [ ] **Step 4: Run tests to verify pass.**

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(goldengraph): predicate clustering for schema discovery"
```

---

## Task 3: Direction detection (source word-order + passive)

Decide, per surface predicate phrase, whether it is a REVERSE alias (extracted edge must be flipped to canonical) or FORWARD. Rule: a phrase is reverse if it is passive (`_passive_strip` removed a `by` tail or a leading copula+`by`) OR, across its edges, the extracted (subj→obj) is consistently OPPOSITE to the source word-order. Canonical = active, subject-first.

**Files:**
- Modify: `goldengraph/schema_discovery.py`
- Test: `tests/test_schema_discovery.py`

- [ ] **Step 1: Write the failing tests**

```python
from goldengraph.schema_discovery import _phrase_is_reverse


def test_active_phrase_is_forward():
    # "A acquired B." extracted (A, acquired, B): subj before obj in source -> forward.
    edges = [("A", "acquired", "B", "A acquired B.")]
    assert _phrase_is_reverse("acquired", edges) is False


def test_passive_phrase_is_reverse():
    # "B was acquired by A." extracted (B, was acquired by, A): passive -> reverse (flip).
    edges = [("B", "was acquired by", "A", "B was acquired by A.")]
    assert _phrase_is_reverse("was acquired by", edges) is True


def test_reversed_extraction_detected_by_source_order():
    # active phrase but the MODEL reversed it: source "A located in B", extracted (B, located in, A).
    # obj 'A' appears before subj 'B' in source -> extracted is opposite to source -> reverse.
    edges = [("B", "located in", "A", "A located in B.")]
    assert _phrase_is_reverse("located in", edges) is True
```

- [ ] **Step 2: Run to verify fail.**

- [ ] **Step 3: Implement**

```python
def _is_passive(predicate: str) -> bool:
    toks = _norm(predicate).split()
    return bool(toks) and (toks[-1] == "by"
                           or toks[0] in ("was", "were", "is", "are", "been", "being"))


def _source_says_reversed(subj_surface, obj_surface, source) -> bool | None:
    """True if, in the source text, the extracted OBJECT appears before the extracted SUBJECT
    (i.e. the extraction is opposite to subject-first source order). None if positions can't
    be found (surface missing). Case-insensitive substring positions."""
    s = (source or "").lower()
    pi, oi = s.find(subj_surface.lower()), s.find(obj_surface.lower())
    if pi < 0 or oi < 0:
        return None
    return oi < pi


def _phrase_is_reverse(phrase: str, edges) -> bool:
    """A surface phrase is a REVERSE alias if it is passive, OR the majority of its edges
    are source-reversed (extracted object precedes subject in the source). Passive wins
    outright. Ties / no source signal -> forward (the conservative default)."""
    if _is_passive(phrase):
        return True
    votes = [_source_says_reversed(s, o, src) for (s, _p, o, src) in edges]
    seen = [v for v in votes if v is not None]
    if not seen:
        return False
    return sum(seen) > len(seen) / 2
```

- [ ] **Step 4: Run tests to verify pass.**

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(goldengraph): source-order + passive direction detection"
```

---

## Task 4: Assemble the RelationSchema (end-to-end discovery)

Build a `RelationSchema` from the clusters: canonical label = underscored most-frequent member; each surface phrase goes to `forward[r]` or `reverse[r]` per Task 3. Then `discover_schema` ties Tasks 1–4 together. This is the **headline unit test**: discover the schema from synthetic `(source, extraction)` data including a reversed/passive case, and confirm the resulting schema, fed to the *real* `canonicalize_extraction`, canonicalizes correctly.

**Files:**
- Modify: `goldengraph/schema_discovery.py`
- Test: `tests/test_schema_discovery.py`

- [ ] **Step 1: Write the failing tests**

```python
from collections import Counter
from goldengraph.schema import canonicalize_extraction
from goldengraph.schema_discovery import _assemble_schema, discover_schema


def test_assemble_schema_labels_and_directions():
    clusters = [["acquired", "was acquired by"], ["located in"]]
    edges_by_phrase = {
        "acquired": [("A", "acquired", "B", "A acquired B.")],
        "was acquired by": [("B", "was acquired by", "A", "B was acquired by A.")],
        "located in": [("X", "located in", "Y", "X located in Y.")],
    }
    sch = _assemble_schema(clusters, edges_by_phrase)
    # canonical label = most frequent member, underscored
    assert "acquired" in sch.relations and "located_in" in sch.relations
    # forward holds the active phrase, reverse holds the passive one
    assert sch.match("acquired") == ("acquired", False)
    assert sch.match("was acquired by") == ("acquired", True)


def test_discover_schema_recovers_and_canonicalizes_reversed_edge():
    # Corpus: one active doc, one PASSIVE doc for the same relation. The passive edge is
    # extracted object-first; discovery must mark its phrase reverse so canonicalize flips it.
    exts = [
        _ext(["A", "B"], [(0, "acquired", 1)]),          # A acquired B  (canonical)
        _ext(["C", "D"], [(0, "was acquired by", 1)]),   # C was acquired by D == D acquired C
    ]
    sources = ["A acquired B.", "C was acquired by D."]
    sch = discover_schema(exts, sources, _StubEmbedder())
    # canonicalize the passive extraction -> edge should become (D, acquired, C)
    out = canonicalize_extraction(exts[1], sch)
    r = out.relationships[0]
    assert (out.mentions[r.subj].name, r.predicate, out.mentions[r.obj].name) == ("D", "acquired", "C")
```

- [ ] **Step 2: Run to verify fail.**

- [ ] **Step 3: Implement**

> **Module imports:** add `from collections import Counter` to the module header (Task 1's skeleton
> only imported `os`/`.extract`/`.schema`). `numpy` stays a local import inside functions.

```python
def _assemble_schema(clusters, edges_by_phrase) -> RelationSchema:
    relations, forward, reverse = [], {}, {}
    for members in clusters:
        # canonical label = most frequent member by edge count, PREFERRING a non-passive member so
        # the relation name is the active form ('acquired', not 'acquired_by'). Fall back to the full
        # member set only if every member is passive. (tie -> shortest, then alpha)
        def _key(m):
            return (len(edges_by_phrase.get(m, ())), -len(m), tuple(-ord(c) for c in m))
        active = [m for m in members if not _is_passive(m)]
        label_phrase = max(active or members, key=_key)
        rel = _norm(label_phrase).replace(" ", "_")
        if rel in forward:  # cluster label collision -> merge into existing
            pass
        fwd, rev = set(forward.get(rel, set())), set(reverse.get(rel, set()))
        for m in members:
            (rev if _phrase_is_reverse(m, edges_by_phrase.get(m, ())) else fwd).add(_norm(m))
        fwd.add(_norm(rel))  # the canonical label is always a forward alias
        forward[rel] = frozenset(a for a in fwd if a)
        reverse[rel] = frozenset(a for a in rev if a)
        if rel not in relations:
            relations.append(rel)
    return RelationSchema(relations=tuple(relations), forward=forward, reverse=reverse)


def discover_schema(extractions, sources, embedder, llm=None) -> RelationSchema:
    """Discover a RelationSchema from open extractions + their source texts. Deterministic
    backbone; `llm` (optional) consolidates ambiguous clusters (Task 5)."""
    edges = _collect_edges(extractions, sources)
    by_phrase: dict[str, list] = {}
    for (s, p, o, src) in edges:
        by_phrase.setdefault(p, []).append((s, p, o, src))
    clusters = _cluster_predicates(list(by_phrase), embedder)
    if llm is not None:
        clusters = _llm_consolidate(clusters, llm)  # Task 5
    return _assemble_schema(clusters, by_phrase)
```

- [ ] **Step 4: Run tests to verify pass.** (Note: `_llm_consolidate` is referenced but only called when `llm` is passed; define a stub `def _llm_consolidate(clusters, llm): return clusters` now, fleshed out in Task 5, so the module imports.)

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(goldengraph): assemble RelationSchema from clusters (end-to-end discovery)"
```

---

## Task 5: Bounded LLM tie-break (consolidate clusters)

One pinned (temperature 0) LLM call that merges near-duplicate clusters the deterministic backbone left separate (e.g. `acquired` vs `purchased`). Fail-open: any error/parse failure returns the input clusters unchanged. The merge is applied as deterministic post-processing of the parsed output (union the named groups), preserving reproducibility.

**Files:**
- Modify: `goldengraph/schema_discovery.py`
- Test: `tests/test_schema_discovery.py`

- [ ] **Step 1: Write the failing tests**

```python
from goldengraph.schema_discovery import _llm_consolidate


class _StubLLM:
    """Returns a JSON merge map: groups of cluster-indices to union."""
    def __init__(self, reply): self._reply = reply
    def complete(self, prompt): return self._reply


def test_llm_consolidate_merges_named_groups():
    clusters = [["acquired"], ["purchased"], ["located in"]]
    # LLM says clusters 0 and 1 are the same relation
    llm = _StubLLM('{"merge": [[0, 1]]}')
    out = _llm_consolidate(clusters, llm)
    fam = {frozenset(c) for c in out}
    assert frozenset({"acquired", "purchased"}) in fam
    assert frozenset({"located in"}) in fam


def test_llm_consolidate_fail_open_on_bad_json():
    clusters = [["acquired"], ["purchased"]]
    out = _llm_consolidate(clusters, _StubLLM("not json"))
    assert out == clusters  # unchanged
```

- [ ] **Step 2: Run to verify fail** (current stub returns clusters unchanged, so `test_llm_consolidate_merges_named_groups` FAILS; the fail-open test passes — that's fine, the merge test drives the impl).

- [ ] **Step 3: Implement** (replace the Task-4 stub)

```python
import json

_CONSOLIDATE_PROMPT = (
    "These are candidate relation clusters discovered from a corpus, each a list of surface "
    "phrases. Merge clusters that express the SAME relation (e.g. 'acquired' and 'purchased'). "
    "Reply with ONLY JSON: {{\"merge\": [[i, j, ...], ...]}} listing groups of cluster INDICES to "
    "union; omit clusters that stand alone.\nClusters:\n{clusters}"
)


def _llm_consolidate(clusters, llm):
    """Union clusters the LLM says are the same relation. Pinned/deterministic post-processing;
    fail-open (any error -> input unchanged)."""
    if len(clusters) < 2:
        return clusters
    try:
        listing = "\n".join(f"{i}: {c}" for i, c in enumerate(clusters))
        raw = llm.complete(_CONSOLIDATE_PROMPT.format(clusters=listing))
        s = raw[raw.index("{"): raw.rindex("}") + 1]
        groups = json.loads(s).get("merge", [])
    except Exception:
        return clusters
    parent = list(range(len(clusters)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]; x = parent[x]
        return x

    for group in groups:
        idxs = [i for i in group if isinstance(i, int) and 0 <= i < len(clusters)]
        for k in idxs[1:]:
            parent[find(k)] = find(idxs[0])
    merged: dict[int, list] = {}
    for i in range(len(clusters)):
        merged.setdefault(find(i), []).extend(clusters[i])
    return [sorted(set(c)) for c in merged.values()]
```

- [ ] **Step 4: Run tests to verify pass.**

- [ ] **Step 5: Run the WHOLE discovery suite + commit**

```bash
PYTHONPATH=. GOLDENGRAPH_NATIVE=0 POLARS_SKIP_CPU_CHECK=1 D:/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest tests/test_schema_discovery.py -q
git add -A && git commit -m "feat(goldengraph): bounded LLM cluster consolidation (fail-open, pinned)"
```

---

## Task 6: Ingest wiring (one-pass discovery flow)

Add the gated discovery flow to `ingest_corpus`. When `GOLDENGRAPH_SCHEMA_DISCOVER=1`: open-extract every doc (no vocab, no per-doc canonicalize), run `discover_schema` over all extractions + the doc texts, then canonicalize each extraction with the discovered schema before the existing resolve/commit. Fail-soft: discovery error → fall back to today's behavior (no canonicalization), logged.

**Files:**
- Modify: `packages/python/goldengraph/goldengraph/ingest.py` (`ingest_corpus`, and a guard in `_prepare_doc` so per-doc canonicalize is SKIPPED in discovery mode)
- Test: `tests/test_schema_discovery.py` (integration test with stub extractor + embedder, no store)

- [ ] **Step 1: Write the failing test** (integration at the `discover_schema`→`canonicalize` seam, store-free)

```python
def test_discovery_flow_canonicalizes_corpus_edges(monkeypatch):
    # Two docs, one active one passive; after discovery+canonicalize BOTH edges point canonical.
    exts = [_ext(["A", "B"], [(0, "acquired", 1)]),
            _ext(["C", "D"], [(0, "was acquired by", 1)])]
    sources = ["A acquired B.", "C was acquired by D."]
    sch = discover_schema(exts, sources, _StubEmbedder())
    canon = [canonicalize_extraction(e, sch) for e in exts]
    got = [(c.mentions[c.relationships[0].subj].name, c.relationships[0].predicate,
            c.mentions[c.relationships[0].obj].name) for c in canon]
    assert got == [("A", "acquired", "B"), ("D", "acquired", "C")]
```

- [ ] **Step 2: Run to verify pass** — **this is a SEAM-CONFIRMATION check, not red-first TDD.** It asserts the discover→canonicalize contract the ingest wiring relies on, and should PASS on Task 4–5 code (no new production code drives it). If it passes, proceed to wire ingest.

> **Embedder required:** the discovery flow passes `ingest_corpus`'s `embedder` into `discover_schema`. With `embedder=None`, `_cluster_predicates` raises → the fail-soft try/except makes discovery a no-op (falls back to open extraction). That's spec-consistent, but it means the Phase-1 Modal run MUST supply an embedder (the bench harness does — it builds `GoldenmatchEmbedder`).

- [ ] **Step 3: Add the gate + flow to `ingest.py`**

Add near the other gate helpers:

```python
def _schema_discover_enabled() -> bool:
    return os.environ.get("GOLDENGRAPH_SCHEMA_DISCOVER", "0") not in ("0", "false", "")
```

In `_prepare_doc`, make the per-doc canonicalize skip in discovery mode (discovery canonicalizes later with the global schema):

```python
        extraction = (extractor or _extract)(text, llm)
        if not _schema_discover_enabled():
            extraction = _maybe_canonicalize(extraction)
```

In `ingest_corpus`, before the commit loop, when discovery is enabled: run the parallel prepare to get extractions, discover the schema over (extractions, docs), canonicalize each prepared extraction, THEN commit. Concretely, gate the existing prepare/commit: if `_schema_discover_enabled()`, collect `prepared = list(map(_prep, docs))` (or via the ThreadPool), build `schema = discover_schema([p[0] for p in prepared], docs, embedder, llm)` inside a try/except (fail-soft → skip canonicalize), replace each `prepared[i]` extraction with `canonicalize_extraction(prepared[i][0], schema)`, then run the existing commit loop. Keep the non-discovery path exactly as-is.

> Implementation note for the worker: `_prepare_doc` returns `(extraction, entities, new_fps)`. Resolution (`entities`) is computed from `extraction.mentions`, which canonicalization does NOT change (it only rewrites relationships), so resolving in prepare and canonicalizing the relationships afterward is consistent. Re-canonicalize only the `extraction` object before `_commit_doc` (which calls `build_batch` off the relationships).

- [ ] **Step 4: Test the ingest path** — add a focused test that monkeypatches `_extract` to a stub returning the active/passive extractions and asserts the committed batch edges are canonical. If a store stub is heavy, assert at the `build_batch` level instead (call `build_batch(canonicalized_extraction, entities, at=1)` and check the edge predicate/direction). Run:
`PYTHONPATH=. GOLDENGRAPH_NATIVE=0 POLARS_SKIP_CPU_CHECK=1 D:/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest tests/test_schema_discovery.py -q`

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(goldengraph): one-pass schema-discovery flow in ingest_corpus (gated, fail-soft)"
```

---

## Task 7: Phase-1 validation gate (engineered, on Modal)

Not a unit test — a measurement that proves discovery recovers the hand-fed result. Uses the existing Modal bench (`scripts/distill/modal_bench.py`) on the engineered corpus with discovery ON and **no** hand-fed vocab.

**Files:**
- Modify (optional): `packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/qa_e2e/` — a small recovery-check that compares the discovered schema's relations/directions against `engineered.RELATION_SCHEMA`.
- No new bench code needed for the e2e number: pass discovery via `--opts`.

- [ ] **Step 1: Schema-recovery check.** Add a tiny script/test that builds the engineered corpus, open-extracts it (or uses the captured extractions), runs `discover_schema`, and reports: relation-set precision/recall vs the 5 known relations + per-relation direction agreement. Target: recovers all 5 with correct direction.

- [ ] **Step 2: End-to-end on Modal.** Dispatch:
```bash
modal run --detach scripts/distill/modal_bench.py --engine goldengraph --eval end_to_end \
  --n 60 --ambiguity 0.0 --spawn \
  --opts $'GOLDENGRAPH_QA_MODE=auto\nGOLDENGRAPH_SCHEMA_DISCOVER=1'
```
(Note: NO `GOLDENGRAPH_RELATION_VOCAB` / `GOLDENGRAPH_SCHEMA_CANON` — discovery supplies the schema.)
Expected: answer-match **≈ 0.655–0.689** (within ~1-question noise of the hand-fed 0.672). Pull the result from the `gg-bench-cache` Volume per the Modal-ops pattern (detach + spawn + Volume poll).

- [ ] **Step 3: Record** the recovery numbers + the e2e number in `docs/superpowers/specs/2026-06-29-goldengraph-schema-discovery-design.md` (validation section) and commit.

> If the e2e lands BELOW the band: do NOT tune to the number. Diagnose with the recovery check first (wrong relations? wrong directions?) and the `GOLDENGRAPH_CHAIN_DEBUG` trace, exactly as the schema-constrained lever was localized. The recovery check isolates discovery quality from the rest of the pipeline.

---

## Task 8: Phase-2 stress test (schema-unknown) — separate, gated on Phase 1

Only after Phase 1 passes. A harder corpus where the answer is not known up front: a paraphrase-injected engineered variant (multiple surface phrasings per relation, so clustering is actually exercised) and/or a real multi-hop set (MuSiQue). This is its own measurement; **no pre-committed number** — report honestly whether discovery holds and how much advantage carries to messy text. Scope the exact corpus in a follow-up plan once Phase 1 is green (keeps this plan to one testable subsystem).

---

## Done criteria

- `discover_schema` unit suite green (clustering, direction incl. passive + source-reversed, end-to-end recovery, LLM tie-break + fail-open).
- `ingest_corpus` discovery flow gated + fail-soft; non-discovery path byte-unchanged.
- Phase-1 gate: discovered schema recovers the 5 engineered relations with correct direction AND e2e ≈ 0.655–0.689.
- Validation numbers recorded in the spec.
