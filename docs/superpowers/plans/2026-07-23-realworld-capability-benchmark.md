# Real-World Capability Benchmark (Wikidata) Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove GoldenGraph's structural capability wins (size-invariant set aggregation; later, temporal as-of) on a REAL, recognizable dataset (Wikidata company structure) instead of the synthetic fan-out corpus, by swapping only the corpus generator and reusing the existing scoring/floor/gate harness.

**Architecture:** The synthetic aggregation bench (`erkgbench/qa_e2e/aggregation.py`) already owns everything except the data: it renders `Document`s, builds the GoldenGraph store, and scores `goldengraph_aggregate` set-F1 vs a `passage_window_floor` by gold-set-size bucket. This plan adds a real-data corpus generator backed by a **committed Wikidata fixture** (a cached SPARQL pull, never live at bench time) that emits the SAME `Document` + `AggQuestion` types, plus a `run_realworld_aggregation` runner mirroring `run_aggregation_deterministic`, and a `--source realworld` CLI switch. Phase 0 is companies + aggregation only; temporal (Phase 1) reuses `temporal.py` the same way.

**Tech Stack:** Python 3.12, the er-kg-bench harness (`packages/python/goldenmatch/benchmarks/er-kg-bench`), `goldengraph-native` wheel (via `ablation._build_store`), Wikidata SPARQL (`https://query.wikidata.org/sparql`, CC0), pytest.

---

## Context: what already exists and is REUSED unchanged

Read these before starting (all under `packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/qa_e2e/`):

- `engineered.py`: `_Entity(id, canonical, variants)` dataclass; `_render_mention(ent, rng, ambiguity)` picks canonical-or-a-variant; `_edge_doc_id(src_id, rel, dst_id)`.
- `corpora.py`: `Document(id, text, src_surface="", dst_surface="")` and `QACorpus`. **The real generator produces `_Entity`s (from engineered) and `Document`s (from corpora) of these exact shapes.**
- `aggregation.py`:
  - `generate_aggregation(*, seed, n_anchors, ambiguity)` -> `(tuple[Document], list[AggQuestion])` — the synthetic generator we mirror.
  - `AggQuestion(id, kind, question, anchor_id, relation, gold_members, gold_count)`.
  - `agg_documents_corpus(docs) -> QACorpus`, `set_f1`, `count_accuracy`, `size_bucket`, `passage_window_floor`, `goldengraph_aggregate`, `gate_verdicts`, `_mean_by_bucket`, `run_aggregation_deterministic(*, seed, n_anchors, ambiguity, passage_k, llm=None)`. **ALL reused as-is.**
- `run_aggregation.py`: the CLI entry point (add a `--source` switch here).

**Key invariant to preserve (from `generate_aggregation`'s comments):** each `(anchor_id, relation)` pair must be UNIQUE per question, or two anchors merge into one store node and union their gold sets — set-F1 precision halves and the exactness gate fails. The real generator must guarantee one question per `(anchor_id, relation)`.

## File Structure

- Create `erkgbench/qa_e2e/realworld.py` — real-data entity loader + `generate_realworld_aggregation` + `run_realworld_aggregation`. One responsibility: turn a Wikidata fixture into the harness's `_Entity`/`Document`/`AggQuestion` types and drive the existing scorer.
- Create `erkgbench/qa_e2e/fixtures/wikidata_companies_v1.json` — the committed SPARQL pull (the reproducible dataset). Pinned; regenerated only by the script below.
- Create `scripts/pull_wikidata_capability_fixture.py` — one-off SPARQL puller that WRITES the fixture. Documented, not imported by the bench, not run in CI.
- Create `erkgbench/qa_e2e/fixtures/wikidata_companies_TINY.json` — a 4-entity hand-authored fixture for fast unit tests (no network, deterministic).
- Modify `erkgbench/qa_e2e/run_aggregation.py` — add `--source {synthetic,realworld}` and `--fixture PATH`.
- Create `tests/test_realworld_aggregation.py` — unit tests over the TINY fixture.

**Fixture JSON schema (v1):**
```json
{
  "meta": {"source": "wikidata", "pulled": "2026-07-23", "sparql_sha": "<hash of the query>", "domain": "companies"},
  "entities": [
    {"qid": "Q95", "canonical": "Google LLC", "aliases": ["Google", "Google Inc.", "GOOGL"]}
  ],
  "facts": [
    {"anchor_qid": "Q20800404", "relation": "has_subsidiary", "member_qids": ["Q95", "Q9366", "..."]}
  ]
}
```
- `entities[].qid` is the ground-truth id (maps to `_Entity.id`); `canonical` -> `_Entity.canonical`; `aliases` -> `_Entity.variants` (real name variation = the cross-doc ER challenge).
- `facts[]` is pre-aggregated: one row per `(anchor, relation)` with the full real member set (guarantees the uniqueness invariant and gives the gold set directly).
- `relation` uses the harness's underscore convention (`has_subsidiary`); rendered as "has subsidiary" in text, matching `generate_aggregation`.

---

## Phase 0 — Companies, aggregation only (the MVP: working, testable software on its own)

### Task 1: TINY test fixture + fixture loader

**Files:**
- Create: `packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/qa_e2e/fixtures/wikidata_companies_TINY.json`
- Create: `packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/qa_e2e/realworld.py`
- Test: `packages/python/goldenmatch/benchmarks/er-kg-bench/tests/test_realworld_aggregation.py`

- [ ] **Step 1: Write the TINY fixture** — 4 entities, 1 anchor with a 3-member subsidiary set, each entity with >=1 real-looking alias:

```json
{
  "meta": {"source": "wikidata", "pulled": "test", "sparql_sha": "test", "domain": "companies"},
  "entities": [
    {"qid": "Q1", "canonical": "Acme Holdings", "aliases": ["Acme Holdings Inc.", "Acme"]},
    {"qid": "Q2", "canonical": "Beta Corp", "aliases": ["Beta Corporation", "BETA"]},
    {"qid": "Q3", "canonical": "Gamma Ltd", "aliases": ["Gamma Limited"]},
    {"qid": "Q4", "canonical": "Delta LLC", "aliases": ["Delta"]}
  ],
  "facts": [
    {"anchor_qid": "Q1", "relation": "has_subsidiary", "member_qids": ["Q2", "Q3", "Q4"]}
  ]
}
```

- [ ] **Step 2: Write the failing test for the loader**

```python
from pathlib import Path
from erkgbench.qa_e2e.realworld import load_realworld_entities, _FIXTURE_DIR

def test_load_realworld_entities_maps_qid_canonical_aliases():
    ents = load_realworld_entities(_FIXTURE_DIR / "wikidata_companies_TINY.json")
    by_id = {e.id: e for e in ents}
    assert set(by_id) == {"Q1", "Q2", "Q3", "Q4"}
    assert by_id["Q1"].canonical == "Acme Holdings"
    assert "Acme" in by_id["Q1"].variants          # aliases -> variants
    assert by_id["Q1"].canonical not in by_id["Q1"].variants  # canonical excluded
```

- [ ] **Step 3: Run to verify it fails**

Run: `.venv/bin/python -m pytest packages/python/goldenmatch/benchmarks/er-kg-bench/tests/test_realworld_aggregation.py::test_load_realworld_entities_maps_qid_canonical_aliases -v`
Expected: FAIL (module `realworld` not found).
(Local Windows interpreter: `/d/show_case/goldenmatch/.venv/Scripts/python.exe`. CI uses the goldengraph-pipeline lane's venv.)

- [ ] **Step 4: Implement `load_realworld_entities`** in `realworld.py`

```python
import json
from pathlib import Path
from .engineered import _Entity

_FIXTURE_DIR = Path(__file__).parent / "fixtures"

def load_realworld_entities(fixture_path) -> list[_Entity]:
    """Load the committed Wikidata fixture into the harness's `_Entity` type.
    qid -> id (ground truth), canonical -> canonical, aliases -> variants
    (real name variation; canonical is never duplicated into variants)."""
    data = json.loads(Path(fixture_path).read_text(encoding="utf-8"))
    out = []
    for e in data["entities"]:
        variants = tuple(a for a in e.get("aliases", ()) if a != e["canonical"])
        out.append(_Entity(id=e["qid"], canonical=e["canonical"], variants=variants))
    return out
```

- [ ] **Step 5: Run to verify it passes** — same command, Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/qa_e2e/fixtures/wikidata_companies_TINY.json \
        packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/qa_e2e/realworld.py \
        packages/python/goldenmatch/benchmarks/er-kg-bench/tests/test_realworld_aggregation.py
git commit -m "feat(er-kg-bench): real-world capability fixture schema + entity loader"
```

### Task 2: `generate_realworld_aggregation` (real docs + gold, mirrors `generate_aggregation`)

**Files:**
- Modify: `erkgbench/qa_e2e/realworld.py`
- Test: `erkgbench/qa_e2e/../tests/test_realworld_aggregation.py`

- [ ] **Step 1: Write the failing test** — renders one Document per member edge, real aliases injected, gold set matches the fixture, and the `(anchor, relation)` uniqueness invariant holds:

```python
import random
from erkgbench.qa_e2e.realworld import generate_realworld_aggregation, _FIXTURE_DIR

def test_generate_realworld_aggregation_shapes_and_gold():
    docs, qs = generate_realworld_aggregation(
        _FIXTURE_DIR / "wikidata_companies_TINY.json", ambiguity=1.0, seed=7)
    # one doc per (anchor, member) edge = 3
    assert len(docs) == 3
    # each doc text mentions the relation words and ends with a period
    assert all("has subsidiary" in d.text and d.text.endswith(".") for d in docs)
    # list + count question for the single anchor
    lists = [q for q in qs if q.kind == "list"]
    assert len(lists) == 1
    q = lists[0]
    assert q.anchor_id == "Q1" and q.relation == "has_subsidiary"
    assert set(q.gold_members) == {"Q2", "Q3", "Q4"} and q.gold_count == 3
    # uniqueness invariant: no duplicate (anchor_id, relation) across list questions
    keys = [(q.anchor_id, q.relation) for q in lists]
    assert len(keys) == len(set(keys))
    # ambiguity=1.0 -> at least one mention uses a non-canonical alias somewhere
    all_text = " ".join(d.text for d in docs)
    assert "Acme" in all_text or "BETA" in all_text or "Beta Corporation" in all_text
```

- [ ] **Step 2: Run to verify it fails** — Expected: FAIL (`generate_realworld_aggregation` undefined)

- [ ] **Step 3: Implement `generate_realworld_aggregation`** — mirror `generate_aggregation`'s rendering exactly (reuse `_render_mention`, `Document`, `AggQuestion`, `_edge_doc_id`):

```python
import random
from .corpora import Document
from .engineered import _render_mention, _edge_doc_id
from .aggregation import AggQuestion

def generate_realworld_aggregation(fixture_path, *, ambiguity: float, seed: int):
    """Real-data drop-in for `generate_aggregation`: one Document per (anchor, member)
    edge with real aliases sampled by `ambiguity`, plus a list+count AggQuestion per
    fact. `facts` are pre-aggregated (one row per (anchor, relation)), so the
    (anchor_id, relation) uniqueness invariant holds by construction."""
    import json
    from pathlib import Path
    data = json.loads(Path(fixture_path).read_text(encoding="utf-8"))
    ents = load_realworld_entities(fixture_path)
    by_id = {e.id: e for e in ents}
    rng = random.Random(seed)
    docs, qs = [], []
    for i, fact in enumerate(data["facts"]):
        src_id, rel = fact["anchor_qid"], fact["relation"]
        members = [m for m in fact["member_qids"] if m in by_id]
        rel_words = rel.replace("_", " ")
        for m in members:
            s = _render_mention(by_id[src_id], rng, ambiguity)
            o = _render_mention(by_id[m], rng, ambiguity)
            docs.append(Document(id=_edge_doc_id(src_id, rel, m),
                                 text=f"{s} {rel_words} {o}.",
                                 src_surface=s, dst_surface=o))
        canon = by_id[src_id].canonical
        qs.append(AggQuestion(id=f"rw-list-{i}", kind="list",
                              question=f"List all entities that {canon} {rel_words}.",
                              anchor_id=src_id, relation=rel,
                              gold_members=tuple(members), gold_count=len(members)))
        qs.append(AggQuestion(id=f"rw-count-{i}", kind="count",
                              question=f"How many entities does {canon} {rel_words}?",
                              anchor_id=src_id, relation=rel,
                              gold_members=tuple(members), gold_count=len(members)))
    return tuple(docs), qs
```

- [ ] **Step 4: Run to verify it passes** — Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(er-kg-bench): generate_realworld_aggregation (real docs + gold)"
```

### Task 3: `run_realworld_aggregation` (drive the existing scorer over real data)

**Files:**
- Modify: `erkgbench/qa_e2e/realworld.py`
- Test: `tests/test_realworld_aggregation.py`

- [ ] **Step 1: Write the failing test** — the runner returns an `AggregationResult` whose gg set-F1 >= the floor set-F1 on the TINY fixture (native-wheel-gated; skip if the wheel is absent):

```python
import pytest
from erkgbench.qa_e2e.realworld import run_realworld_aggregation, _FIXTURE_DIR

def test_run_realworld_aggregation_gg_beats_floor():
    try:
        import goldengraph_native  # noqa: F401
    except ImportError:
        pytest.skip("goldengraph-native wheel not installed")
    res = run_realworld_aggregation(
        _FIXTURE_DIR / "wikidata_companies_TINY.json",
        ambiguity=1.0, passage_k=2)
    # on the 3-member set, exact traversal should match all; the k=2 window can't
    gg = list(res.gg_setf1.values())
    assert gg and min(gg) >= 0.99            # exact traversal recovers the full set
```

- [ ] **Step 2: Run to verify it fails** — Expected: FAIL (`run_realworld_aggregation` undefined)

- [ ] **Step 3: Implement `run_realworld_aggregation`** — copy the body of `run_aggregation_deterministic` (aggregation.py:257), swapping only the generator call. Keep everything else (store build, floor, buckets, gate) identical:

```python
def run_realworld_aggregation(fixture_path, *, ambiguity: float, passage_k: int, llm=None):
    """Mirror of aggregation.run_aggregation_deterministic but sourced from the real
    fixture. All scoring/floor/bucket/gate logic is reused unchanged."""
    from . import ablation, dials
    from .gold import GoldGraph
    from .aggregation import (agg_documents_corpus, size_bucket, set_f1, count_accuracy,
                              passage_window_floor, goldengraph_aggregate,
                              _mean_by_bucket, AggregationResult, llm_rag_aggregate)
    docs, qs = generate_realworld_aggregation(fixture_path, ambiguity=ambiguity, seed=7)
    corpus = agg_documents_corpus(docs)
    g = GoldGraph.from_corpus(corpus)
    slice_graph, coverage = ablation._build_store(
        corpus, g, dials.oracle_keys(corpus, g), ablation._typ_of(g))
    s2c, anchor_surfaces = {}, {}
    for eid, surf, _typ in dials._entity_surfaces(g):
        s2c.setdefault(surf, eid)
        anchor_surfaces.setdefault(eid, set()).add(surf)
    gg_f1, floor_f1, floor_rec, gg_count, llm_f1 = [], [], [], [], []
    for q in (q for q in qs if q.kind == "list"):
        b = size_bucket(q.gold_count); gold = set(q.gold_members)
        a_surfs = anchor_surfaces.get(q.anchor_id, set())
        got = goldengraph_aggregate(slice_graph, coverage, q.anchor_id, q.relation)
        floor = passage_window_floor(docs, a_surfs, q.relation, passage_k=passage_k,
                                     surface_to_canon=s2c)
        gg_f1.append((b, set_f1(got, gold)["f1"]))
        fscore = set_f1(floor, gold)
        floor_f1.append((b, fscore["f1"])); floor_rec.append((b, fscore["recall"]))
        gg_count.append((b, count_accuracy(len(got), q.gold_count)))
        if llm is not None and not getattr(llm, "exhausted", False):
            rag = llm_rag_aggregate(docs, a_surfs, q.relation, passage_k=passage_k,
                                    surface_to_canon=s2c, llm=llm)
            llm_f1.append((b, set_f1(rag, gold)["f1"]))
    return AggregationResult(
        gg_setf1=_mean_by_bucket(gg_f1), floor_setf1=_mean_by_bucket(floor_f1),
        gg_count_acc=_mean_by_bucket(gg_count), floor_recall=_mean_by_bucket(floor_rec),
        llm_setf1=_mean_by_bucket(llm_f1) if llm_f1 else None)
```

Note: verify `AggregationResult`'s exact constructor fields against aggregation.py before finalizing (the tail was truncated when this plan was written — match the real dataclass).

- [ ] **Step 4: Run to verify it passes (or skips cleanly without the wheel)** — Expected: PASS or SKIP

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(er-kg-bench): run_realworld_aggregation over the Wikidata fixture"
```

### Task 4: CLI switch `--source realworld`

**Files:**
- Modify: `erkgbench/qa_e2e/run_aggregation.py`
- Test: `tests/test_realworld_aggregation.py`

- [ ] **Step 1: Read `run_aggregation.py`** to see its current argparse + which function it calls (`run_aggregation_deterministic`). Match its output format (Markdown/JSON writer) so realworld results render identically.

- [ ] **Step 2: Write the failing test** — invoking the CLI module with `--source realworld --fixture <TINY>` returns rc 0 and writes a results table mentioning the buckets. (Use `subprocess` or import `main` and pass argv.)

- [ ] **Step 3: Add `--source {synthetic,realworld}` (default `synthetic`) and `--fixture PATH`.** When `realworld`, call `run_realworld_aggregation(args.fixture, ambiguity=args.ambiguity, passage_k=args.passage_k, llm=...)`; else the existing path. Do NOT change the default behavior (synthetic stays byte-identical).

- [ ] **Step 4: Run to verify it passes** — Expected: PASS

- [ ] **Step 5: Commit** — `git commit -m "feat(er-kg-bench): --source realworld for the aggregation CLI"`

### Task 5: The real fixture puller (produces `wikidata_companies_v1.json`)

**Files:**
- Create: `packages/python/goldenmatch/benchmarks/er-kg-bench/scripts/pull_wikidata_capability_fixture.py`
- Create (output, committed): `erkgbench/qa_e2e/fixtures/wikidata_companies_v1.json`

- [ ] **Step 1: Write the puller** — a standalone script (argparse `--out`, `--limit`, `--min-set-size`) that runs two SPARQL queries against `https://query.wikidata.org/sparql` (User-Agent set, JSON format), aggregates member sets per anchor, keeps anchors with `>= min_set_size` members (default 2) across the target buckets, and writes the v1 fixture schema. Subsidiary query:

```sparql
SELECT ?company ?companyLabel ?alias ?sub WHERE {
  ?company wdt:P31/wdt:P279* wd:Q4830453 .   # instance of business
  ?company wdt:P355 ?sub .                     # has subsidiary
  OPTIONAL { ?company skos:altLabel ?alias FILTER(LANG(?alias)="en") }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en" }
}
LIMIT 20000
```

Fetch member/aliases for the sub QIDs in a second pass. Deterministic ordering (sort by qid) so the committed fixture is stable. Print a size-bucket histogram so the operator sees the fan-out distribution.

- [ ] **Step 2: Run the puller once, locally** (not in CI):

Run: `.venv/bin/python packages/.../scripts/pull_wikidata_capability_fixture.py --out packages/.../fixtures/wikidata_companies_v1.json --min-set-size 2`
Expected: writes the fixture; prints a histogram with entries in the 11-20 and 21+ buckets (the buckets where the floor collapses — the point of the bench).

- [ ] **Step 3: Sanity-check the fixture** — a quick test that `load_realworld_entities` + `generate_realworld_aggregation` run over `wikidata_companies_v1.json` without error and produce >= one 11-20-bucket question.

- [ ] **Step 4: Commit the script AND the fixture** (the fixture is the reproducible dataset):

```bash
git add packages/.../scripts/pull_wikidata_capability_fixture.py packages/.../fixtures/wikidata_companies_v1.json
git commit -m "feat(er-kg-bench): Wikidata company fixture + reproducible puller (v1)"
```

### Task 6: Local end-to-end verification (the headline result)

- [ ] **Step 1: Run the real aggregation bench** over `wikidata_companies_v1.json` (needs the native wheel; run in the goldengraph venv):

Run: `.venv/bin/python -m erkgbench.qa_e2e.run_aggregation --source realworld --fixture .../wikidata_companies_v1.json --ambiguity 1.0 --passage-k 10`

- [ ] **Step 2: Confirm the WIN shape** — GoldenGraph set-F1 stays flat across size buckets while the passage-window floor's recall collapses in the 11-20 / 21+ buckets. This flat-vs-collapse curve on REAL company data is the deliverable. Record it in `results/` (mirror the synthetic result doc) and note it in `packages/python/goldengraph/CLAUDE.md`.

---

## Phase 1 — Temporal as-of (follow-on; same drop-in pattern)

Mirror Phase 0 against `temporal.py`: extend the fixture schema with `temporal_facts` (`anchor_qid`, `relation` e.g. `chief_executive_officer`, list of `{object_qid, start, end}` intervals from `P580`/`P582`), add `generate_realworld_temporal` producing `TemporalFact`/`TemporalQuestion` (past-regime and current-regime questions per correction), and `run_realworld_temporal` mirroring `run_temporal_deterministic`. The puller gains a CEO/position query:

```sparql
SELECT ?company ?ceo ?ceoLabel ?start ?end WHERE {
  ?company wdt:P31/wdt:P279* wd:Q4830453 .
  ?company p:P169 ?st . ?st ps:P169 ?ceo .
  OPTIONAL { ?st pq:P580 ?start } OPTIONAL { ?st pq:P582 ?end }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en" }
}
```
Keep only anchors with >= 2 dated CEO intervals (a real correction to test as-of). The win: `store.as_of(year)` correct on past-date questions; the temporal-blind floor returns the most-recent/most-mentioned -> wrong.

## Phase 2 — CI workflow + real-LLM RAG arm + second domain (follow-on)

- Add a `bench-capability-realworld` workflow (or a `mode` in `bench-graphrag-qa.yml`) that runs `--source realworld` on `large-new-64GB`, uploads the results artifact. Doc-only/synthetic paths unaffected.
- Add the optional real-LLM RAG arm (`llm=` already threaded through `run_*`) so the board shows GoldenGraph vs a REAL RAG, not just the deterministic floor.
- Add a second domain fixture (`wikidata_academic_v1.json`: `P50` authorship / `P108` affiliation) to show the win generalizes beyond companies.

---

## Risks & design cautions (READ before implementing)

- **Wikidata is gold-BY-CONSTRUCTION, not real-world truth.** The task is text->structure->answer (the floor/RAG get the SAME rendered docs); we measure recovery of what's IN the fixture. State this in the results doc. Restrict to well-populated anchors to limit missing-member false negatives.
- **Reproducibility:** the bench NEVER hits live Wikidata — it reads the committed fixture. Only `pull_wikidata_capability_fixture.py` touches the network, run by hand. Pin `meta.pulled` + `meta.sparql_sha`.
- **Preserve the `(anchor, relation)` uniqueness invariant** (Task 2) or set-F1 precision silently halves.
- **Native-wheel gating:** `run_realworld_aggregation` needs `goldengraph_native` (via `ablation._build_store`); unit tests must `pytest.skip` when it's absent (matches how the goldengraph tests already gate). Loader/generator tests stay wheel-free.
- **Licensing:** Wikidata is CC0 — the fixture is redistributable.
- **Do NOT change the synthetic default** — `--source synthetic` must stay byte-identical; realworld is purely additive.

## How to run / verify (summary)

- Wheel-free unit tests (loader + generator): `pytest tests/test_realworld_aggregation.py -k "not gg_beats_floor"`.
- Full local bench (needs wheel): `run_aggregation --source realworld --fixture .../wikidata_companies_v1.json`.
- Success criterion: on real company data, GoldenGraph set-F1 is flat across size buckets while the passage-window floor recall collapses in the large-set buckets.
