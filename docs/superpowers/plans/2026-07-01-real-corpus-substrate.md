# Real-Corpus (Wikidata/RxNorm/events) Substrate Validation — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Feed the engineered substrate generator **real** entities from `records.csv` (48 QID/rxcui/slug ids, real aliases, real types) via a `GOLDENGRAPH_BENCH_ENTITIES=real` gate, then run the existing substrate eval to calibrate whether the `name_ci` type-jitter fix holds on real entities and whether the baseline is less jitter-prone on real crisp types.

**Architecture:** One new loader + one gated line in `generate_engineered`; everything downstream (edges, rendering, gold, scoring) reused. Then a Modal calibration run (baseline vs `name_ci`, real entities) beside the engineered numbers.

**Tech Stack:** Python (er-kg-bench), pytest (box-safe pure), Modal for the run.

**Spec:** `docs/superpowers/specs/2026-07-01-real-corpus-substrate-design.md`

**Branch:** `feat/real-corpus-substrate` (already off current main; no rebase needed).

**Box-safe test invocation:**
```bash
PY="/d/show_case/goldenmatch/.venv/Scripts/python.exe"
cd packages/python/goldenmatch/benchmarks/er-kg-bench
PYTHONPATH="." POLARS_SKIP_CPU_CHECK=1 GOLDENGRAPH_NATIVE=0 "$PY" -m pytest <test> -q -p no:cacheprovider
```

---

## Task 1: `_load_real_entities()` + the `GOLDENGRAPH_BENCH_ENTITIES=real` gate

**Files:**
- Modify: `packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/qa_e2e/engineered.py`
- Test: `packages/python/goldenmatch/benchmarks/er-kg-bench/tests/test_real_entities.py`

- [ ] **Step 1: Write the failing test:**

```python
"""GOLDENGRAPH_BENCH_ENTITIES=real feeds the engineered generator real records.csv entities."""
from erkgbench.qa_e2e.engineered import (
    _load_real_entities,
    emit_gold_mentions,
    generate_engineered,
)


def test_load_real_entities_groups_by_entity_id_verbatim():
    ents = _load_real_entities()
    assert len(ents) == 48
    ids = {e.id for e in ents}
    # ids are verbatim across 3 sources -- a QID, an rxcui:, and a slug all survive (NOT Q-filtered)
    assert any(i.startswith("Q") for i in ids)
    assert any(i.startswith("rxcui:") for i in ids)
    assert any("-" in i for i in ids)          # event slug
    # Q37156 (IBM) carries real aliases; canonical is not also a variant
    ibm = next(e for e in ents if e.id == "Q37156")
    assert ibm.canonical
    assert len(ibm.variants) >= 2
    assert ibm.canonical not in ibm.variants


def test_real_gate_yields_real_id_gold(monkeypatch):
    monkeypatch.setenv("GOLDENGRAPH_BENCH_ENTITIES", "real")
    corpus = generate_engineered(seed=20260620, n_questions=1, ambiguity=0.0)
    gold_ids = {eid for eid, _s, _d in emit_gold_mentions(corpus.documents)}
    assert gold_ids
    assert all(i.startswith("Q") or i.startswith("rxcui:") or "-" in i for i in gold_ids)


def test_real_gate_off_by_default_uses_concepts(monkeypatch):
    monkeypatch.delenv("GOLDENGRAPH_BENCH_ENTITIES", raising=False)
    corpus = generate_engineered(seed=20260620, n_questions=1, ambiguity=0.0)
    gold_ids = {eid for eid, _s, _d in emit_gold_mentions(corpus.documents)}
    # concept ids are gm:* / Q* concept ids, never rxcui: -> proves the real source is NOT used
    assert not any(i.startswith("rxcui:") for i in gold_ids)
```

- [ ] **Step 2: Run, verify fail** (`ImportError: cannot import name '_load_real_entities'`).

- [ ] **Step 3: Implement.** In `engineered.py`, add the loader beside `_load_entities`:

```python
def _load_real_entities() -> list[_Entity]:
    """Real entities from dataset/records.csv (Wikidata / RxNorm / event reference data). Group rows by
    `entity_id` VERBATIM (a QID `Q37156`, an `rxcui:<n>`, or an event slug -- never assume a `Q` prefix,
    that would drop 24/48). canonical = the lowest-`record_id` mention (numeric sort); variants = the other
    distinct real aliases. The entity_id is the ground truth. Pure / no network (records.csv is committed)."""
    import csv

    bench_root = Path(__file__).resolve().parents[2]
    path = bench_root / "dataset" / "records.csv"
    by_id: dict[str, list[tuple[int, str]]] = {}
    with open(path, encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            eid = (row.get("entity_id") or "").strip()
            mention = (row.get("mention") or "").strip()
            if not eid or not mention:
                continue
            by_id.setdefault(eid, []).append((int(row["record_id"]), mention))
    entities: list[_Entity] = []
    for eid, rows in by_id.items():
        rows.sort(key=lambda t: t[0])          # numeric record_id (int, not lexical)
        canonical = rows[0][1]
        variants = tuple(dict.fromkeys(m for _rid, m in rows[1:] if m != canonical))
        entities.append(_Entity(id=eid, canonical=canonical, variants=variants))
    return entities
```

- [ ] **Step 4: Gate the entity source** in `generate_engineered`, replacing the sole `entities = _load_entities()` call:

```python
    import os as _os_src
    if _os_src.environ.get("GOLDENGRAPH_BENCH_ENTITIES", "").strip().lower() == "real":
        entities = _load_real_entities()
    else:
        entities = _load_entities()
```

- [ ] **Step 5: Run tests, verify pass** + `ruff check`.
- [ ] **Step 6: Commit** — `feat(er-kg-bench): real-entity source for the substrate generator (records.csv)`.

---

## Task 2: Modal calibration run + verdict report

**Files:**
- Create: `docs/superpowers/reports/2026-07-01-real-corpus-substrate-verdict.md`

- [ ] **Step 1: Fire two Modal legs** (detached+spawn, distinct `--n`, `PYTHONIOENCODING=utf-8 PYTHONUTF8=1`; full ambiguity sweep 0/0.3/0.6 to compare the whole curve vs engineered):

```bash
M="/d/show_case/goldenmatch/.venv/Scripts/modal.exe"
# baseline (name,typ) key on REAL entities
$M run --detach scripts/distill/modal_bench.py --engine goldengraph --eval substrate --n 50 \
  --opts $'GOLDENGRAPH_BENCH_ENTITIES=real' --spawn
# name_ci key on REAL entities
$M run --detach scripts/distill/modal_bench.py --engine goldengraph --eval substrate --n 51 \
  --opts $'GOLDENGRAPH_BENCH_ENTITIES=real\nGOLDENGRAPH_XDOC_KEY=name_ci' --spawn
```
Poll each `results/substrate_<n>_goldengraph-qwen2.5-7b-instruct.md` with a Monitor.

- [ ] **Step 2: Read the calibration** — the `[substrate]` R(B)/P(B)/edge_recall lines per ambiguity, both legs.
  - **Baseline R(B) on real entities vs engineered's 0.23** — is it higher (real crisp types jitter less)?
  - **name_ci vs baseline on real entities** — does the fix still help, and by how much?

- [ ] **Step 3: Write the verdict report** — the real-entity curve beside the engineered curve; state which of the three spec outcomes held (universal jitter / concept-corpus overstated / fix was an artifact). This is a **calibration**, not a pass/fail gate — report honestly whatever the numbers say.

- [ ] **Step 4: Commit** the report.

---

## Completion

Use superpowers:finishing-a-development-branch: verify box-safe tests pass, PR (base `main`), arm auto-merge. Deferred follow-ons stay out of scope (real-homograph `name_ci_type` validation; real Wikidata edges; real Wikipedia prose / level-2).
