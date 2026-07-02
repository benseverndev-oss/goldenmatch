# Substrate Metric Split (SP-A) Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Report the substrate as three named axes — presence / relational / connectivity — instead of one conflated `coverage` number, and ship the `LEVER_AXIS_MAP` contract the config-driver (SP-B/SP-C) will route ejections against.

**Architecture:** A single new pure assembler `substrate_scorecard()` composes already-existing scorers (`presence_aligner_report`, `align_mentions_to_nodes`+`metrics.score`, `edge_recall`, `graph_coherence`). `score_substrate` gains one safe-default optional `qid_aliases` param and always embeds the scorecard. `run_substrate_eval` reporting (both print sites + both markdown tables) is reworked to show the split. No new alignment math.

**Tech Stack:** Python (pure functions, no Polars/native/LLM in the changed code), pytest.

**Spec:** `docs/superpowers/specs/2026-07-02-substrate-metric-split-design.md`

**Branch:** `feat/substrate-metric-split` (already created off `origin/main`).

---

## Files

- **Modify** `packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/substrate_eval.py`
  - Add module constants `KNOWN_LEVERS` + `LEVER_AXIS_MAP`.
  - Add pure fn `substrate_scorecard(graph, gold_mentions, qid_aliases=None)`.
  - Add `qid_aliases=None` param to `score_substrate` + embed `"scorecard"` key.
- **Modify** `packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/run_substrate_eval.py`
  - `run_wiki()` returns the scorecard; `[substrate-wiki]` print + wiki markdown table show the three axes.
  - Engineered `[substrate]` print + `_to_markdown()` table show relational + connectivity(edge_recall) + coherence (no presence, no connectivity cov/f1 — None on that path).
- **Modify (tests)** `packages/python/goldenmatch/benchmarks/er-kg-bench/tests/test_substrate_eval.py`
  - All new tests are pure (hand-built graph dicts), box-safe, no Modal/LLM.

## Test runner (box-safe)

All test steps use this exact command from the `er-kg-bench` package dir (the package is not pip-installed in the shared `.venv`; `PYTHONPATH=$PWD` shadows it):

```bash
cd /d/show_case/gg-local-llm/packages/python/goldenmatch/benchmarks/er-kg-bench
PYTHONPATH="$PWD" PYTHONIOENCODING=utf-8 PYTHONUTF8=1 POLARS_SKIP_CPU_CHECK=1 GOLDENMATCH_NATIVE=0 \
  /d/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest tests/test_substrate_eval.py -q
```

Reference fixture shape used throughout (a graph dict is `{"entities": [...], "edges": [...]}`):
- entity: `{"entity_id": int, "canonical_name": str, "typ": str, "members": [...], "surface_names": [str], "source_refs": [str]}`
- edge: `{"subj": int, "predicate": str, "obj": int, "source_refs": [str]}`
- gold mention: `(entity_id_or_qid: str, surface: str, doc_id: str)`; engineered doc_id is `src::rel::dst`.

---

### Task 1: `substrate_scorecard()` — wiki (aliased) three-axis path

**Files:**
- Modify: `packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/substrate_eval.py` (add fn after `score_substrate`, ~line 285)
- Test: `packages/python/goldenmatch/benchmarks/er-kg-bench/tests/test_substrate_eval.py`

- [ ] **Step 1: Write the failing tests (aliased path)**

Add to `tests/test_substrate_eval.py`:

```python
from erkgbench import substrate_eval as se


def _wiki_graph():
    # Two docs, entity Q1 present+connected in docA, Q2 present but EDGELESS (only via node source_refs).
    return {
        "entities": [
            {"entity_id": 0, "canonical_name": "ibm", "typ": "org", "members": [],
             "surface_names": ["ibm", "big blue"], "source_refs": ["docA"]},
            {"entity_id": 1, "canonical_name": "lenovo", "typ": "org", "members": [],
             "surface_names": ["lenovo"], "source_refs": ["docA"]},
            {"entity_id": 2, "canonical_name": "acme", "typ": "org", "members": [],
             "surface_names": ["acme"], "source_refs": ["docB"]},
        ],
        "edges": [
            {"subj": 0, "predicate": "acquired", "obj": 1, "source_refs": ["docA::acquired::docA"]},
        ],
    }


def _wiki_gold():
    # (qid, surface, doc) — doc ids are BASE ids for the wiki path.
    return [("Q1", "big blue", "docA"), ("Q2", "acme", "docB")]


def _wiki_aliases():
    return {"Q1": ["ibm", "big blue"], "Q2": ["acme"]}


def test_scorecard_presence_matches_relaxed():
    g, gold, al = _wiki_graph(), _wiki_gold(), _wiki_aliases()
    sc = se.substrate_scorecard(g, gold, al)
    rep = se.presence_aligner_report(g, gold, al)
    assert sc["presence"]["coverage"] == rep["relaxed_coverage"]


def test_scorecard_relational_over_presence():
    g, gold, al = _wiki_graph(), _wiki_gold(), _wiki_aliases()
    sc = se.substrate_scorecard(g, gold, al)
    rep = se.presence_aligner_report(g, gold, al)
    assert sc["relational"]["f1"] == rep["relaxed_fb"]
    assert sc["relational"]["recall"] == rep["relaxed_rb"]
    assert sc["relational"]["precision"] == rep["relaxed_pb"]


def test_scorecard_connectivity_is_strict():
    g, gold, al = _wiki_graph(), _wiki_gold(), _wiki_aliases()
    sc = se.substrate_scorecard(g, gold, al)
    rep = se.presence_aligner_report(g, gold, al)
    assert sc["connectivity"]["coverage"] == rep["strict_coverage"]
    assert sc["connectivity"]["f1"] == rep["strict_fb"]
    assert sc["connectivity"]["edge_recall"] == se.edge_recall(g, gold)
    assert sc["coherence"] == se.graph_coherence(g)
```

- [ ] **Step 2: Run to verify it fails**

Run the box-safe command with `-k scorecard`.
Expected: FAIL — `AttributeError: module 'erkgbench.substrate_eval' has no attribute 'substrate_scorecard'`.

- [ ] **Step 3: Implement the aliased branch**

Add to `substrate_eval.py` after `score_substrate`:

```python
def substrate_scorecard(graph: dict, gold_mentions, qid_aliases=None) -> dict:
    """Three-axis substrate scoreboard, assembled from existing pure scorers (no new alignment math):
    PRESENCE  = is the gold entity in the KB at all (global alias match; wiki path only, needs
                `qid_aliases`); RELATIONAL = given presence, clustering quality R(B)/P(B)/F1;
                CONNECTIVITY = how much is actually edge-wired (the old edge-gated headline, relabeled).
    On the engineered/no-alias path (`qid_aliases is None`) presence and connectivity.coverage/.f1 are
    None (both derive from the alias-dependent strict/relaxed aligner); only edge_recall + coherence
    are alias-free. See docs/superpowers/specs/2026-07-02-substrate-metric-split-design.md."""
    from erkgbench import metrics

    coh = graph_coherence(graph)
    er = edge_recall(graph, gold_mentions)
    if qid_aliases is not None:
        rep = presence_aligner_report(graph, gold_mentions, qid_aliases)
        return {
            "presence": {"coverage": rep["relaxed_coverage"]},
            "relational": {"f1": rep["relaxed_fb"], "recall": rep["relaxed_rb"],
                           "precision": rep["relaxed_pb"]},
            "connectivity": {"coverage": rep["strict_coverage"], "f1": rep["strict_fb"],
                             "edge_recall": er},
            "coherence": coh,
        }
    entity_ids = [m[0] for m in gold_mentions]
    b = metrics.score(entity_ids, align_mentions_to_nodes(graph, gold_mentions))
    return {
        "presence": None,
        "relational": {"f1": b.f1, "recall": b.recall, "precision": b.precision},
        "connectivity": {"coverage": None, "f1": None, "edge_recall": er},
        "coherence": coh,
    }
```

- [ ] **Step 4: Run to verify aliased tests pass**

Run the box-safe command with `-k scorecard`.
Expected: PASS (the 3 aliased tests).

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/substrate_eval.py \
        packages/python/goldenmatch/benchmarks/er-kg-bench/tests/test_substrate_eval.py
git commit -m "feat(substrate): substrate_scorecard three-axis (presence/relational/connectivity), wiki path"
```

---

### Task 2: `substrate_scorecard()` — engineered (no-alias) path + the exposing test

**Files:**
- Modify: `substrate_eval.py` (implemented in Task 1 already covers this branch)
- Test: `tests/test_substrate_eval.py`

- [ ] **Step 1: Write the failing tests (no-alias + the split it exists to expose)**

```python
def test_scorecard_no_aliases_presence_none():
    g, gold = _wiki_graph(), _wiki_gold()
    sc = se.substrate_scorecard(g, gold, qid_aliases=None)
    assert sc["presence"] is None
    assert sc["connectivity"]["coverage"] is None
    assert sc["connectivity"]["f1"] is None
    assert sc["connectivity"]["edge_recall"] == se.edge_recall(g, gold)
    assert set(sc["relational"]) == {"f1", "recall", "precision"}
    assert sc["coherence"] == se.graph_coherence(g)


def test_scorecard_all_present_perfect():
    # Every gold is a connected node in its own doc: presence, relational, connectivity all perfect.
    g = {
        "entities": [
            {"entity_id": 0, "canonical_name": "ibm", "typ": "org", "members": [],
             "surface_names": ["ibm"], "source_refs": ["docA"]},
            {"entity_id": 1, "canonical_name": "lenovo", "typ": "org", "members": [],
             "surface_names": ["lenovo"], "source_refs": ["docA"]},
        ],
        "edges": [{"subj": 0, "predicate": "acquired", "obj": 1, "source_refs": ["docA::acquired::docA"]}],
    }
    gold = [("Q1", "ibm", "docA"), ("Q2", "lenovo", "docA")]
    al = {"Q1": ["ibm"], "Q2": ["lenovo"]}
    sc = se.substrate_scorecard(g, gold, al)
    assert sc["presence"]["coverage"] == 1.0
    assert sc["connectivity"]["coverage"] == 1.0


def test_scorecard_present_but_unconnected():
    # Gold entities exist as nodes (global match) but their docs produced NO edge:
    # presence high, connectivity coverage ~0 -> the exact conflation this sub-project splits.
    g = {
        "entities": [
            {"entity_id": 0, "canonical_name": "ibm", "typ": "org", "members": [],
             "surface_names": ["ibm"], "source_refs": ["docA"]},
            {"entity_id": 1, "canonical_name": "acme", "typ": "org", "members": [],
             "surface_names": ["acme"], "source_refs": ["docB"]},
        ],
        "edges": [],
    }
    gold = [("Q1", "ibm", "docA"), ("Q2", "acme", "docB")]
    al = {"Q1": ["ibm"], "Q2": ["acme"]}
    sc = se.substrate_scorecard(g, gold, al)
    assert sc["presence"]["coverage"] == 1.0
    assert sc["connectivity"]["coverage"] == 0.0
```

- [ ] **Step 2: Run to verify**

Run the box-safe command with `-k scorecard`.
Expected: PASS for all (the no-alias branch already exists from Task 1; `all_present`/`present_but_unconnected` validate the wiki branch semantics). If `test_scorecard_present_but_unconnected` fails on the exact `0.0`, inspect `strict_coverage` on an edgeless graph (`_assign_real_nodes_aliased` finds no `by_doc`/`node_by_doc` edge candidates → all orphan negatives → coverage 0.0); adjust the fixture only if `_assign_real_nodes_aliased`'s node-provenance union (#1369) is present on the branch and reaches the nodes — if so this test asserts the node-provenance behavior, which is fine; keep whichever value the real function returns and assert it explicitly.

> NOTE: this branch (`feat/substrate-metric-split`) is off `origin/main` and does NOT include #1369's node-provenance union in `_assign_real_nodes_aliased`, so edgeless nodes are unreachable by the strict aligner → `strict_coverage == 0.0`. If the branch is later rebased onto a main that has #1369, revisit this assertion.

- [ ] **Step 3: Commit**

```bash
git add packages/python/goldenmatch/benchmarks/er-kg-bench/tests/test_substrate_eval.py
git commit -m "test(substrate): scorecard no-alias path + present-but-unconnected split"
```

---

### Task 3: `LEVER_AXIS_MAP` + `KNOWN_LEVERS` contract

**Files:**
- Modify: `substrate_eval.py` (add constants near the top of the module, after the docstring/imports, ~line 3)
- Test: `tests/test_substrate_eval.py`

- [ ] **Step 1: Write the failing test**

```python
def test_lever_axis_map_names_are_real_gates():
    # Every lever named in any LEVER_AXIS_MAP value must be a known lever with a real GOLDENGRAPH_* gate.
    mapped = {lv for lvs in se.LEVER_AXIS_MAP.values() for lv in lvs}
    assert mapped <= set(se.KNOWN_LEVERS), mapped - set(se.KNOWN_LEVERS)
    for lever, env in se.KNOWN_LEVERS.items():
        assert env.startswith("GOLDENGRAPH_"), (lever, env)
    assert set(se.LEVER_AXIS_MAP) == {"presence", "relational", "connectivity"}


def test_known_levers_gates_exist_in_source():
    # Guard against a typo'd env name: each KNOWN_LEVERS env var must appear in the goldengraph source.
    import pathlib
    gg = pathlib.Path(se.__file__).resolve()
    # walk up to repo root, then into the goldengraph package
    root = gg
    for _ in range(10):
        cand = root.parent / "packages" / "python" / "goldengraph" / "goldengraph"
        if cand.is_dir():
            pkg = cand
            break
        root = root.parent
    else:
        import pytest
        pytest.skip("goldengraph package not locatable from this checkout")
    blob = "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in pkg.glob("*.py"))
    for lever, env in se.KNOWN_LEVERS.items():
        assert env in blob, f"{lever} -> {env} not found in goldengraph source"
```

- [ ] **Step 2: Run to verify it fails**

Run box-safe with `-k lever or known_levers`.
Expected: FAIL — `AttributeError: ... has no attribute 'LEVER_AXIS_MAP'`.

- [ ] **Step 3: Implement the constants**

Add near the top of `substrate_eval.py` (after line 2, the `from __future__` import):

```python
# --- Config-driver contract (consumed by SP-B/SP-C) -------------------------------------------------
# Each substrate lever -> the GOLDENGRAPH_* env var that gates it. This is the explicit source of truth
# for "what levers exist" until SP-B's SubstrateConfig becomes the registry.
KNOWN_LEVERS: dict[str, str] = {
    "chunk_extract": "GOLDENGRAPH_CHUNK_EXTRACT",
    "extract_recall": "GOLDENGRAPH_EXTRACT_RECALL",
    "extractor": "GOLDENGRAPH_EXTRACTOR",
    "xdoc_key": "GOLDENGRAPH_XDOC_KEY",
    "entity_type_canon": "GOLDENGRAPH_ENTITY_TYPE_CANON",
    "schema_canon": "GOLDENGRAPH_SCHEMA_CANON",
    "relation_vocab": "GOLDENGRAPH_RELATION_VOCAB",
    "relation_reprompt": "GOLDENGRAPH_RELATION_REPROMPT",
    "rebel_fuse": "GOLDENGRAPH_REBEL_FUSE",
}

# Which axis each lever CAN move (not exclusive -- a lever may affect more than one). The SP-C ejection
# router reads LEVER_AXIS_MAP[failing_axis] to narrow which levers to propose tweaking. Encodes the
# arc's MEASURED findings (chunking->presence WIN, name_ci/xdoc->relational WIN; reprompt/rebel refuted
# -> included but must stay measurement-gated). A hint, not an authorization to blind-flip.
LEVER_AXIS_MAP: dict[str, list[str]] = {
    "presence": ["chunk_extract", "extract_recall", "extractor"],
    "relational": ["xdoc_key", "entity_type_canon", "schema_canon", "relation_vocab",
                   "relation_reprompt", "rebel_fuse"],
    "connectivity": ["relation_reprompt", "rebel_fuse", "relation_vocab"],
}
```

- [ ] **Step 4: Run to verify passes**

Run box-safe with `-k lever or known_levers`.
Expected: PASS (both).

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/substrate_eval.py \
        packages/python/goldenmatch/benchmarks/er-kg-bench/tests/test_substrate_eval.py
git commit -m "feat(substrate): LEVER_AXIS_MAP + KNOWN_LEVERS driver contract (SP-B/SP-C consume)"
```

---

### Task 4: `score_substrate` optional `qid_aliases` + embedded scorecard (back-compat)

**Files:**
- Modify: `substrate_eval.py:271-285` (`score_substrate`)
- Test: `tests/test_substrate_eval.py`

- [ ] **Step 1: Write the failing tests**

```python
def _eng_graph_gold():
    # Engineered doc-id oracle: doc id is src::rel::dst; one edge per doc.
    g = {
        "entities": [
            {"entity_id": 0, "canonical_name": "a", "typ": "t", "members": [], "surface_names": ["a"],
             "source_refs": ["e0::r::e1"]},
            {"entity_id": 1, "canonical_name": "b", "typ": "t", "members": [], "surface_names": ["b"],
             "source_refs": ["e0::r::e1"]},
        ],
        "edges": [{"subj": 0, "predicate": "r", "obj": 1, "source_refs": ["e0::r::e1"]}],
    }
    gold = [("e0", "a", "e0::r::e1"), ("e1", "b", "e0::r::e1")]
    return g, gold


def test_score_substrate_backcompat_no_aliases():
    g, gold = _eng_graph_gold()
    resolver_clusters = [[0], [1]]
    out = se.score_substrate(gold_mentions=gold, resolver_clusters=resolver_clusters, graph=g)
    # every legacy flat key still present
    for k in ("er_f1_a", "er_p_a", "er_r_a", "er_f1_b", "er_p_b", "er_r_b",
              "ab_gap", "components", "largest_fraction", "provenance", "edge_recall"):
        assert k in out
    # new scorecard embedded, presence None without aliases
    assert "scorecard" in out
    assert out["scorecard"]["presence"] is None
    # relational in the scorecard matches the legacy flat er_*_b (same alignment)
    assert out["scorecard"]["relational"]["f1"] == out["er_f1_b"]


def test_score_substrate_embeds_scorecard_with_aliases():
    g, gold = _wiki_graph(), _wiki_gold()
    out = se.score_substrate(gold_mentions=gold, resolver_clusters=[[0], [1]], graph=g,
                             qid_aliases=_wiki_aliases())
    assert out["scorecard"]["presence"] is not None
    assert out["scorecard"]["presence"]["coverage"] == 1.0
```

- [ ] **Step 2: Run to verify it fails**

Run box-safe with `-k score_substrate`.
Expected: FAIL — `TypeError: score_substrate() got an unexpected keyword argument 'qid_aliases'` (second test) and `KeyError: 'scorecard'` (first).

- [ ] **Step 3: Implement — add param + embed scorecard**

In `substrate_eval.py`, change the signature and the return dict of `score_substrate`:

Signature (line 271):
```python
def score_substrate(*, gold_mentions, resolver_clusters, graph, qid_aliases=None) -> dict:
```

At the END of the return dict (currently closes ~line 289 with `edge_recall`), add the scorecard key. The simplest safe edit: build the dict, then attach:

```python
    result = {
        "er_f1_a": a.f1, "er_p_a": a.precision, "er_r_a": a.recall,
        "er_f1_b": b.f1, "er_p_b": b.precision, "er_r_b": b.recall,
        "ab_gap": a.f1 - b.f1,
        "components": coh["components"], "largest_fraction": coh["largest_fraction"],
        "provenance": provenance_coverage(graph),
        "edge_recall": edge_recall(graph, gold_mentions),
    }
    result["scorecard"] = substrate_scorecard(graph, gold_mentions, qid_aliases)
    return result
```

(Note: the relational axis in the no-alias scorecard is scored over `align_mentions_to_nodes` — the SAME clustering as the legacy `er_*_b` — so `scorecard.relational.f1 == er_f1_b` by construction; the test asserts this.)

- [ ] **Step 4: Run to verify passes**

Run box-safe with `-k score_substrate`, then the FULL file to confirm no regression:
```bash
... -m pytest tests/test_substrate_eval.py -q
```
Expected: all green (prior 27 + the new scorecard/lever/score_substrate tests).

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/substrate_eval.py \
        packages/python/goldenmatch/benchmarks/er-kg-bench/tests/test_substrate_eval.py
git commit -m "feat(substrate): score_substrate embeds scorecard via optional qid_aliases (back-compat)"
```

---

### Task 5: Wire reporting — wiki path (`run_wiki` + `[substrate-wiki]` + wiki md table)

**Files:**
- Modify: `run_substrate_eval.py:78-90` (`run_wiki`), `:244-264` (wiki print + md)

- [ ] **Step 1: Make `run_wiki` return the scorecard**

Replace `run_wiki`'s return (lines 84-90) so it also carries the scorecard (keep the existing flat keys so nothing else breaks):

```python
    documents, gold, qid_aliases, graph = _wiki_build()
    clustering = substrate_eval.align_real_mentions_to_nodes_aliased(graph, gold, qid_aliases)
    coverage = substrate_eval.real_alignment_coverage_aliased(graph, gold, qid_aliases)
    b = metrics.score([m[0] for m in gold], clustering)
    coh = substrate_eval.graph_coherence(graph)
    sc = substrate_eval.substrate_scorecard(graph, gold, qid_aliases)
    return {"er_r_b": b.recall, "er_p_b": b.precision, "er_f1_b": b.f1, "coverage": coverage,
            "n_docs": len(documents), "n_gold": len(gold), "components": coh["components"],
            "scorecard": sc}
```

- [ ] **Step 2: Rework the `[substrate-wiki]` print + md table (lines 244-264)**

```python
    if args.corpus == "wiki":
        r = run_wiki()
        sc = r["scorecard"]
        print(
            f"[substrate-wiki] presence: cov={sc['presence']['coverage']:.4f} | "
            f"relational: F1={sc['relational']['f1']:.4f} R={sc['relational']['recall']:.4f} "
            f"P={sc['relational']['precision']:.4f} | "
            f"connectivity: cov={sc['connectivity']['coverage']:.4f} F1={sc['connectivity']['f1']:.4f} "
            f"edge_recall={sc['connectivity']['edge_recall']:.4f} | "
            f"coherence: comp={sc['coherence']['components']} "
            f"largest={sc['coherence']['largest_fraction']:.3f} "
            f"docs={r['n_docs']} gold={r['n_gold']}",
            flush=True,
        )
        md = (
            "# Substrate-Quality (real Wikipedia prose)\n\n"
            "| presence_cov | relational_F1 | relational_R | relational_P | connectivity_cov | "
            "connectivity_F1 | edge_recall | components | docs | gold |\n"
            "|---|---|---|---|---|---|---|---|---|---|\n"
            f"| {sc['presence']['coverage']:.4f} | {sc['relational']['f1']:.4f} | "
            f"{sc['relational']['recall']:.4f} | {sc['relational']['precision']:.4f} | "
            f"{sc['connectivity']['coverage']:.4f} | {sc['connectivity']['f1']:.4f} | "
            f"{sc['connectivity']['edge_recall']:.4f} | {sc['coherence']['components']} | "
            f"{r['n_docs']} | {r['n_gold']} |\n\n"
            "PRESENCE = fraction of gold entities present as a node (global alias match). "
            "RELATIONAL = clustering quality given presence. CONNECTIVITY = the old edge-gated "
            "'coverage' (relabeled) + edge_recall. See the metric-split spec.\n"
        )
        with open(args.out_md, "w", encoding="utf-8") as fh:
            fh.write(md)
        print("\n" + md, flush=True)
        return
```

- [ ] **Step 3: Static-check + smoke-parse the format strings**

There is no gold wiki corpus to build on the box, so verify the print/md code compiles and the f-strings reference real keys by constructing a fake `r`:

```bash
cd /d/show_case/gg-local-llm/packages/python/goldenmatch/benchmarks/er-kg-bench
PYTHONPATH="$PWD" /d/show_case/goldenmatch/.venv/Scripts/python.exe -c "
import ast; ast.parse(open('erkgbench/run_substrate_eval.py').read()); print('parse-ok')
from erkgbench import substrate_eval as se
g={'entities':[{'entity_id':0,'canonical_name':'ibm','typ':'org','members':[],'surface_names':['ibm'],'source_refs':['docA']}],'edges':[{'subj':0,'predicate':'r','obj':0,'source_refs':['docA::r::docA']}]}
gold=[('Q1','ibm','docA')]; al={'Q1':['ibm']}
sc=se.substrate_scorecard(g,gold,al)
print('[substrate-wiki] presence: cov=%.4f | relational: F1=%.4f | connectivity: cov=%.4f edge_recall=%.4f' % (sc['presence']['coverage'], sc['relational']['f1'], sc['connectivity']['coverage'], sc['connectivity']['edge_recall']))
"
```
Expected: `parse-ok` then a formatted line with no `KeyError`.

- [ ] **Step 4: Commit**

```bash
git add packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/run_substrate_eval.py
git commit -m "feat(substrate): wiki reporting shows presence/relational/connectivity split"
```

---

### Task 6: Wire reporting — engineered path (`[substrate]` + `_to_markdown`)

**Files:**
- Modify: `run_substrate_eval.py:156-173` (`_to_markdown`), `:270-275` (engineered print)

- [ ] **Step 1: Rework the engineered `[substrate]` print (lines 270-275)**

`run_one` already returns `score_substrate(...)` which now embeds `scorecard`. Update the print to add the connectivity(edge_recall)+coherence framing via the scorecard (no presence, no connectivity cov/f1 — None on this path):

```python
        sb = run_one(args.seed, amb)
        rows.append((amb, sb))
        sc = sb["scorecard"]
        print(
            f"[substrate] ambiguity={amb}: relational: F1={sc['relational']['f1']:.4f} "
            f"R={sc['relational']['recall']:.4f} P={sc['relational']['precision']:.4f} | "
            f"connectivity: edge_recall={sc['connectivity']['edge_recall']:.4f} | "
            f"coherence: comp={sc['coherence']['components']} "
            f"largest={sc['coherence']['largest_fraction']:.3f} | "
            f"ER-F1(A)={sb['er_f1_a']:.4f} gap={sb['ab_gap']:.4f} provenance={sb['provenance']:.3f}",
            flush=True,
        )
```

(Keep `er_f1_a`/`ab_gap`/`provenance` — the A-vs-B gap is the engineered instrument's validated signal; the split just relabels the B side.)

- [ ] **Step 2: Update `_to_markdown` header + body (lines 156-173)**

Add `presence`/`connectivity` framing columns. Presence is N/A on engineered, so show the relational F1(B) as the substrate B-axis and keep A/gap. Minimal, honest change — relabel the existing table note; do NOT invent a presence column that is always empty:

```python
def _to_markdown(rows: list[tuple[float, dict]]) -> str:
    head = (
        "# Substrate-Quality Scoreboard (engineered)\n\n"
        "| ambiguity | ER-F1(A) | relational_F1(B) | relational_P | relational_R | edge_recall | "
        "A-B gap | components | largest-frac | provenance |\n"
        "|---|---|---|---|---|---|---|---|---|---|\n"
    )
    body = "".join(
        f"| {amb} | {sb['er_f1_a']:.4f} | {sb['er_f1_b']:.4f} | {sb['er_p_b']:.4f} | {sb['er_r_b']:.4f} | "
        f"{sb['edge_recall']:.4f} | {sb['ab_gap']:.4f} | {sb['components']} | {sb['largest_fraction']:.4f} | "
        f"{sb['provenance']:.4f} |\n"
        for amb, sb in rows
    )
    note = (
        "\nA = resolver in isolation (clean gold surfaces); B = end-to-end build. **A-B gap = "
        "extraction-induced fragmentation.** On the engineered corpus the doc-id oracle IS the presence "
        "signal, so only the RELATIONAL (B) + connectivity(edge_recall) axes are reported here; the "
        "presence/connectivity-coverage split is a wiki-path (alias-bearing) metric.\n"
    )
    return head + body + note
```

- [ ] **Step 3: Static-check the engineered path**

```bash
cd /d/show_case/gg-local-llm/packages/python/goldenmatch/benchmarks/er-kg-bench
PYTHONPATH="$PWD" /d/show_case/goldenmatch/.venv/Scripts/python.exe -c "
import ast; ast.parse(open('erkgbench/run_substrate_eval.py').read()); print('parse-ok')
from erkgbench import substrate_eval as se
g={'entities':[{'entity_id':0,'canonical_name':'a','typ':'t','members':[],'surface_names':['a'],'source_refs':['e0::r::e1']},{'entity_id':1,'canonical_name':'b','typ':'t','members':[],'surface_names':['b'],'source_refs':['e0::r::e1']}],'edges':[{'subj':0,'predicate':'r','obj':1,'source_refs':['e0::r::e1']}]}
gold=[('e0','a','e0::r::e1'),('e1','b','e0::r::e1')]
out=se.score_substrate(gold_mentions=gold, resolver_clusters=[[0],[1]], graph=g)
sc=out['scorecard']
print('[substrate] relational F1=%.4f | connectivity edge_recall=%.4f | comp=%d' % (sc['relational']['f1'], sc['connectivity']['edge_recall'], sc['coherence']['components']))
from erkgbench.run_substrate_eval import _to_markdown
print(_to_markdown([(0.0, out)])[:80])
"
```
Expected: `parse-ok`, a formatted `[substrate]` line, and the markdown header — no `KeyError`.

- [ ] **Step 4: Run the FULL test file once more**

```bash
cd /d/show_case/gg-local-llm/packages/python/goldenmatch/benchmarks/er-kg-bench
PYTHONPATH="$PWD" PYTHONIOENCODING=utf-8 PYTHONUTF8=1 POLARS_SKIP_CPU_CHECK=1 GOLDENMATCH_NATIVE=0 \
  /d/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest tests/test_substrate_eval.py -q
```
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/run_substrate_eval.py
git commit -m "feat(substrate): engineered reporting shows relational + edge_recall + coherence (presence N/A)"
```

---

### Task 7: Finish the branch

- [ ] **Step 1: Full test file green (final)** — run the box-safe command once more, confirm the pre-existing 27 + new tests all pass.
- [ ] **Step 2: Update the spec status** — flip the spec header `Status:` to `implemented` and commit (`docs(substrate): mark metric-split spec implemented`).
- [ ] **Step 3: Push + PR** — per the SOP: `unset GH_TOKEN; export GH_TOKEN=$(gh auth token --user benzsevern)`; `git push -u origin feat/substrate-metric-split`; open PR base `main`; arm `gh pr merge <N> --repo benseverndev-oss/goldenmatch --auto` and STOP (no CI poll loop).
- [ ] **Step 4: Update memory** — append the SP-A result to `project_goldengraph_local_oss_llm_lane.md` (metric split shipped; the config-surface program's SP-B/SP-C are the follow-ons).

---

## Notes for the implementer

- **Do NOT run the full pytest suite or Modal.** Only the single `tests/test_substrate_eval.py` file via the box-safe command. The box is memory-starved.
- **No LLM, no build, no Modal in this sub-project** — every changed line is pure Python over dict fixtures. The wiki/engineered *end-to-end* numbers don't change; only the *reporting* of them does.
- **`metrics.score`** returns an object with `.f1`, `.precision`, `.recall` (see existing `score_substrate`). Don't assume a dict.
- **Back-compat is load-bearing:** `score_substrate`'s existing flat keys must stay byte-identical; the scorecard is strictly additive. If any existing test asserts the exact key set of `score_substrate`'s return, update it to allow the new `scorecard` key.
