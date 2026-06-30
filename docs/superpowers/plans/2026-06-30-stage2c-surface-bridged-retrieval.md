# Stage-2-C: Surface-Bridged Retrieval Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an opt-in (default-off) `_retrieve_local_bridged` that bridges same-name under-merged siblings as the retrieval ball expands, so multi-hop answers stranded behind a split bridge-entity enter the subgraph — then validate on MuSiQue (ship-or-construction-ceiling-null).

**Architecture:** New iterative-frontier function in `goldengraph/answer.py` reusing the existing `_bridge_surfaces`; a one-line env gate at the single `_retrieve_local` call site in `ask`. Default off = byte-identical to today.

**Tech Stack:** Python (stdlib), pytest, the existing `scripts/distill/modal_bench.py --corpus musique` Modal harness.

**Spec:** `docs/superpowers/specs/2026-06-30-stage2c-surface-bridged-retrieval-design.md`
**Branch:** `feat/stage2c-retrieval-connectivity` (already created off `origin/main`).

---

## Environment notes (read before starting)

- **Box-safe pure tests** (no native, no LLM, no polars):
  ```bash
  cd packages/python/goldengraph
  PYTHONPATH="." GOLDENGRAPH_NATIVE=0 POLARS_SKIP_CPU_CHECK=1 "D:/show_case/goldenmatch/.venv/Scripts/python.exe" \
    -m pytest tests/test_retrieval_bridge.py tests/test_chain_retrieval.py -q -p no:cacheprovider
  ```
- **Do NOT run the whole suite locally** (OOM). `test_retrieval.py` needs `goldengraph_native` (absent on the box) — it is a CI/Modal check, NOT a local one. `ruff check` + `py_compile` before each commit. GitHub auth: `GH_TOKEN=$(gh auth token --user benzsevern)`.
- **Critical test-stub fact (do NOT "fix"):** `_StubGraph.query(ids, hops)` in `test_chain_retrieval.py` **ignores `hops`** and only ever does a 1-hop expansion from `ids`. That is intentional — it is what makes the plain ball strand at the sink-copy (assert-1). The fixture proves the BRIDGING mechanism (via the bridged function's own iteration), not the depth-growth of the real native `query`. Leave the stub alone.

## File structure

- **Modify:** `goldengraph/answer.py` — add `_retrieve_local_bridged` (after `_retrieve_local`) and `_bridge_enabled()` (near `_hybrid_filter_mode`); wire the gate into the one `_retrieve_local` call site in `ask` (line 287).
- **Create:** `tests/test_retrieval_bridge.py` — coupling + no-strand + budget + empty-seeds + gate tests.
- **Create (Task 3):** `docs/superpowers/reports/2026-06-30-stage2c-surface-bridged-retrieval.md`.

---

### Task 1: `_retrieve_local_bridged`

**Files:**
- Modify: `goldengraph/answer.py`
- Test: `tests/test_retrieval_bridge.py`

- [ ] **Step 1: Write failing tests** (create the file)

```python
# tests/test_retrieval_bridge.py
"""Surface-bridged retrieval (stage-2-C): the ball unions same-name under-merged siblings as it
expands, so an answer stranded behind a split bridge-entity becomes reachable. Pure (stub graph)."""
from __future__ import annotations

from goldengraph.answer import _retrieve_local, _retrieve_local_bridged
# Reuse the under-merge fixture + stub from the chain-retrieval tests (no tests/__init__.py, so the
# sibling module is importable on the pytest path -- same pattern as `from conftest import ...`).
from test_chain_retrieval import _StubGraph, _split_graph


def _names(ball):
    return {e["canonical_name"] for e in ball["entities"]}


def test_plain_ball_strands_at_under_merge():
    # plain retrieval seeded at A cannot reach C: id1 is a sink, id4 (same name 'B') is a different node
    ball = _retrieve_local(_split_graph(), [0], max_hops=4, node_budget=64)
    assert "C" not in _names(ball)


def test_bridged_ball_crosses_under_merge():
    # per-hop surface bridging unions B(id1)<->B(id4), so part_of->C enters the ball
    ball = _retrieve_local_bridged(_split_graph(), [0], max_hops=4, node_budget=64)
    assert "C" in _names(ball)


def _connected_graph():
    # A -acquired-> B -part_of-> C with a SINGLE 'B' node (no under-merge)
    ents = [{"entity_id": i, "canonical_name": n} for i, n in [(0, "A"), (1, "B"), (2, "C")]]
    edges = [{"subj": 0, "predicate": "acquired", "obj": 1},
             {"subj": 1, "predicate": "part_of", "obj": 2}]
    return _StubGraph(ents, edges)


def test_bridged_reaches_answer_on_connected_graph():
    # no under-merge to bridge -> bridging is a no-op for siblings, but the iteration still reaches C
    # (proves bridging doesn't break / loop on the easy case)
    ball = _retrieve_local_bridged(_connected_graph(), [0], max_hops=4, node_budget=64)
    assert "C" in _names(ball)


def test_node_budget_bounds_expansion():
    # budget=1 breaks AFTER the first hop, BEFORE the bridge hop -> C never enters
    ball = _retrieve_local_bridged(_split_graph(), [0], max_hops=4, node_budget=1)
    assert "C" not in _names(ball)


def test_empty_seeds_falls_back():
    ball = _retrieve_local_bridged(_split_graph(), [], max_hops=4, node_budget=64)
    assert ball["entities"] == [] and ball["edges"] == []
```

- [ ] **Step 2: Run, verify FAIL** (`_retrieve_local_bridged` undefined).

- [ ] **Step 3: Implement** — in `goldengraph/answer.py`, immediately after `_retrieve_local`:

```python
def _retrieve_local_bridged(slice_graph, seeds, *, max_hops: int, node_budget: int) -> dict:
    """Like `_retrieve_local`, but at each hop bridges the reached frontier across same-NAME
    under-merged siblings (the proven `trace_chain` mechanism), so an answer stranded behind a split
    bridge-entity (a sink-copy with no out-edge whose source-copy owns the next hop) enters the ball
    connected to the seeds. Opt-in via the `GOLDENGRAPH_RETRIEVAL_BRIDGE` gate; `node_budget` bounds the
    accumulation so a popular-name frontier cannot blow up the ball."""
    if not seeds:
        return slice_graph.query(seeds, max_hops)
    frontier = set(seeds)
    ents: dict = {}            # dedup by entity_id
    edges: list = []
    seen: set = set()          # dedup edges by (subj, predicate, obj)
    for _hop in range(max(max_hops, 1)):
        sub = slice_graph.query(list(frontier), 1)
        id_to_name = {e["entity_id"]: e["canonical_name"] for e in sub.get("entities", ())}
        for e in sub.get("entities", ()):
            ents.setdefault(e["entity_id"], e)
        for ed in sub.get("edges", ()):
            k = (ed["subj"], ed["predicate"], ed["obj"])
            if k not in seen:
                seen.add(k)
                edges.append(ed)
        if len(ents) >= node_budget:
            break
        # next frontier: the reached ids, BRIDGED across same-name siblings
        frontier = _bridge_surfaces(slice_graph, set(id_to_name), id_to_name)
    return {"entities": list(ents.values()), "edges": edges}
```

- [ ] **Step 4: Run, verify PASS** (5 tests).

```bash
PYTHONPATH="." GOLDENGRAPH_NATIVE=0 POLARS_SKIP_CPU_CHECK=1 "D:/show_case/goldenmatch/.venv/Scripts/python.exe" \
  -m pytest tests/test_retrieval_bridge.py -q -p no:cacheprovider
```
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add goldengraph/answer.py tests/test_retrieval_bridge.py
git commit -m "feat(goldengraph): _retrieve_local_bridged (surface-bridged retrieval ball)"
```

---

### Task 2: gate it into `ask`

**Files:**
- Modify: `goldengraph/answer.py`
- Test: `tests/test_retrieval_bridge.py`

- [ ] **Step 1: Write failing test** (append)

```python
def test_bridge_gate_env(monkeypatch):
    from goldengraph.answer import _bridge_enabled
    monkeypatch.delenv("GOLDENGRAPH_RETRIEVAL_BRIDGE", raising=False)
    assert _bridge_enabled() is False
    for on in ("1", "true", "yes"):
        monkeypatch.setenv("GOLDENGRAPH_RETRIEVAL_BRIDGE", on)
        assert _bridge_enabled() is True
    for off in ("0", "false", ""):
        monkeypatch.setenv("GOLDENGRAPH_RETRIEVAL_BRIDGE", off)
        assert _bridge_enabled() is False
```

- [ ] **Step 2: Run, verify FAIL** (`_bridge_enabled` undefined).

- [ ] **Step 3: Implement** — add the gate helper near `_hybrid_filter_mode` in `answer.py`:

```python
def _bridge_enabled() -> bool:
    """`GOLDENGRAPH_RETRIEVAL_BRIDGE` gate (default off). On -> the local/hybrid retrieval ball is
    built with `_retrieve_local_bridged` (same-name under-merge bridging) instead of `_retrieve_local`."""
    import os

    return os.environ.get("GOLDENGRAPH_RETRIEVAL_BRIDGE", "0") not in ("0", "false", "")
```

Wire the gate at the single call site (`answer.py` line 287). Replace:
```python
    subgraph = _retrieve_local(slice_graph, seeds, max_hops=hops, node_budget=node_budget)
```
with:
```python
    _retrieve = _retrieve_local_bridged if _bridge_enabled() else _retrieve_local
    subgraph = _retrieve(slice_graph, seeds, max_hops=hops, node_budget=node_budget)
```

(Both `mode="local"` and `mode="hybrid"` flow through this one line, so the gate covers both — matching the spec. No other call site changes.)

- [ ] **Step 4: Run, verify PASS** + the chain tests for no-regression:

```bash
PYTHONPATH="." GOLDENGRAPH_NATIVE=0 POLARS_SKIP_CPU_CHECK=1 "D:/show_case/goldenmatch/.venv/Scripts/python.exe" \
  -m pytest tests/test_retrieval_bridge.py tests/test_chain_retrieval.py -q -p no:cacheprovider
```
Expected: all pass (gate default-off → `ask`'s retrieval is the unchanged `_retrieve_local`).

- [ ] **Step 5: ruff + commit**

```bash
"D:/show_case/goldenmatch/.venv/Scripts/python.exe" -m ruff check goldengraph/answer.py tests/test_retrieval_bridge.py
git commit -am "feat(goldengraph): GOLDENGRAPH_RETRIEVAL_BRIDGE gate in ask (default off)"
```

---

### Task 3: N=20 MuSiQue validation → ship-or-null report

**Files:**
- Create: `docs/superpowers/reports/2026-06-30-stage2c-surface-bridged-retrieval.md`

A MEASUREMENT, not code. Detached Modal pattern.

- [ ] **Step 1: Push the branch**

```bash
GH_TOKEN=$(gh auth token --user benzsevern) git push -u origin feat/stage2c-retrieval-connectivity
```

- [ ] **Step 2: Fire the N=20 run (RETRIEVAL_BRIDGE=1)**

```bash
P="a99885f0-c5af-4ae1-9dc8-255cc60aa129"
export MODAL_TOKEN_ID=$(infisical.cmd secrets get MODAL_TOKEN_ID --projectId "$P" --env dev --plain --silent)
export MODAL_TOKEN_SECRET=$(infisical.cmd secrets get MODAL_TOKEN_SECRET --projectId "$P" --env dev --plain --silent)
M="D:/show_case/goldenmatch/.venv/Scripts/modal.exe"
PYTHONIOENCODING=utf-8 "$M" run --detach scripts/distill/modal_bench.py \
  --engine goldengraph --eval end_to_end --corpus musique --n 20 --spawn \
  --opts $'GOLDENGRAPH_QA_MODE=auto\nGOLDENGRAPH_RETRIEVAL_BRIDGE=1'
```
Result: `results/end_to_end_20_goldengraph-qwen2.5-7b-instruct-musique.md`. Poll with a Monitor. Bridging is retrieval-time + cheap, so this completes well under the cap (unlike cross-doc-link).

**Comparison baseline:** the bridge-OFF N=20 run is the matched control. The cross-doc-link N=20 run is NOT a clean control (different opts). If a clean bridge-OFF N=20 baseline isn't already on the volume, fire one (`--opts GOLDENGRAPH_QA_MODE=auto`, same seed/subset) so the A/B is on the identical N=20 questions.

- [ ] **Step 3: Aggregate** (same-component is the primary signal)

```bash
grep -iE "support_recall=|musique \| 0" /tmp/s2c.md | head -2
grep -oE "(EXTRACTION|RETRIEVAL-BROKEN-CHAIN|SYNTHESIS)" /tmp/s2c.md | sort | uniq -c | sort -rn
# SYNTHESIS same_component split (islands should DROP if bridging connects them)
awk '/SYNTHESIS \(retrieved/{i=1;next} i&&/same_component=/{if(/=True/)t++;else f++;i=0} END{print "True:",t+0," False:",f+0}' /tmp/s2c.md
```

- [ ] **Step 4: Write the verdict report** — `docs/superpowers/reports/2026-06-30-stage2c-surface-bridged-retrieval.md`:
  - Run config (N=20, model, RETRIEVAL_BRIDGE=1, date) + the matched bridge-OFF N=20 baseline.
  - Before/after: `same_component=False` count (the primary signal), `answer_match`, SYNTHESIS/RETRIEVAL buckets, `support_recall`.
  - **Verdict per the pre-committed gate:**
    - *islands drop + answer_match rises* → SUCCESS: ship default-off (opt-in lever); record the win; note a follow-up N=50 confirm (bridging is cheap enough to complete N=50).
    - *flat* → CONSTRUCTION-CEILING NULL: the disconnection is genuine (not name-based under-merge); real-corpus multi-hop is 7B-construction-bound; cheap levers (voting, linking, bridging) exhausted; the remaining fix is a bigger program (stronger extractor / hybrid-passage). State plainly, no tuning.
  - Confidence statement scaled to N=20.

- [ ] **Step 5: Commit the report**

```bash
git add docs/superpowers/reports/2026-06-30-stage2c-surface-bridged-retrieval.md
git commit -m "docs(stage-2c): surface-bridged retrieval validation verdict"
```

---

## Done criterion

- Tasks 1-2 merged behind green tests (new file + chain tests, no regression; default path byte-identical).
- A committed verdict report with the matched bridge-OFF vs bridge-ON N=20 comparison and a ship-or-construction-ceiling verdict per the pre-committed gate.
- Open a PR; arm auto-merge once CI is green. (Code lands regardless of the verdict — default-off; the report records whether the lever is worth enabling and, if null, closes the cheap-lever line for real-corpus multi-hop.)
