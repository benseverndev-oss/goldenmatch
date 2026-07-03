# L2 Clean Absolute via Alias-Anchored Alignment — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Fix L2's `coverage=0.40` by matching built nodes to gold QIDs via the QID's full Wikidata alias set, giving a clean real-prose absolute (coverage = true extraction recall; aligned R(B) = clean resolution).

**Architecture:** Extend the committed wiki snapshot with `wiki_aliases.json` (offline fetch); a new alias-anchored aligner; the `--corpus wiki` eval swaps to it.

**Spec:** `docs/superpowers/specs/2026-07-01-wiki-prose-aliased-design.md`
**Branch:** `feat/wiki-prose-aliased` (stacked on `feat/wiki-prose-substrate` / PR #1341; rebase onto main after it merges).

**Box-safe test invocation:**
```bash
PY="/d/show_case/goldenmatch/.venv/Scripts/python.exe"
cd packages/python/goldenmatch/benchmarks/er-kg-bench
PYTHONPATH="." POLARS_SKIP_CPU_CHECK=1 GOLDENGRAPH_NATIVE=0 "$PY" -m pytest <test> -q -p no:cacheprovider
```

---

## Task 1: alias fetch + committed `wiki_aliases.json`

**Files:** Modify `dataset/build_wiki_corpus.py`; create (generated) `dataset/wiki_aliases.json`.

- [ ] **Step 1: Add `fetch_aliases` + an `--aliases-only` mode** (regenerate aliases against the EXISTING committed corpus, so the corpus's `revid`s stay pinned):

```python
ALIASES_OUT = HERE / "wiki_aliases.json"


def fetch_aliases(qids: list[str]) -> dict[str, list[str]]:
    """{QID: sorted distinct en labels + altLabels} from Wikidata (batched <=50 ids/call)."""
    out: dict[str, list[str]] = {}
    for i in range(0, len(qids), 50):
        data = _get(_WD, {"action": "wbgetentities", "ids": "|".join(qids[i:i + 50]),
                          "props": "labels|aliases", "languages": "en"})
        for qid, ent in data.get("entities", {}).items():
            names = set()
            lab = ent.get("labels", {}).get("en", {}).get("value")
            if lab:
                names.add(lab)
            names.update(a["value"] for a in ent.get("aliases", {}).get("en", []) if a.get("value"))
            if names:
                out[qid] = sorted(names)
        time.sleep(0.5)
    return out
```
In `main()`, after `--max`, add `ap.add_argument("--aliases-only", action="store_true", ...)`. When set:
```python
    if args.aliases_only:
        recs = [json.loads(l) for l in OUT.read_text(encoding="utf-8").splitlines() if l.strip()]
        qids = sorted({qid for r in recs for qid, _s in r["gold"]})
        aliases = fetch_aliases(qids)
        ALIASES_OUT.write_text(json.dumps(aliases, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"[wiki] wrote {ALIASES_OUT.name}: {len(aliases)} entities", flush=True)
        return
```
Also emit aliases in the full-build path (after writing `OUT`): `ALIASES_OUT.write_text(json.dumps(fetch_aliases(sorted(set(title2qid[t] for t in closed))), ensure_ascii=False) + "\n", encoding="utf-8")`.

- [ ] **Step 2: Run `--aliases-only` OFFLINE** to produce `dataset/wiki_aliases.json` against the committed corpus:
```bash
PYTHONIOENCODING=utf-8 python dataset/build_wiki_corpus.py --aliases-only
```
Verify it wrote a `{QID: [aliases]}` map for the ~17 gold QIDs (e.g. Q37156 includes both `IBM` and `International Business Machines`).

- [ ] **Step 3: ruff + commit** — `feat(er-kg-bench): wiki QID->alias map (Wikidata) for alias-anchored L2` (include `wiki_aliases.json`).

---

## Task 2: `load_wiki_corpus` returns the alias map (3-tuple)

**Files:** Modify `erkgbench/qa_e2e/wiki_corpus.py`; update `tests/test_wiki_corpus.py`.

- [ ] **Step 1: Update the loader test** (it currently unpacks a 2-tuple → will break):

```python
def test_load_wiki_corpus_flattens_gold(tmp_path):
    snap = tmp_path / "wiki_corpus.jsonl"
    snap.write_text(... same two records ..., encoding="utf-8")
    docs, gold, qid_aliases = load_wiki_corpus(snap)         # 3-tuple now
    assert {d.id for d in docs} == {"Q37156", "Q_rh"}
    assert all(len(g) == 3 for g in gold)
    assert qid_aliases == {}                                  # no sibling wiki_aliases.json -> empty


def test_load_wiki_corpus_reads_sibling_aliases(tmp_path):
    (tmp_path / "wiki_corpus.jsonl").write_text(
        json.dumps({"doc_id": "Q37156", "title": "IBM", "revid": 1, "text": "IBM.",
                    "gold": [["Q37156", "IBM"]]}) + "\n", encoding="utf-8")
    (tmp_path / "wiki_aliases.json").write_text(
        json.dumps({"Q37156": ["IBM", "International Business Machines", "Big Blue"]}), encoding="utf-8")
    _docs, _gold, qid_aliases = load_wiki_corpus(tmp_path / "wiki_corpus.jsonl")
    assert qid_aliases["Q37156"] == {"ibm", "international business machines", "big blue"}  # lowercased set
```

- [ ] **Step 2: Run, verify fail.**

- [ ] **Step 3: Implement** — change `load_wiki_corpus`:
```python
    p = Path(path) if path else Path(__file__).resolve().parents[2] / "dataset" / "wiki_corpus.jsonl"
    apath = p.parent / "wiki_aliases.json"
    qid_aliases: dict[str, set[str]] = {}
    if apath.exists():
        qid_aliases = {q: {a.lower() for a in al}
                       for q, al in json.loads(apath.read_text(encoding="utf-8")).items()}
    docs, gold = [], []
    ...  # unchanged
    return docs, gold, qid_aliases
```

- [ ] **Step 4: Run tests, verify pass** + ruff. **Commit** — `feat(er-kg-bench): load_wiki_corpus returns QID->alias map (3-tuple)`.

---

## Task 3: `align_real_mentions_to_nodes_aliased` + coverage

**Files:** Modify `erkgbench/substrate_eval.py`; extend `tests/test_substrate_eval.py`.

- [ ] **Step 1: Write the failing tests:**

```python
from erkgbench.substrate_eval import (
    align_real_mentions_to_nodes_aliased, real_alignment_coverage_aliased,
)


def test_aliased_match_finds_node_when_wikilink_surface_misses():
    # gold surface "Big Blue" != node surface "IBM"; alias set bridges it
    graph = {"entities": [_rent(5, "IBM"), _rent(1, "Red Hat")],
             "edges": [{"subj": 5, "obj": 1, "predicate": "acquired", "source_refs": ["Q37156"]}]}
    gm = [("Q37156", "Big Blue", "Q37156"), ("Qrh", "Red Hat", "Q37156")]
    aliases = {"Q37156": {"ibm", "big blue", "international business machines"}, "Qrh": {"red hat"}}
    clusters = sorted(map(sorted, align_real_mentions_to_nodes_aliased(graph, gm, aliases)))
    assert clusters == [[0], [1]]                            # Big Blue -> node 5 via alias "ibm"
    assert real_alignment_coverage_aliased(graph, gm, aliases) == 1.0


def test_aliased_largest_intersection_tiebreak_and_orphan():
    graph = {"entities": [_rent(5, "IBM"), _rent(8, "Apple")],
             "edges": [{"subj": 5, "obj": 8, "predicate": "r", "source_refs": ["d1"]}]}
    gm = [("Q37156", "IBM", "d1"), ("Qx", "Ghost", "d1")]
    aliases = {"Q37156": {"ibm"}}                            # Qx has no aliases + no surface match
    clusters = sorted(map(sorted, align_real_mentions_to_nodes_aliased(graph, gm, aliases)))
    assert [0] in clusters and sum(len(c) for c in clusters) == 2   # Ghost -> unique orphan
    assert real_alignment_coverage_aliased(graph, gm, aliases) == 0.5


def test_aliased_reduces_to_exact_surface_when_alias_is_surface():
    graph = {"entities": [_rent(7, "Apple")],
             "edges": [{"subj": 7, "obj": 7, "predicate": "r", "source_refs": ["d1"]}]}
    gm = [("Qa", "Apple", "d1")]
    assert align_real_mentions_to_nodes_aliased(graph, gm, {"Qa": {"apple"}}) == [[0]]
```

- [ ] **Step 2: Run, verify fail.**

- [ ] **Step 3: Implement** (after `real_alignment_coverage`):
```python
def _assign_real_nodes_aliased(graph, gold_mentions, qid_aliases):
    """Like `_assign_real_nodes` but the match target is the mention's QID ALIAS SET (union the literal
    surface), so a built node is found by ANY of the entity's aliases -- dissolving wikilink-surface vs
    extracted-form mismatch. Node surface set = surface_names + canonical_name (case-folded), same fields
    the surface aligner uses. Largest-intersection wins, lowest node id breaks ties; no match -> unique
    orphan negative."""
    id2surf = {}
    for e in graph.get("entities", ()):
        surfs = {str(s).strip().lower() for s in e.get("surface_names", ()) if s}
        cn = str(e.get("canonical_name", "")).strip().lower()
        if cn:
            surfs.add(cn)
        id2surf[e.get("entity_id")] = surfs
    by_doc = {}
    for e in graph.get("edges", ()):
        for ref in e.get("source_refs", ()):
            by_doc.setdefault(_base_doc_id(ref), set()).update((e.get("subj"), e.get("obj")))
    node_of = {}
    fresh = -1
    for i, (qid, surface, doc) in enumerate(gold_mentions):
        match = set(qid_aliases.get(qid, ())) | {str(surface).strip().lower()}
        best, best_ov = None, 0
        for n in sorted(by_doc.get(_base_doc_id(doc), set())):   # sorted -> lowest id on tie
            ov = len(id2surf.get(n, set()) & match)
            if ov > best_ov:
                best, best_ov = n, ov
        if best is not None:
            node_of[i] = best
        else:
            node_of[i] = fresh
            fresh -= 1
    return node_of


def align_real_mentions_to_nodes_aliased(graph, gold_mentions, qid_aliases):
    groups = {}
    for i, node in _assign_real_nodes_aliased(graph, gold_mentions, qid_aliases).items():
        groups.setdefault(node, []).append(i)
    return [sorted(v) for v in groups.values()]


def real_alignment_coverage_aliased(graph, gold_mentions, qid_aliases) -> float:
    node_of = _assign_real_nodes_aliased(graph, gold_mentions, qid_aliases)
    if not node_of:
        return 1.0
    return sum(1 for n in node_of.values() if n >= 0) / len(node_of)
```

- [ ] **Step 4: Run tests, verify pass** + ruff. **Commit** — `feat(er-kg-bench): alias-anchored real-prose aligner (align_real_aliased)`.

---

## Task 4: `run_wiki` uses the aliased aligner

**Files:** Modify `erkgbench/run_substrate_eval.py`.

- [ ] **Step 1:** In `run_wiki`, unpack the 3-tuple and swap the aligner:
```python
    documents, gold, qid_aliases = load_wiki_corpus()
    graph = _build_graph_from_documents(documents)
    clustering = substrate_eval.align_real_mentions_to_nodes_aliased(graph, gold, qid_aliases)
    coverage = substrate_eval.real_alignment_coverage_aliased(graph, gold, qid_aliases)
```
(rest unchanged).
- [ ] **Step 2:** ruff + `py_compile`. Box-safe: scoring reuses Task-3 units. **Commit** — `feat(er-kg-bench): --corpus wiki uses alias-anchored alignment`.

---

## Task 5: Modal re-run + verdict update

**Files:** Update `docs/superpowers/reports/2026-07-01-wiki-prose-substrate-verdict.md` (add the aliased section) or a new sibling report.

- [ ] **Step 1: Fire two legs** (distinct `--n`, `PYTHONIOENCODING=utf-8 PYTHONUTF8=1`):
```bash
M="/d/show_case/goldenmatch/.venv/Scripts/modal.exe"
$M run --detach scripts/distill/modal_bench.py --engine goldengraph --eval substrate --n 62 \
  --opts $'GOLDENGRAPH_SUBSTRATE_CORPUS=wiki' --spawn                                  # baseline (aliased align)
$M run --detach scripts/distill/modal_bench.py --engine goldengraph --eval substrate --n 63 \
  --opts $'GOLDENGRAPH_SUBSTRATE_CORPUS=wiki\nGOLDENGRAPH_XDOC_KEY=name_ci' --spawn     # name_ci
```
Monitor `results/substrate_6{2,3}_*.md` for `[substrate-wiki]`.

- [ ] **Step 2: Read coverage first.** Compare to the surface-only L2 (cov 0.40, baseline R 0.126 / name_ci 0.232). Coverage up => surface-mismatch was the miss, aligned R(B) is the clean absolute. Coverage flat => extraction-drop floor.
- [ ] **Step 3: Update the verdict** with the aliased numbers + the coverage interpretation.
- [ ] **Step 4: Commit** the report.

---

## Completion

Rebase onto main once #1341 merges. Use superpowers:finishing-a-development-branch: box-safe tests, PR (base `main`), arm auto-merge.
