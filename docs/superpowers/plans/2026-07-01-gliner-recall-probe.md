# GLiNER Entity-Recall Probe — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A measurement-only Modal probe that reports how much of the real-prose substrate residual GLiNER could recover — splitting NER-miss (GLiNER-addressable) from edge-miss — to gate whether the GLiNER-hybrid lever is worth building.

**Architecture:** A pure scorer `gliner_probe_report` in `substrate_eval.py` (box-unit-tested) plus an impure runner `run_wiki_gliner_probe` in `run_substrate_eval.py` that builds the best-config graph, runs GLiNER per-doc, and calls the scorer. One shared alias-match primitive backs both the aligner and the probe so they can't drift. No production engine change; gate stays off.

**Tech Stack:** Python 3.11, pure stdlib for the scorer; `gliner` (Modal image only) for the runner; pytest.

**Spec:** `docs/superpowers/specs/2026-07-01-gliner-recall-probe-design.md`
**Branch:** `feat/gliner-recall-probe` (off `main`, chunking already merged).

**Box-safe test invocation** (run yourself; the scorer is pure so no goldengraph/polars import is triggered):
```bash
PY="/d/show_case/goldenmatch/.venv/Scripts/python.exe"
BENCH="D:/show_case/gg-local-llm/packages/python/goldenmatch/benchmarks/er-kg-bench"
cd "$BENCH"
PYTHONPATH="$BENCH" POLARS_SKIP_CPU_CHECK=1 "$PY" -m pytest tests/test_substrate_eval.py -q -p no:cacheprovider
```

## File structure

| File | Responsibility |
|---|---|
| `packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/substrate_eval.py` | **Modify.** Add pure `_alias_match_surface` primitive + `gliner_probe_report`. Point the aligner's inline substring test at the primitive (DRY, no-drift). |
| `packages/python/goldenmatch/benchmarks/er-kg-bench/tests/test_substrate_eval.py` | **Modify.** 5 new pure-scorer tests + confirm existing aligner tests still pass. |
| `packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/run_substrate_eval.py` | **Modify.** Factor `_wiki_build()`; add `run_wiki_gliner_probe()`; add `--gliner-probe` flag + `GOLDENGRAPH_GLINER_PROBE` env in `main`. |
| `scripts/distill/modal_bench.py` | **Modify.** Add `gliner` to the image `pip_install`. |
| `docs/superpowers/reports/2026-07-01-gliner-recall-probe-verdict.md` | **Create** in Task 4 after the Modal run. |

---

## Task 1: pure `_alias_match_surface` primitive + `gliner_probe_report`

**Files:**
- Modify: `erkgbench/substrate_eval.py`
- Test: `tests/test_substrate_eval.py`

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_substrate_eval.py`:

```python
from erkgbench.substrate_eval import gliner_probe_report


def _graph(entities, edges):
    return {"entities": entities, "edges": edges}


def test_probe_splits_ner_miss_from_edge_miss():
    # gold A: aligned (node 1 has an in-doc edge). gold B: edge-miss (node 2 exists, no edge in its doc).
    # gold C: ner-miss (no node matches its aliases anywhere).
    entities = [
        {"entity_id": 1, "canonical_name": "Apple", "surface_names": ["Apple"], "typ": "org"},
        {"entity_id": 2, "canonical_name": "Tim Cook", "surface_names": ["Tim Cook"], "typ": "person"},
    ]
    edges = [{"subj": 1, "obj": 1, "predicate": "is", "source_refs": ["docA"]}]  # only docA has an edge
    graph = _graph(entities, edges)
    gold = [
        ("Qa", "apple", "docA"),      # aligned (node 1, docA edge)
        ("Qb", "tim cook", "docB"),   # edge-miss: node 2 exists but docB has no edge
        ("Qc", "sundar pichai", "docC"),  # ner-miss: no node matches
    ]
    aliases = {"Qa": ["apple"], "Qb": ["tim cook"], "Qc": ["sundar pichai"]}
    # GLiNER finds the edge-miss AND the ner-miss entity in their docs
    gliner_by_doc = {"docB": {"Tim Cook"}, "docC": {"Sundar Pichai"}}
    r = gliner_probe_report(graph, gold, aliases, gliner_by_doc)
    assert r["n_gold"] == 3
    assert r["n_missed"] == 2          # B and C
    assert r["n_edge_miss"] == 1       # B
    assert r["n_ner_miss"] == 1        # C
    # the true prize: of the 1 ner-miss, GLiNER found 1
    assert r["ner_recovered_frac"] == 1.0
    # conflated context metric counts both missed that GLiNER matched
    assert r["residual_recovered_frac"] == 1.0


def test_probe_case_folds_gliner_surface():
    # cased GLiNER surface must match a lowercased alias/gold set (guards false REFUTED).
    graph = _graph([], [])
    gold = [("Qx", "barack obama", "d1")]
    aliases = {"Qx": ["barack obama"]}
    r = gliner_probe_report(graph, gold, aliases, {"d1": {"Barack Obama"}})
    assert r["gliner_recall"] == 1.0


def test_probe_alias_and_substring_and_per_doc_match():
    graph = _graph([], [])
    gold = [
        ("Qibm", "big blue", "d1"),        # matches via alias "ibm"
        ("Qn", "thomas nabbes", "d2"),     # matches via substring "nabbes"
        ("Qz", "zeta", "d3"),              # no gliner match
    ]
    aliases = {"Qibm": ["ibm", "big blue"], "Qn": ["thomas nabbes"], "Qz": ["zeta"]}
    gliner_by_doc = {
        "d1": {"IBM"},          # alias match
        "d2": {"Nabbes"},       # substring match
        "d3": {"Yeti"},         # unrelated -> junk, no gold match
    }
    r = gliner_probe_report(graph, gold, aliases, gliner_by_doc)
    assert r["gliner_recall"] == 2 / 3
    # per-doc: a d1 surface must not match a d3 gold
    # junk: "Yeti" in d3 matches no d3 gold -> 1 junk of 3 total surfaces
    assert r["junk_rate"] == 1 / 3


def test_probe_junk_rate_all_match_is_zero():
    graph = _graph([], [])
    gold = [("Qa", "apple", "d1")]
    aliases = {"Qa": ["apple"]}
    r = gliner_probe_report(graph, gold, aliases, {"d1": {"Apple"}})
    assert r["junk_rate"] == 0.0


def test_probe_degenerate_guards():
    graph = _graph([], [])
    # empty gliner
    r = gliner_probe_report(graph, [("Qa", "apple", "d1")], {"Qa": ["apple"]}, {})
    assert r["gliner_recall"] == 0.0 and r["ner_recovered_frac"] == 0.0 and r["junk_rate"] == 0.0
    # empty gold
    r0 = gliner_probe_report(graph, [], {}, {"d1": {"Apple"}})
    assert r0["n_gold"] == 0 and r0["residual_recovered_frac"] == 0.0
    # all-aligned (|missed| == 0): one gold, one node with an in-doc edge
    g2 = _graph([{"entity_id": 1, "canonical_name": "Apple", "surface_names": ["Apple"], "typ": "org"}],
                [{"subj": 1, "obj": 1, "predicate": "is", "source_refs": ["d1"]}])
    r2 = gliner_probe_report(g2, [("Qa", "apple", "d1")], {"Qa": ["apple"]}, {"d1": {"Apple"}})
    assert r2["n_missed"] == 0 and r2["residual_recovered_frac"] == 0.0 and r2["ner_recovered_frac"] == 0.0
```

- [ ] **Step 2: Run, verify fail.** Box-safe invocation. Expected: `ImportError: cannot import name 'gliner_probe_report'`.

- [ ] **Step 3: Implement in `substrate_eval.py`.** Add the primitive near `_base_doc_id`, then the report at the end of the file:

```python
def _alias_match_surface(surf_lc: str, match_set: set[str]) -> bool:
    """True iff a (already lowercased) surface matches a lowercased alias/gold `match_set`:
    exact membership OR substring either way. The single shared predicate behind both the
    aligned-node substring fallback and the GLiNER-probe match, so they cannot drift."""
    if not surf_lc:
        return False
    if surf_lc in match_set:
        return True
    return any(surf_lc in m or m in surf_lc for m in match_set if m)


def gliner_probe_report(graph: dict, gold_mentions, qid_aliases, gliner_by_doc) -> dict:
    """Measure GLiNER's addressable recall against the real-prose substrate residual.

    Splits the unaligned gold (`_assign_real_nodes_aliased` node_of < 0) into NER-miss (no graph node
    matches the alias set anywhere -> GLiNER-addressable) vs edge-miss (a node exists but produced no
    in-doc edge -> GLiNER can't help). The gate number is `ner_recovered_frac`: of the NER-miss gold,
    the share whose entity GLiNER surfaces (in the same doc). Pure; `gliner_by_doc` maps base doc id ->
    set of raw GLiNER surface strings."""
    node_of = _assign_real_nodes_aliased(graph, gold_mentions, qid_aliases)
    # node surfaces (case-folded), exactly as the aligner builds them
    id2surf: dict[int, set[str]] = {}
    for e in graph.get("entities", ()):
        surfs = {str(s).strip().lower() for s in e.get("surface_names", ()) if s}
        cn = str(e.get("canonical_name", "")).strip().lower()
        if cn:
            surfs.add(cn)
        id2surf[e.get("entity_id")] = surfs

    def _match_set(qid, surface):
        return set(qid_aliases.get(qid, ())) | {str(surface).strip().lower()}

    def _entity_exists(match_set):  # any node anywhere whose surfaces match -> NER present
        return any(
            any(_alias_match_surface(s, match_set) for s in surfs) for surfs in id2surf.values()
        )

    def _gliner_hit(doc, match_set):  # any GLiNER surface IN THIS DOC matches
        surfaces = gliner_by_doc.get(_base_doc_id(doc), ())
        return any(_alias_match_surface(str(g).strip().lower(), match_set) for g in surfaces)

    n_gold = len(gold_mentions)
    gliner_matched = ner_miss = edge_miss = ner_recovered = missed_recovered = missed = 0
    for i, (qid, surface, doc) in enumerate(gold_mentions):
        ms = _match_set(qid, surface)
        hit = _gliner_hit(doc, ms)
        gliner_matched += hit
        if node_of.get(i, -1) < 0:
            missed += 1
            if hit:
                missed_recovered += 1
            if _entity_exists(ms):
                edge_miss += 1
            else:
                ner_miss += 1
                if hit:
                    ner_recovered += 1

    # junk proxy: GLiNER surfaces (per doc) matching NO gold of that doc
    gold_by_doc: dict[str, list] = {}
    for (qid, surface, doc) in gold_mentions:
        gold_by_doc.setdefault(_base_doc_id(doc), []).append(_match_set(qid, surface))
    total_surf = junk = 0
    for doc, surfaces in gliner_by_doc.items():
        base = _base_doc_id(doc)
        golds = gold_by_doc.get(base, [])
        for g in surfaces:
            total_surf += 1
            g_lc = str(g).strip().lower()
            if not any(_alias_match_surface(g_lc, ms) for ms in golds):
                junk += 1

    def _frac(num, den):
        return num / den if den else 0.0

    return {
        "n_gold": n_gold,
        "n_missed": missed,
        "n_ner_miss": ner_miss,
        "n_edge_miss": edge_miss,
        "gliner_recall": _frac(gliner_matched, n_gold),
        "llm_coverage": _frac(sum(1 for v in node_of.values() if v >= 0), n_gold),
        "ner_recovered_frac": _frac(ner_recovered, ner_miss),
        "residual_recovered_frac": _frac(missed_recovered, missed),
        "junk_rate": _frac(junk, total_surf),
    }
```

- [ ] **Step 4: Point the aligner at the shared primitive (no-drift, low-risk).** In `_assign_real_nodes_aliased`, replace the inline substring fallback:

```python
        if best is None:                                           # substring fallback (parity with surface
            for n in cands:                                        # aligner): any alias/surface term overlaps
                if any(m and any(m in sn or sn in m for sn in id2surf.get(n, ())) for m in match):
                    best = n
                    break
```
with:
```python
        if best is None:                                           # substring fallback via the shared primitive
            for n in cands:
                if any(_alias_match_surface(sn, match) for sn in id2surf.get(n, ())):
                    best = n
                    break
```
(`_alias_match_surface` also matches exact members, a superset of the old substring-only test — but exact members were already caught by the intersection loop above, so behavior is unchanged for the aligner.)

- [ ] **Step 5: Run tests, verify pass** (box-safe) — the 5 new tests AND the existing `test_substrate_eval.py` suite (regression on the aligner refactor). Then `ruff check erkgbench/substrate_eval.py`.

- [ ] **Step 6: Commit.**

```bash
git add erkgbench/substrate_eval.py tests/test_substrate_eval.py
git commit -m "feat(erkgbench): gliner_probe_report pure scorer (NER-miss vs edge-miss split) + shared alias-match primitive"
```

---

## Task 2: runner `_wiki_build` + `run_wiki_gliner_probe` + `--gliner-probe`

**Files:**
- Modify: `erkgbench/run_substrate_eval.py`

No box unit test (needs the native store + LLM + GLiNER). Verified by `ruff`, `py_compile`, and the Modal run in Task 3. Keep the diff mechanical.

- [ ] **Step 1: Factor the build.** Replace the body of `run_wiki` so the corpus load + graph build live in a shared helper:

```python
def _wiki_build():
    """Load the wiki corpus and build the graph with the current env config. Returns
    (documents, gold, qid_aliases, graph) so both run_wiki and the GLiNER probe reuse it."""
    from erkgbench.qa_e2e.wiki_corpus import load_wiki_corpus
    documents, gold, qid_aliases = load_wiki_corpus()
    graph = _build_graph_from_documents(documents)
    return documents, gold, qid_aliases, graph


def run_wiki() -> dict:
    """Level 2: build over REAL Wikipedia prose; align gold to nodes by alias+doc; score R(B)/P(B)+coverage."""
    from erkgbench import metrics
    documents, gold, qid_aliases, graph = _wiki_build()
    clustering = substrate_eval.align_real_mentions_to_nodes_aliased(graph, gold, qid_aliases)
    coverage = substrate_eval.real_alignment_coverage_aliased(graph, gold, qid_aliases)
    b = metrics.score([m[0] for m in gold], clustering)
    coh = substrate_eval.graph_coherence(graph)
    return {"er_r_b": b.recall, "er_p_b": b.precision, "er_f1_b": b.f1, "coverage": coverage,
            "n_docs": len(documents), "n_gold": len(gold), "components": coh["components"]}
```

- [ ] **Step 2: Add the probe runner** (after `run_wiki`):

```python
def _gliner_by_doc(documents, *, threshold: float) -> dict:
    """Run GLiNER per-doc (whole lead), returning {base_doc_id: set(entity surfaces)}. GLiNER loads once."""
    from erkgbench.substrate_eval import _base_doc_id
    from goldengraph.extract_local import gliner_extractor
    extractor = gliner_extractor(threshold=threshold)
    out: dict[str, set[str]] = {}
    for d in documents:
        ex = extractor(d.text)
        out[_base_doc_id(d.id)] = {m.name for m in ex.mentions}
    return out


def run_wiki_gliner_probe() -> dict:
    """GLiNER entity-recall probe: build best-config graph, run GLiNER per-doc, report NER-addressable
    recovery of the residual. Threshold from GOLDENGRAPH_GLINER_THRESHOLD (default 0.4)."""
    documents, gold, qid_aliases, graph = _wiki_build()
    threshold = float(os.environ.get("GOLDENGRAPH_GLINER_THRESHOLD", "0.4") or "0.4")
    try:
        gbd = _gliner_by_doc(documents, threshold=threshold)
    except Exception as e:  # noqa: BLE001 -- fail-soft: still report the LLM baseline
        print(f"[gliner-probe] GLiNER failed ({e!r}); reporting empty gliner_by_doc", flush=True)
        gbd = {}
    r = substrate_eval.gliner_probe_report(graph, gold, qid_aliases, gbd)
    r.update(n_docs=len(documents), threshold=threshold)
    return r
```

- [ ] **Step 3: Route it in `main`.** Add the flag and branch:

```python
    ap.add_argument("--gliner-probe", action="store_true",
                    help="run the GLiNER entity-recall probe instead of the plain wiki eval")
    args = ap.parse_args()

    _probe = args.gliner_probe or os.environ.get("GOLDENGRAPH_GLINER_PROBE", "") not in ("", "0", "false")
    if args.corpus == "wiki" and _probe:
        r = run_wiki_gliner_probe()
        print(
            f"[gliner-probe] thr={r['threshold']} gliner_recall={r['gliner_recall']:.4f} "
            f"llm_coverage={r['llm_coverage']:.4f} n_missed={r['n_missed']} "
            f"ner_miss={r['n_ner_miss']} edge_miss={r['n_edge_miss']} "
            f"NER_recovered={r['ner_recovered_frac']:.4f} residual_recovered={r['residual_recovered_frac']:.4f} "
            f"junk_rate={r['junk_rate']:.4f}",
            flush=True,
        )
        md = (
            "# GLiNER Entity-Recall Probe (wiki)\n\n"
            "| threshold | gliner_recall | llm_coverage | n_missed | ner_miss | edge_miss | "
            "NER_recovered | residual_recovered | junk_rate |\n"
            "|---|---|---|---|---|---|---|---|---|\n"
            f"| {r['threshold']} | {r['gliner_recall']:.4f} | {r['llm_coverage']:.4f} | {r['n_missed']} | "
            f"{r['n_ner_miss']} | {r['n_edge_miss']} | {r['ner_recovered_frac']:.4f} | "
            f"{r['residual_recovered_frac']:.4f} | {r['junk_rate']:.4f} |\n\n"
            "NER_recovered = of the NER-miss gold (entity absent from the graph), share GLiNER surfaces. "
            "residual_recovered conflates NER-miss + edge-miss (context only). junk_rate is inflated by "
            "wikilink-only gold.\n"
        )
        with open(args.out_md, "w", encoding="utf-8") as fh:
            fh.write(md)
        print("\n" + md, flush=True)
        return
```
(place this block BEFORE the existing `if args.corpus == "wiki":` plain-eval branch so the probe wins when requested).

- [ ] **Step 4: Verify** — `ruff check erkgbench/run_substrate_eval.py` and `"$PY" -m py_compile erkgbench/run_substrate_eval.py`. (No import/pytest — the module pulls goldengraph.)

- [ ] **Step 5: Commit.**

```bash
git add erkgbench/run_substrate_eval.py
git commit -m "feat(erkgbench): --gliner-probe runner (per-doc GLiNER + _wiki_build helper)"
```

---

## Task 3: Modal image + measurement + verdict

**Files:**
- Modify: `scripts/distill/modal_bench.py`
- Create: `docs/superpowers/reports/2026-07-01-gliner-recall-probe-verdict.md`

- [ ] **Step 1: Add `gliner` to the image.** In `modal_bench.py`, extend the `pip_install(...)` list (currently `"maturin", "goldenmatch", "datasets", "openai", "numpy", "pytest", "lightrag-hku>=1.5,<1.6"`) with `"gliner"`. Commit:

```bash
git add scripts/distill/modal_bench.py
git commit -m "chore(bench): add gliner to the gg-bench Modal image for the entity-recall probe"
```

- [ ] **Step 2: Fire the probe** (2 threshold points; run yourself, `PYTHONIOENCODING=utf-8 PYTHONUTF8=1`, `--detach --spawn`, distinct `--n`). The probe needs the same best-config env as leg 83 PLUS the probe switch:

```bash
M="/d/show_case/goldenmatch/.venv/Scripts/modal.exe"
BEST=$'GOLDENGRAPH_SUBSTRATE_CORPUS=wiki\nGOLDENGRAPH_XDOC_KEY=name_ci\nGOLDENGRAPH_CHUNK_EXTRACT=1\nGOLDENGRAPH_CHUNK_SENTENCES=6\nGOLDENGRAPH_CHUNK_OVERLAP=2\nGOLDENGRAPH_GLINER_PROBE=1'
# threshold 0.4 (default)
$M run --detach scripts/distill/modal_bench.py --engine goldengraph --eval substrate --n 90 --opts "$BEST" --spawn
# threshold 0.3 (recall-friendlier second point)
$M run --detach scripts/distill/modal_bench.py --engine goldengraph --eval substrate --n 91 \
  --opts "$BEST"$'\nGOLDENGRAPH_GLINER_THRESHOLD=0.3' --spawn
```
Poll `gg-bench-cache` for `results/substrate_9{0,1}_*.md`; read the `[gliner-probe]` line.

> Note: the modal substrate branch runs `run_substrate_eval --corpus wiki`; `GOLDENGRAPH_GLINER_PROBE=1` (set via `--opts`) routes it to the probe. If the flag doesn't thread through the subprocess env, fall back to appending `--gliner-probe` to the substrate subprocess argv in `modal_bench.py` (one line) and re-run.

- [ ] **Step 3: Write the verdict** `docs/superpowers/reports/2026-07-01-gliner-recall-probe-verdict.md`: the two-threshold table, the NER-miss/edge-miss split, and the gate call against the spec's bar (`ner_recovered_frac ≳ 0.25` at tolerable junk). Include the caveats: junk_rate inflated by wikilink-only gold, PASS = necessity not sufficiency, GLiNER seq-length truncation (if REFUTED, note the chunked-GLiNER sanity pass as the pre-belief check).

- [ ] **Step 4: Commit.**

```bash
git add docs/superpowers/reports/2026-07-01-gliner-recall-probe-verdict.md
git commit -m "docs(goldengraph): GLiNER entity-recall probe verdict (wiki)"
```

---

## Completion

Use superpowers:finishing-a-development-branch: run the box-safe `tests/test_substrate_eval.py` suite, then open a PR (base `main`) and arm auto-merge. This is measurement-only — the PR ships the probe tooling regardless of the verdict. If PASS, the verdict hands off to a new GLiNER-hybrid spec (GLiNER entities → LLM relation prompt); if REFUTED, the extraction-recall thread closes at chunking and the arc moves to cross-corpus robustness of the name_ci + chunking stack.
