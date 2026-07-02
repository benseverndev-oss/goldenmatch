# Presence-Aligner Probe Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A pure `presence_aligner_report` scorer (strict edge-based vs relaxed global-surface alignment on the same graph) + a `--presence-probe` runner, to measure how much of the ~51% coverage ceiling is recoverable and at what precision cost.

**Architecture:** Add `_assign_real_nodes_presence` (strict, then global surface fallback reaching edgeless nodes) and `presence_aligner_report` (coverage/R(B)/P(B) both ways via the pure `metrics.score`) to `substrate_eval.py`; wire a `--presence-probe` branch into `run_substrate_eval` mirroring `--gliner-probe`. Measurement-only; the shipped strict path and metric are untouched.

**Tech Stack:** Python 3.11, pure stdlib + `erkgbench.metrics` (pure). pytest with hand-built graphs (no LLM/network).

**Spec:** `docs/superpowers/specs/2026-07-02-presence-aligner-probe-design.md`
**Branch:** `feat/presence-aligner-probe` (off `main`).

**Box-safe test invocation** (the scorer is pure — no goldengraph/native import triggered):
```bash
PY="/d/show_case/goldenmatch/.venv/Scripts/python.exe"
BENCH="D:/show_case/gg-local-llm/packages/python/goldenmatch/benchmarks/er-kg-bench"
cd "$BENCH"
PYTHONPATH="$BENCH" POLARS_SKIP_CPU_CHECK=1 "$PY" -m pytest tests/test_substrate_eval.py -q -p no:cacheprovider
```

## File structure

| File | Responsibility |
|---|---|
| `packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/substrate_eval.py` | **Modify.** Add `_assign_real_nodes_presence` + `presence_aligner_report`. Reuses `_assign_real_nodes_aliased` / `_alias_match_surface`. |
| `packages/python/goldenmatch/benchmarks/er-kg-bench/tests/test_substrate_eval.py` | **Modify.** 4 pure tests (edgeless recovered, collision→P drop, strict unchanged, degenerate). |
| `packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/run_substrate_eval.py` | **Modify.** Add `run_wiki_presence_probe()` + `--presence-probe` flag/env branch in `main`. |
| `docs/superpowers/reports/2026-07-02-presence-aligner-probe-verdict.md` | **Create** in Task 3 after the Modal run. |

---

## Task 1: pure `_assign_real_nodes_presence` + `presence_aligner_report`

**Files:**
- Modify: `erkgbench/substrate_eval.py`
- Test: `tests/test_substrate_eval.py`

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_substrate_eval.py`:

```python
from erkgbench.substrate_eval import presence_aligner_report


def _g(entities, edges):
    return {"entities": entities, "edges": edges}


def test_presence_recovers_edgeless_node():
    # node 1 "Apple" edged in docA; node 2 "Tim Cook" is edgeless (no edge anywhere).
    entities = [
        {"entity_id": 1, "canonical_name": "Apple", "surface_names": ["Apple"], "typ": "org"},
        {"entity_id": 2, "canonical_name": "Tim Cook", "surface_names": ["Tim Cook"], "typ": "person"},
    ]
    edges = [{"subj": 1, "obj": 1, "predicate": "is", "source_refs": ["docA"]}]
    gold = [("Qa", "apple", "docA"), ("Qb", "tim cook", "docB")]
    aliases = {"Qa": ["apple"], "Qb": ["tim cook"]}
    r = presence_aligner_report(_g(entities, edges), gold, aliases)
    assert r["strict_coverage"] == 0.5      # only Qa (docA edge); Qb unreachable strictly
    assert r["relaxed_coverage"] == 1.0     # Qb recovered via global surface match to node 2


def test_presence_collision_shows_precision_drop():
    # one "Smith" node edged in docA; two DISTINCT-QID gold both surface "smith".
    entities = [{"entity_id": 1, "canonical_name": "Smith", "surface_names": ["Smith"], "typ": "person"}]
    edges = [{"subj": 1, "obj": 1, "predicate": "x", "source_refs": ["docA"]}]
    gold = [("Qa", "smith", "docA"), ("Qb", "smith", "docB")]  # different entities, same surface
    aliases = {"Qa": ["smith"], "Qb": ["smith"]}
    r = presence_aligner_report(_g(entities, edges), gold, aliases)
    assert r["strict_pb"] == 1.0            # strict: only Qa aligns -> no false pair
    assert r["relaxed_pb"] < 1.0            # relaxed: Qb collides onto node 1 -> false pair


def test_presence_strict_matches_shipped_aligner():
    from erkgbench import metrics
    from erkgbench.substrate_eval import (
        align_real_mentions_to_nodes_aliased,
        real_alignment_coverage_aliased,
    )
    entities = [
        {"entity_id": 1, "canonical_name": "Apple", "surface_names": ["Apple"], "typ": "org"},
        {"entity_id": 2, "canonical_name": "Google", "surface_names": ["Google"], "typ": "org"},
    ]
    edges = [
        {"subj": 1, "obj": 2, "predicate": "rivals", "source_refs": ["docA"]},
        {"subj": 1, "obj": 2, "predicate": "rivals", "source_refs": ["docB"]},
    ]
    gold = [("Qa", "apple", "docA"), ("Qg", "google", "docA"), ("Qa", "apple", "docB")]
    aliases = {"Qa": ["apple"], "Qg": ["google"]}
    graph = _g(entities, edges)
    r = presence_aligner_report(graph, gold, aliases)
    assert r["strict_coverage"] == real_alignment_coverage_aliased(graph, gold, aliases)
    clustering = align_real_mentions_to_nodes_aliased(graph, gold, aliases)
    sb = metrics.score([m[0] for m in gold], clustering)
    assert r["strict_pb"] == sb.precision and r["strict_rb"] == sb.recall


def test_presence_degenerate_guards():
    r0 = presence_aligner_report(_g([], []), [], {})
    assert r0["n_gold"] == 0                # empty gold, no crash
    # empty graph but non-empty gold -> nothing to align, strict and relaxed both 0 coverage
    r1 = presence_aligner_report(_g([], []), [("Qa", "apple", "d1")], {"Qa": ["apple"]})
    assert r1["strict_coverage"] == 0.0 and r1["relaxed_coverage"] == 0.0
```

- [ ] **Step 2: Run, verify fail.** Box-safe invocation. Expected: `ImportError: cannot import name 'presence_aligner_report'`.

- [ ] **Step 3: Implement in `substrate_eval.py`** (append near `gliner_probe_report`):

```python
def _assign_real_nodes_presence(graph: dict, gold_mentions, qid_aliases) -> dict[int, int]:
    """Strict edge-based assignment (`_assign_real_nodes_aliased`), then for each gold left unaligned
    (node_of < 0) a GLOBAL surface/alias match to ANY node -- reaching edgeless nodes the doc-keyed
    strict path cannot. Exact set-intersection largest-wins, then substring fallback (shared
    primitive), lowest node id on tie. Still-unmatched keep their unique negatives."""
    node_of = dict(_assign_real_nodes_aliased(graph, gold_mentions, qid_aliases))
    id2surf: dict[int, set[str]] = {}
    for e in graph.get("entities", ()):
        surfs = {str(s).strip().lower() for s in e.get("surface_names", ()) if s}
        cn = str(e.get("canonical_name", "")).strip().lower()
        if cn:
            surfs.add(cn)
        id2surf[e.get("entity_id")] = surfs
    all_ids = sorted(id2surf)
    for i, (qid, surface, _doc) in enumerate(gold_mentions):
        if node_of.get(i, -1) >= 0:
            continue
        match = set(qid_aliases.get(qid, ())) | {str(surface).strip().lower()}
        best, best_ov = None, 0
        for nid in all_ids:                                    # exact set-intersection: largest wins
            ov = len(id2surf[nid] & match)
            if ov > best_ov:
                best, best_ov = nid, ov
        if best is None:                                       # substring fallback via shared primitive
            for nid in all_ids:
                if any(_alias_match_surface(s, match) for s in id2surf[nid]):
                    best = nid
                    break
        if best is not None:
            node_of[i] = best
    return node_of


def presence_aligner_report(graph: dict, gold_mentions, qid_aliases) -> dict:
    """Strict (edge-based) vs relaxed (global surface fallback reaching edgeless nodes) alignment on
    the SAME graph: coverage / R(B) / P(B) both ways. The delta quantifies the coverage ceiling and
    its precision cost -- a metric-side diagnostic, NOT a change to the shipped strict path. Pure
    (graph dict + the pure `metrics.score`)."""
    from erkgbench import metrics

    def _cluster(node_of: dict[int, int]) -> list[list[int]]:
        groups: dict[int, list[int]] = {}
        for i, n in node_of.items():
            groups.setdefault(n, []).append(i)
        return [sorted(v) for v in groups.values()]

    def _cov(node_of: dict[int, int]) -> float:
        return sum(1 for n in node_of.values() if n >= 0) / len(node_of) if node_of else 1.0

    qids = [m[0] for m in gold_mentions]
    strict = _assign_real_nodes_aliased(graph, gold_mentions, qid_aliases)
    relaxed = _assign_real_nodes_presence(graph, gold_mentions, qid_aliases)
    sb = metrics.score(qids, _cluster(strict))
    rb = metrics.score(qids, _cluster(relaxed))
    return {
        "n_gold": len(gold_mentions),
        "strict_coverage": _cov(strict), "relaxed_coverage": _cov(relaxed),
        "strict_pb": sb.precision, "relaxed_pb": rb.precision,
        "strict_rb": sb.recall, "relaxed_rb": rb.recall,
        "strict_fb": sb.f1, "relaxed_fb": rb.f1,
    }
```

- [ ] **Step 4: Run tests, verify pass** (box-safe) — the 4 new tests AND the existing `test_substrate_eval.py` suite (regression). Then `ruff check erkgbench/substrate_eval.py`.

- [ ] **Step 5: Commit.**

```bash
git add erkgbench/substrate_eval.py tests/test_substrate_eval.py
git commit -m "feat(erkgbench): presence_aligner_report -- strict vs relaxed (global-surface) alignment diagnostic"
```

---

## Task 2: `--presence-probe` runner

**Files:**
- Modify: `erkgbench/run_substrate_eval.py`

No box unit test (needs the native store + LLM build). Verified by `ruff` + `py_compile` + the Modal run in Task 3.

- [ ] **Step 1: Add the probe runner** (after `run_wiki_gliner_probe`, ~line 120):

```python
def run_wiki_presence_probe() -> dict:
    """Presence-aligner diagnostic: build the best-config graph, report strict vs relaxed alignment
    (coverage / R(B) / P(B)). Quantifies how much of the coverage ceiling is edgeless-but-present."""
    _documents, gold, qid_aliases, graph = _wiki_build()
    r = substrate_eval.presence_aligner_report(graph, gold, qid_aliases)
    r.update(n_docs=len(_documents))
    return r
```

- [ ] **Step 2: Route it in `main`.** Add the flag beside `--gliner-probe` (before `args = ap.parse_args()`):

```python
    ap.add_argument("--presence-probe", action="store_true",
                    help="run the strict-vs-relaxed presence-aligner diagnostic")
```
Add the branch immediately AFTER the existing `if args.corpus == "wiki" and _probe:` gliner block and BEFORE the plain `if args.corpus == "wiki":` branch:

```python
    _presence = (args.presence_probe
                 or os.environ.get("GOLDENGRAPH_PRESENCE_PROBE", "") not in ("", "0", "false"))
    if args.corpus == "wiki" and _presence:
        r = run_wiki_presence_probe()
        print(
            f"[presence-probe] strict_cov={r['strict_coverage']:.4f} relaxed_cov={r['relaxed_coverage']:.4f} "
            f"strict_pb={r['strict_pb']:.4f} relaxed_pb={r['relaxed_pb']:.4f} "
            f"strict_rb={r['strict_rb']:.4f} relaxed_rb={r['relaxed_rb']:.4f} "
            f"strict_fb={r['strict_fb']:.4f} relaxed_fb={r['relaxed_fb']:.4f}",
            flush=True,
        )
        md = (
            "# Presence-Aligner Probe (wiki)\n\n"
            "| axis | strict (edge) | relaxed (global surface) |\n"
            "|---|---|---|\n"
            f"| coverage | {r['strict_coverage']:.4f} | {r['relaxed_coverage']:.4f} |\n"
            f"| R(B) | {r['strict_rb']:.4f} | {r['relaxed_rb']:.4f} |\n"
            f"| P(B) | {r['strict_pb']:.4f} | {r['relaxed_pb']:.4f} |\n"
            f"| F1(B) | {r['strict_fb']:.4f} | {r['relaxed_fb']:.4f} |\n\n"
            "relaxed reaches edgeless-but-present nodes globally (any doc). A P(B) drop means "
            "more-aligned-with-some-collisions, not pure error -- the two P columns are over different "
            "pair populations.\n"
        )
        with open(args.out_md, "w", encoding="utf-8") as fh:
            fh.write(md)
        print("\n" + md, flush=True)
        return
```

> Note: place the `_presence` block so the gliner branch (its own flag) still wins when `--gliner-probe` is set; the two flags are independent. If neither probe flag is set, control falls through to the plain `if args.corpus == "wiki":` eval unchanged.

- [ ] **Step 3: Verify** — `ruff check erkgbench/run_substrate_eval.py` and `"$PY" -m py_compile erkgbench/run_substrate_eval.py`.

- [ ] **Step 4: Commit.**

```bash
git add erkgbench/run_substrate_eval.py
git commit -m "feat(erkgbench): --presence-probe runner (strict-vs-relaxed coverage diagnostic)"
```

---

## Task 3: Modal measurement + verdict

**Files:** Create `docs/superpowers/reports/2026-07-02-presence-aligner-probe-verdict.md`.

Run yourself (Modal, `PYTHONIOENCODING=utf-8 PYTHONUTF8=1`, `--detach --spawn`, distinct `--n`). Rig: best config (`name_ci` + chunking `(6,2)`), SCHEMA_CANON off.

- [ ] **Step 1: Fire two probe legs** (7B seeded + V3):

```bash
M="/d/show_case/goldenmatch/.venv/Scripts/modal.exe"
BEST=$'GOLDENGRAPH_SUBSTRATE_CORPUS=wiki\nGOLDENGRAPH_XDOC_KEY=name_ci\nGOLDENGRAPH_CHUNK_EXTRACT=1\nGOLDENGRAPH_CHUNK_SENTENCES=6\nGOLDENGRAPH_CHUNK_OVERLAP=2\nGOLDENGRAPH_PRESENCE_PROBE=1'
# 7B seeded (reproducible)
$M run --detach scripts/distill/modal_bench.py --engine goldengraph --eval substrate --n 150 \
  --opts "$BEST"$'\nGOLDENGRAPH_LLM_SEED=42' --spawn
# DeepSeek-V3 (ceiling graph)
$M run --detach scripts/distill/modal_bench.py --engine goldengraph --eval substrate --n 151 \
  --chat deepseek-chat --opts "$BEST" --spawn
```
Poll `gg-bench-cache` for `results/substrate_15{0,1}_*.md`; read the `[presence-probe]` line (`substrate_150_goldengraph-qwen2.5-7b-instruct.md`, `substrate_151_goldengraph-deepseek-chat.md`).

- [ ] **Step 2: Read both legs.** Tabulate strict vs relaxed coverage / R(B) / P(B) for each graph.

- [ ] **Step 3: Write the verdict** `docs/superpowers/reports/2026-07-02-presence-aligner-probe-verdict.md`, honoring the spec's verdict-wording guards (lower-bound is directional; "edgeless" = any-doc reach; the two P columns are over different pair populations):
  - **Metric artifact (→ build node provenance):** `relaxed_cov` → ~0.9-1.0 at `relaxed_pb` ~1.0.
  - **Real limit (→ ~0.5 honest):** `relaxed_pb` craters.
  - Report both graphs; note whether the read agrees across 7B and V3.

- [ ] **Step 4: Commit** the report.

```bash
git add docs/superpowers/reports/2026-07-02-presence-aligner-probe-verdict.md
git commit -m "docs(goldengraph): presence-aligner probe verdict (wiki, 7B + V3)"
```

---

## Completion

Use superpowers:finishing-a-development-branch: run the box-safe `tests/test_substrate_eval.py` suite, open a PR (base `main`), arm auto-merge. Measurement-only — the PR ships the diagnostic tooling regardless of verdict. If the relaxed path recovers coverage cleanly, the verdict hands off to a **node-provenance engine** spec (stamp `source_refs` on entity nodes: store + `build_batch` + query + per-doc aligner path). If precision craters, the verdict records that ~0.5 is a more honest limit and the arc turns to the gold definition.
