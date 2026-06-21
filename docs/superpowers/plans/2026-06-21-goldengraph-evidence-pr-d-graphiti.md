# GoldenGraph Evidence #1 / PR D — Graphiti competitor adapter Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Add a real Graphiti engine to the qa-e2e harness — the temporally-aware, LLM-extraction competitor (nondeterministic, needs a graph DB), so the head-to-head includes it.

**Architecture:** A `GraphitiQAEngine` implementing the `QAEngine` protocol over Graphiti's real async API (`add_episode` → `search`), driven via `asyncio.run`. **Graphiti has no embedded backend — it requires Neo4j or FalkorDB.** We use **FalkorDB** (Redis-based, a single lightweight container) as a CI **service container**. Graphiti `search` returns retrieved facts/edges, not a synthesized answer, so the adapter adds a small **LLM synthesis step** over the retrieved facts (the cost seam wraps both Graphiti's internal extraction LLM and the adapter's synthesis LLM).

**Tech Stack:** `graphiti-core` (pinned), FalkorDB service container, `asyncio`, the existing `erkgbench/qa_e2e` harness, OpenAI (via Graphiti's `LLMClient` + the synthesis call).

**Prerequisite:** PR A (#1156, merged). Independent of PRs B/C.

**Confirmed API (Context7 `/getzep/graphiti`):**
- `from graphiti_core import Graphiti`; `Graphiti(graph_driver=FalkorDriver(host, port))` (from `graphiti_core.driver.falkordb_driver`) OR `Graphiti(neo4j_uri, user, password)`.
- `await client.add_episode(name=, episode_body=, source_description=, reference_time=<datetime>, source=EpisodeType.text)` — ingest one document.
- `await client.search(query) -> list[EntityEdge]` — retrieve facts.
- LLM/embedder are configured on the client (OpenAI by default via `OPENAI_API_KEY`); a custom `LLMClient` is injectable for the cost seam.
- **CONFIRM during impl:** the index-setup method name (`build_indices_and_constraints`), the exact `search` return shape (`.fact`/`.episodes`), and the `LLMClient` injection point + signature.

**Design obstacles (load-bearing — surfaced from research):**
1. **Graph DB required.** Real e2e cannot run without a Neo4j/FalkorDB server. → FalkorDB service container in the opt-in lane; no DB means no e2e (the CI smoke is DB-free, see below).
2. **Search ≠ answer.** Graphiti returns facts; the adapter synthesizes an answer from the top facts via one LLM call. Keep the synthesis prompt minimal + shared across engines for fairness (a tiny `synthesize_from_facts(question, facts, llm)` helper).
3. **Nondeterministic.** Graphiti's extraction is LLM-driven; runs vary. This is expected (it's the determinism gap goldengraph exploits in slice #2). Record it; don't chase flakiness.
4. **Cost seam spans two places** — Graphiti-internal extraction calls + the adapter's synthesis call. Wrap Graphiti's `LLMClient` with a counting decorator AND count the synthesis call into the same counter.

---

## File Structure

| File | Responsibility |
|---|---|
| `erkgbench/qa_e2e/engines/graphiti.py` | `GraphitiQAEngine` + counting LLMClient wrapper + `synthesize_from_facts`. |
| `tests/test_qa_graphiti_smoke.py` | Protocol conformance + cost-seam test (DB-free, no LLM). |
| `erkgbench/qa_e2e/run_qa_e2e.py` | Add `graphiti` to `_build_engine` (reads `FALKORDB_HOST`/`PORT` env). |
| `.github/workflows/bench-graphiti-smoke.yml` | Isolated `graphiti-core` venv; DB-free protocol/cost smoke. |
| `.github/workflows/bench-graphrag-qa.yml` | Add a `graphiti` job WITH a `falkordb` service container. |

---

## Task 1: Graphiti adapter (DB-free testable seam + real path)

**Files:** Create `erkgbench/qa_e2e/engines/graphiti.py`; Test `tests/test_qa_graphiti_smoke.py`.

- [ ] **Step 1: Failing test** (protocol + cost seam + the synthesis helper — NO DB, NO real LLM)

```python
# tests/test_qa_graphiti_smoke.py
"""Protocol conformance + cost-seam + fact-synthesis helper, all DB-free and
LLM-free. The real e2e (add_episode -> search against a live FalkorDB) is the
opt-in bench-graphrag-qa lane (it needs a graph DB)."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

pytest.importorskip("graphiti_core")

from erkgbench.qa_e2e.engines.graphiti import (  # noqa: E402
    GraphitiQAEngine,
    make_counting_llm_client,
    synthesize_from_facts,
)
from erkgbench.qa_e2e.harness import QAEngine  # noqa: E402


def test_graphiti_engine_conforms_to_protocol():
    eng = GraphitiQAEngine(falkordb_host="localhost", falkordb_port=6379)
    assert isinstance(eng, QAEngine)
    assert eng.name == "graphiti"
    assert eng.fidelity == "real-e2e"


def test_synthesize_from_facts_calls_llm_and_counts():
    counter = {"in": 0, "out": 0}

    async def _stub(prompt, **kwargs):
        return "Ada"

    text = asyncio.run(
        synthesize_from_facts("Who founded Acme?", ["Acme founded by Ada"], _stub, counter)
    )
    assert text == "Ada"
    assert counter["in"] > 0 and counter["out"] > 0
```

- [ ] **Step 2: run -> fail** (`pytest tests/test_qa_graphiti_smoke.py` in the graphiti venv).

- [ ] **Step 3: Implement** `engines/graphiti.py`:
  - `make_counting_llm_client(inner_llm_client, counter)` — a thin `LLMClient` subclass/wrapper that counts tokens on each generate call and delegates (CONFIRM the `LLMClient` abstract method name, likely `generate_response`).
  - `synthesize_from_facts(question, facts, llm_callable, counter) -> str` — one LLM call combining the question + top facts into an answer; counts tokens.
  - `GraphitiQAEngine(*, falkordb_host, falkordb_port, llm_client=None)`:
    - `build_kg(corpus)`: construct `Graphiti(graph_driver=FalkorDriver(host, port))` with the counting LLM client; `await build_indices_and_constraints()`; `await add_episode(...)` per doc; handle = `{client}`.
    - `answer(handle, question)`: `facts = await client.search(question)`; `text = await synthesize_from_facts(question, [f.fact for f in facts[:5]], ...)`; `retrieved_fact_ids=()` (fact uuids available but not aligned to corpus ids); count tokens.
  - All async bodies driven via `asyncio.run`.

- [ ] **Step 4: run -> pass** (2 tests, DB-free). **Step 5: commit** `feat(qa-e2e): Graphiti engine adapter + DB-free smoke`.

---

## Task 2: Wire `graphiti` into the CLI

- [ ] Add to `run_qa_e2e._build_engine`: `if name == "graphiti": from .engines.graphiti import GraphitiQAEngine; return GraphitiQAEngine(falkordb_host=os.environ.get("FALKORDB_HOST","localhost"), falkordb_port=int(os.environ.get("FALKORDB_PORT","6379")))`. py_compile + ruff. Commit.

---

## Task 3: CI — DB-free smoke lane + real lane with a FalkorDB service

- [ ] **`bench-graphiti-smoke.yml`** (`push` on the adapter path; `ubuntu-latest`, no DB): `pip install graphiti-core goldenmatch pytest numpy`; run `tests/test_qa_graphiti_smoke.py`.
- [ ] **`bench-graphrag-qa.yml`** — add a `graphiti` job WITH a service container:

```yaml
  graphiti:
    runs-on: large-new-64GB
    timeout-minutes: 60
    services:
      falkordb:
        image: falkordb/falkordb:latest
        ports: ["6379:6379"]
    steps:
      - uses: actions/checkout@df4cb1c069e1874edd31b4311f1884172cec0e10  # v6
      - uses: actions/setup-python@a309ff8b426b58ec0e2a45f0f869d46889d02405  # v6.2.0
        with: { python-version: "3.12" }
      - run: python -m pip install --upgrade pip pytest numpy graphiti-core goldenmatch datasets
      - name: Run Graphiti end-to-end (real LLM + FalkorDB, budget-capped)
        working-directory: packages/python/goldenmatch/benchmarks/er-kg-bench
        env:
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
          FALKORDB_HOST: localhost
          FALKORDB_PORT: "6379"
          POLARS_SKIP_CPU_CHECK: "1"
        run: |
          python -m erkgbench.qa_e2e.run_qa_e2e --engine graphiti \
            --corpus "${{ inputs.corpus }}" --max-questions "${{ inputs.max_questions }}" \
            --budget-usd "${{ inputs.budget_usd }}" \
            --out-md results/RESULTS_QA_E2E_graphiti.md --out-json results/results_qa_e2e_graphiti.json
      - uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a  # v4
        with: { name: graphrag-qa-results-graphiti, path: "packages/python/goldenmatch/benchmarks/er-kg-bench/results/results_qa_e2e_graphiti*.*", if-no-files-found: ignore }
```

- [ ] Validate YAML; commit `ci(qa-e2e): Graphiti smoke lane + FalkorDB-backed real job`.

---

## Done criteria
- `GraphitiQAEngine` conforms; DB-free smoke (protocol + cost seam + synthesis helper) passes in the `graphiti-core` venv.
- `bench-graphrag-qa` `graphiti` job runs end-to-end against a FalkorDB service.
- Results note Graphiti's **nondeterminism** + `retrieved_fact_ids=()` (support-recall 0.0 by construction).

## Open questions
- Confirm Graphiti's index-setup method + `search` return shape + `LLMClient` method to wrap (the three CONFIRM items above) — do this first during impl by reading `graphiti_core`.
- FalkorDB vs Neo4j: FalkorDB is the lighter CI service; if `graphiti-core` pins a Neo4j-only feature, fall back to a `neo4j` service container.
