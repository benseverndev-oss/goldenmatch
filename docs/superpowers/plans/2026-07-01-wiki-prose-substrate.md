# Level 2: Real Wikipedia-Prose Substrate Validation — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Validate the `name_ci` substrate fix on **real Wikipedia prose**: a committed seed+1-hop article corpus with wikilink→QID gold, scored by a new surface+doc alignment (the engineered doc-id oracle is gone), guarded by an engineered-reproduction sanity check.

**Architecture:** Offline fetch → committed `wiki_corpus.jsonl` snapshot → the substrate eval builds a graph from real prose and aligns gold to nodes by surface+doc. Pure logic (parser, alignment, loader) is box-tested; the fetch runs once offline; one Modal run produces the calibration.

**Tech Stack:** Python (er-kg-bench), stdlib `urllib` for fetch, pytest (box-safe pure), Modal for the run.

**Spec:** `docs/superpowers/specs/2026-07-01-wiki-prose-substrate-design.md`
**Branch:** `feat/wiki-prose-substrate` (already off main).

**Box-safe test invocation:**
```bash
PY="/d/show_case/goldenmatch/.venv/Scripts/python.exe"
cd packages/python/goldenmatch/benchmarks/er-kg-bench
PYTHONPATH="." POLARS_SKIP_CPU_CHECK=1 GOLDENGRAPH_NATIVE=0 "$PY" -m pytest <test> -q -p no:cacheprovider
```

---

## Task 1: `parse_wikilinks` (pure wikilink parser)

**Files:**
- Create: `packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/qa_e2e/wiki_corpus.py`
- Test: `packages/python/goldenmatch/benchmarks/er-kg-bench/tests/test_wiki_corpus.py`

- [ ] **Step 1: Write the failing test:**

```python
from erkgbench.qa_e2e.wiki_corpus import parse_wikilinks


def test_parse_wikilinks_piped_and_bare():
    wt = "[[IBM]] acquired [[Red Hat|Red Hat, Inc.]] in 2019."
    assert parse_wikilinks(wt) == [("IBM", "IBM"), ("Red Hat, Inc.", "Red Hat")]


def test_parse_wikilinks_skips_namespaced_and_strips_section():
    wt = "[[File:logo.png|thumb]] see [[Apple Inc.#History|Apple]] and [[Category:Tech]]"
    assert parse_wikilinks(wt) == [("Apple", "Apple Inc.")]  # File:/Category: skipped, #section stripped
```

- [ ] **Step 2: Run, verify fail.**

- [ ] **Step 3: Implement in `wiki_corpus.py`:**

```python
"""Real-Wikipedia-prose substrate corpus: wikilink parser + committed-snapshot loader."""
from __future__ import annotations

import json
import re
from pathlib import Path

#: [[Target]] or [[Target|Surface]]; group1=target, group2=optional surface. No nested brackets/pipes.
_WIKILINK = re.compile(r"\[\[([^\[\]|]+)(?:\|([^\[\]|]+))?\]\]")


def parse_wikilinks(wikitext: str) -> list[tuple[str, str]]:
    """(surface, target_title) per article-namespace wikilink. Skips File:/Category:/interwiki (`:` in
    target) and section-only links; strips a `#section` anchor from the target."""
    out: list[tuple[str, str]] = []
    for m in _WIKILINK.finditer(wikitext):
        target = m.group(1).strip()
        surface = (m.group(2) or m.group(1)).strip()
        if not target or target.startswith("#") or ":" in target:
            continue
        target = target.split("#", 1)[0].strip()
        if target and surface:
            out.append((surface, target))
    return out
```

- [ ] **Step 4: Run, verify pass** + ruff.
- [ ] **Step 5: Commit** — `feat(er-kg-bench): wikilink parser for the real-prose corpus`.

---

## Task 2: `align_real_mentions_to_nodes` + coverage (the new alignment)

**Files:**
- Modify: `packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/substrate_eval.py`
- Test: `packages/python/goldenmatch/benchmarks/er-kg-bench/tests/test_substrate_eval.py` (extend)

- [ ] **Step 1: Write the failing tests:**

```python
from erkgbench.substrate_eval import (
    align_real_mentions_to_nodes, real_alignment_coverage, align_mentions_to_nodes,
)


def _rent(eid, *surfaces):  # a graph entity with surface_names
    return {"entity_id": eid, "canonical_name": surfaces[0], "typ": "thing", "surface_names": list(surfaces)}


def test_align_real_surface_and_doc_match():
    # doc d1 has nodes 5 (IBM) and 1 (Red Hat); gold IBM->QID_ibm, Red Hat->QID_rh
    graph = {"entities": [_rent(5, "IBM"), _rent(1, "Red Hat")],
             "edges": [{"subj": 5, "obj": 1, "predicate": "acquired", "source_refs": ["d1"]}]}
    gm = [("Q_ibm", "IBM", "d1"), ("Q_rh", "Red Hat", "d1")]
    assert sorted(map(sorted, align_real_mentions_to_nodes(graph, gm))) == [[0], [1]]
    assert real_alignment_coverage(graph, gm) == 1.0


def test_align_real_exact_beats_substring_and_orphan_unique():
    # "Apple" exact node 7 beats substring node 8 "Apple Inc"; "Ghost" has no match -> unique orphan
    graph = {"entities": [_rent(7, "Apple"), _rent(8, "Apple Inc")],
             "edges": [{"subj": 7, "obj": 8, "predicate": "r", "source_refs": ["d1"]}]}
    gm = [("Qa", "Apple", "d1"), ("Qx", "Ghost", "d1"), ("Qy", "Nowhere", "d1")]
    clusters = sorted(map(sorted, align_real_mentions_to_nodes(graph, gm)))
    assert [0] in clusters                          # Apple -> node 7 (exact), its own cluster
    assert sum(len(c) for c in clusters) == 3       # 2 orphans stay SEPARATE (unique negatives)
    assert real_alignment_coverage(graph, gm) == 1 / 3


def test_align_real_reproduces_engineered_oracle():
    # SANITY GUARD: on an engineered-shaped graph (1 edge/doc, distinct surfaces), the surface aligner
    # must match the doc-id oracle's clustering. Entity A appears in two docs -> merged node 0.
    graph = {"entities": [_rent(0, "A"), _rent(1, "B"), _rent(2, "C")],
             "edges": [{"subj": 0, "obj": 1, "predicate": "r", "source_refs": ["A::r::B"]},
                       {"subj": 0, "obj": 2, "predicate": "r2", "source_refs": ["A::r2::C"]}]}
    gm = [("A", "A", "A::r::B"), ("B", "B", "A::r::B"), ("A", "A", "A::r2::C"), ("C", "C", "A::r2::C")]
    assert (sorted(map(sorted, align_real_mentions_to_nodes(graph, gm)))
            == sorted(map(sorted, align_mentions_to_nodes(graph, gm))))  # both: [[0,2],[1],[3]]
```

- [ ] **Step 2: Run, verify fail.**

- [ ] **Step 3: Implement in `substrate_eval.py`** (after `_assign_nodes`):

```python
def _assign_real_nodes(graph: dict, gold_mentions: list[tuple[str, str, str]]) -> dict[int, int]:
    """Per gold-mention index -> built node, by SURFACE+DOC match (no engineered doc-id oracle). Candidates
    = nodes touched by an edge sourced from the mention's doc; pick exact surface match (case-folded) over
    substring, tie-broken by LOWEST node id (deterministic); no match -> a UNIQUE decrementing negative
    (orphan singleton, like `_assign_nodes`). Precision rides on exact-before-substring on real articles
    (large candidate sets)."""
    id2surf: dict[int, set[str]] = {}
    for e in graph.get("entities", ()):
        nid = e.get("entity_id")
        surfs = {str(s).strip().lower() for s in e.get("surface_names", ()) if s}
        cn = str(e.get("canonical_name", "")).strip().lower()
        if cn:
            surfs.add(cn)
        id2surf[nid] = surfs
    by_doc: dict[str, set[int]] = {}
    for e in graph.get("edges", ()):
        for ref in e.get("source_refs", ()):
            by_doc.setdefault(_base_doc_id(ref), set()).update((e.get("subj"), e.get("obj")))
    node_of: dict[int, int] = {}
    fresh = -1
    for i, (_eid, surface, doc) in enumerate(gold_mentions):
        s = str(surface).strip().lower()
        cands = by_doc.get(_base_doc_id(doc), set())
        exact = sorted(n for n in cands if s in id2surf.get(n, ()))
        if exact:
            node_of[i] = exact[0]
            continue
        substr = sorted(n for n in cands if any(s and (s in sn or sn in s) for sn in id2surf.get(n, ())))
        if substr:
            node_of[i] = substr[0]
            continue
        node_of[i] = fresh
        fresh -= 1
    return node_of


def align_real_mentions_to_nodes(graph: dict, gold_mentions: list[tuple[str, str, str]]) -> list[list[int]]:
    """Cluster gold-mention indices by built node via surface+doc match -- the real-prose counterpart to
    `align_mentions_to_nodes` (which needs the engineered `src::rel::dst` doc-id). Same output shape."""
    groups: dict[int, list[int]] = {}
    for i, node in _assign_real_nodes(graph, gold_mentions).items():
        groups.setdefault(node, []).append(i)
    return [sorted(v) for v in groups.values()]


def real_alignment_coverage(graph: dict, gold_mentions: list[tuple[str, str, str]]) -> float:
    """Fraction of gold mentions assigned to a real (non-orphan) built node. A low value means the ER score
    is measuring alignment failure, not resolution -- report it alongside R(B)."""
    node_of = _assign_real_nodes(graph, gold_mentions)
    if not node_of:
        return 1.0
    return sum(1 for n in node_of.values() if n >= 0) / len(node_of)
```

- [ ] **Step 4: Run tests (incl. the engineered-reproduction guard), verify pass** + ruff.
- [ ] **Step 5: Commit** — `feat(er-kg-bench): real-prose surface+doc alignment + coverage (align_real)`.

---

## Task 3: fetch script + seeds + committed snapshot + loader

**Files:**
- Create: `packages/python/goldenmatch/benchmarks/er-kg-bench/dataset/wiki_seeds.jsonl`
- Create: `packages/python/goldenmatch/benchmarks/er-kg-bench/dataset/build_wiki_corpus.py`
- Create (generated, committed): `packages/python/goldenmatch/benchmarks/er-kg-bench/dataset/wiki_corpus.jsonl`
- Extend: `erkgbench/qa_e2e/wiki_corpus.py` (`load_wiki_corpus`)
- Test: extend `tests/test_wiki_corpus.py`

- [ ] **Step 1: Seed file** — `wiki_seeds.jsonl`, ~6-10 interconnected QIDs (an acquisition/tech cluster co-references well), e.g. `{"qid": "Q37156", "title": "IBM"}` (IBM, Microsoft, Red Hat, GitHub, Google, Apple Inc., Oracle, ...). One `{"qid","title"}` per line.

- [ ] **Step 2: `build_wiki_corpus.py`** (stdlib urllib; run offline). Behavior:
  - For each seed: fetch the enwiki **lead-section** wikitext (`api.php?action=query&prop=revisions&rvprop=content|ids&rvslots=main&rvsection=0&format=json&titles=<title>`), record `revid`.
  - 1-hop expand: `parse_wikilinks` the seed leads → collect target titles → fetch those articles' leads too (cap total at ~30 articles). The closed set = fetched titles.
  - Resolve every wikilink target title → QID: batch `wikidata.org/w/api.php?action=wbgetentities&sites=enwiki&titles=<t1|t2|...>&props=info&format=json` → `entities[*].id`. Cache. Drop unresolved.
  - **Plain text:** from the lead wikitext, replace each `[[T|S]]`→`S` / `[[T]]`→`T`, strip `{{templates}}`, `<ref>...</ref>`, `'''`/`''`, and `<!--comments-->` (best-effort regex). This is the doc text the LLM extracts from; gold surfaces appear verbatim.
  - **Gold:** for each article, keep wikilinks whose resolved `Target_QID` is in the closed set → `[Target_QID, Surface]`. `doc_id = article's own QID`.
  - Write `wiki_corpus.jsonl`: `{"doc_id": <qid>, "title": <t>, "revid": <n>, "text": <plain>, "gold": [[qid, surface], ...]}` per article.
  - Politeness: `User-Agent` header, ~0.2s sleep between calls.
  - **Run it offline now** to produce the committed `wiki_corpus.jsonl`. (Network; run locally, commit the output.)

- [ ] **Step 3: `load_wiki_corpus()` in `wiki_corpus.py`** (pure):
```python
def load_wiki_corpus(path: str | Path | None = None):
    """Read the committed wiki_corpus.jsonl -> (documents, gold_mentions). documents = list of objects with
    .id/.text/.src_surface/.dst_surface (reuse corpora.Document; surfaces unused); gold_mentions =
    (Target_QID, Surface, doc_id) flattened across articles. Pure / no network."""
    from .corpora import Document
    p = Path(path) if path else Path(__file__).resolve().parents[2] / "dataset" / "wiki_corpus.jsonl"
    docs, gold = [], []
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        docs.append(Document(id=rec["doc_id"], text=rec["text"]))
        for qid, surface in rec.get("gold", []):
            gold.append((qid, surface, rec["doc_id"]))
    return docs, gold
```

- [ ] **Step 4: Test** `load_wiki_corpus` against a tiny committed fixture (or a 2-line temp file): asserts docs have ids/text and gold is `(qid, surface, doc_id)` tuples. Box-safe.

- [ ] **Step 5: Run tests + ruff. Commit** — `feat(er-kg-bench): wiki corpus fetch script + seeds + snapshot + loader` (include the generated `wiki_corpus.jsonl`).

---

## Task 4: `--corpus wiki` eval path

**Files:**
- Modify: `packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/run_substrate_eval.py`
- Modify: `scripts/distill/modal_bench.py` (substrate branch: pass a corpus flag through)

- [ ] **Step 1:** In `run_substrate_eval.py`, add a `--corpus {engineered,wiki}` arg. For `wiki`: `documents, gold = load_wiki_corpus()`; `_build_graph` over `documents` (reuse the existing `ingest_corpus` build, `doc_ids=[d.id...]`); score via `align_real_mentions_to_nodes` + `real_alignment_coverage` instead of `align_mentions_to_nodes`. Emit an `[substrate-wiki]` line with R(B)/P(B)/coverage. No ambiguity sweep (real prose has its own variance) -- one build, baseline-vs-`name_ci` selected by `GOLDENGRAPH_XDOC_KEY` as usual.
- [ ] **Step 2:** Refactor `score_substrate` (or add `score_substrate_real`) to accept an aligner + report coverage, so Level-B uses `align_real_mentions_to_nodes` for wiki. Keep engineered path unchanged.
- [ ] **Step 3:** `modal_bench.py` substrate branch: honor `GOLDENGRAPH_SUBSTRATE_CORPUS` (env via `--opts`) -> pass `--corpus`.
- [ ] **Step 4:** Box-safe test: `run_substrate_eval` wiki path is import-clean (`ruff` + `py_compile`); the scoring reuses Task-2 units already tested.
- [ ] **Step 5: Commit** — `feat(er-kg-bench): --corpus wiki substrate eval path`.

---

## Task 5: Modal calibration run + verdict

**Files:** Create `docs/superpowers/reports/2026-07-01-wiki-prose-substrate-verdict.md`.

- [ ] **Step 1:** Fire two Modal legs (detached+spawn, `PYTHONIOENCODING=utf-8 PYTHONUTF8=1`, distinct `--n`):
```bash
M="/d/show_case/goldenmatch/.venv/Scripts/modal.exe"
$M run --detach scripts/distill/modal_bench.py --engine goldengraph --eval substrate --n 60 \
  --opts $'GOLDENGRAPH_SUBSTRATE_CORPUS=wiki' --spawn                                   # baseline
$M run --detach scripts/distill/modal_bench.py --engine goldengraph --eval substrate --n 61 \
  --opts $'GOLDENGRAPH_SUBSTRATE_CORPUS=wiki\nGOLDENGRAPH_XDOC_KEY=name_ci' --spawn      # name_ci
```
Monitor each `results/substrate_<n>_*.md`.

- [ ] **Step 2: Read** R(B)/P(B) baseline vs `name_ci` **and coverage**. Gate: coverage must be high enough (say ≥0.7) for the ER numbers to mean anything -- report it prominently; a low-coverage run is inconclusive, not a low score.

- [ ] **Step 3: Write the verdict** -- the wiki numbers beside level-0 (engineered) + level-1 (real-entity 0.976). The level-1→level-2 drop = the **real-prose extraction penalty**. Does `name_ci` still beat baseline on real sentences? Honest calibration, whatever it says.

- [ ] **Step 4: Commit** the report.

---

## Completion

Use superpowers:finishing-a-development-branch: verify box-safe tests, PR (base `main`), arm auto-merge. Deferred (unchanged): pronoun/coreference gold, larger/multi-domain corpora, real-prose homograph analysis.
