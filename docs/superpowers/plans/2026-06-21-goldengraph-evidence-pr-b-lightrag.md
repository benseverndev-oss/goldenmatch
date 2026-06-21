# GoldenGraph Evidence #1 / PR B — LightRAG competitor adapter Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a real LightRAG engine to the qa-e2e harness so the head-to-head has its first competitor — the engine we most directly claim to beat on ER.

**Architecture:** A `LightRAGQAEngine` implementing the existing `QAEngine` protocol (`build_kg`/`answer`) over LightRAG's real async API (`ainsert` / `aquery`), driven from the sync protocol via `asyncio.run`. LightRAG owns its own LLM calls, so the cost seam is an injected **counting `llm_model_func`** (the LightRAG analogue of PR A's `_CountingLLM`). LightRAG's heavy, fast-moving deps conflict with the workspace, so it gets its **own isolated-venv CI lane** (the `goldenmatch-kg` pattern).

**Tech Stack:** Python 3.12, `lightrag-hku` (pinned), `asyncio`, the existing `erkgbench/qa_e2e` harness (lands on main with PR A / #1156), `goldenmatch.core.llm_budget.BudgetTracker` (already used by the harness).

**Prerequisite:** PR A (#1156) merged — this builds on `erkgbench/qa_e2e/{harness,corpora}.py` and `bench-graphrag-qa.yml`.

**Design decision — why the CI smoke does NOT run LightRAG end-to-end:** LightRAG's entity extraction expects the LLM to emit a specific delimited record format; a trivial stub LLM ("return Ada") produces no graph and the extraction parser is brittle on it. So:
- The **CI smoke** (isolated venv, no key) validates protocol conformance + the cost-counting `llm_model_func` wrapper in isolation (call it directly, assert token accounting) — deterministic, no real LLM.
- The **real end-to-end** validation (insert→query producing an answer) is the opt-in `bench-graphrag-qa` lane with a real LLM. This mirrors the spec's fidelity posture: `real-e2e` is exercised in the opt-in lane, not the required CI.

**LightRAG API reference (v1.4.x, confirmed via Context7 `/hkuds/lightrag`):**
- `rag = LightRAG(working_dir=, llm_model_func=, embedding_func=)`
- `await rag.initialize_storages()` then `await rag.ainsert(text)` then `await rag.aquery(q, param=QueryParam(mode="hybrid"))` then `await rag.finalize_storages()`
- `llm_model_func`: `async def f(prompt, system_prompt=None, history_messages=[], **kwargs) -> str`
- `embedding_func`: a `lightrag.utils.EmbeddingFunc(embedding_dim, max_token_size, func=...)`; real run uses `lightrag.llm.openai.openai_embed` + `gpt_4o_mini_complete`.

---

## File Structure

All paths under `packages/python/goldenmatch/benchmarks/er-kg-bench/` unless noted.

| File | Responsibility |
|---|---|
| `erkgbench/qa_e2e/engines/lightrag.py` | `LightRAGQAEngine` + `_CountingLLMFunc`. Lazy-imports `lightrag`. |
| `tests/test_qa_lightrag_smoke.py` | Protocol conformance + cost-seam unit test (no real LLM, no insert/query). |
| `erkgbench/qa_e2e/run_qa_e2e.py` | Add `lightrag` to `_build_engine`. |
| `.github/workflows/bench-graphrag-qa.yml` (repo root) | Add a `lightrag` job (isolated venv) to the opt-in lane. |
| `.github/workflows/goldengraph-pipeline.yml` OR a small new `bench-lightrag-smoke.yml` | Run the LightRAG smoke in an isolated `lightrag-hku` venv (key-free). |

---

## Task 1: LightRAG adapter + counting llm_model_func

**Files:**
- Create: `erkgbench/qa_e2e/engines/lightrag.py`
- Test: `tests/test_qa_lightrag_smoke.py`

- [ ] **Step 1: Write the failing test** (protocol + cost seam only; no insert/query)

```python
# tests/test_qa_lightrag_smoke.py
"""Validates the LightRAG adapter's protocol conformance + cost-counting seam
WITHOUT running LightRAG end-to-end (its extraction needs a real LLM's delimited
output; the real e2e run is the opt-in bench-graphrag-qa lane). Needs lightrag
installed, so it runs in the LightRAG isolated-venv lane."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

pytest.importorskip("lightrag")

from erkgbench.qa_e2e.engines.lightrag import LightRAGQAEngine, make_counting_llm_func  # noqa: E402
from erkgbench.qa_e2e.harness import QAEngine  # noqa: E402


def test_lightrag_engine_conforms_to_protocol():
    eng = LightRAGQAEngine(llm_model_func=_stub_llm, embedding_func=object(), work_root=".")
    assert isinstance(eng, QAEngine)
    assert eng.name == "lightrag"
    assert eng.fidelity == "real-e2e"


async def _stub_llm(prompt, system_prompt=None, history_messages=None, **kwargs):
    return "Ada"


def test_counting_llm_func_accounts_tokens():
    counter = {"in": 0, "out": 0}
    wrapped = make_counting_llm_func(_stub_llm, counter)
    out = asyncio.run(wrapped("a long enough prompt to count", system_prompt="sys"))
    assert out == "Ada"
    assert counter["in"] > 0 and counter["out"] > 0
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_qa_lightrag_smoke.py -v` (in the lightrag venv) — FAIL: `ImportError: LightRAGQAEngine`.

- [ ] **Step 3: Implement the adapter**

```python
# erkgbench/qa_e2e/engines/lightrag.py
"""LightRAG QA engine adapter over the real async API (ainsert/aquery), driven
from the sync QAEngine protocol via asyncio.run. LightRAG owns its LLM calls, so
the cost seam is an injected counting llm_model_func."""
from __future__ import annotations

import asyncio
import tempfile
import time
from typing import Any

from ..harness import AnswerResult, BuildResult


def make_counting_llm_func(inner, counter: dict):
    """Wrap a LightRAG llm_model_func to estimate token usage (len//4) into
    `counter` ({'in','out'}). Signature matches LightRAG's contract."""

    async def _wrapped(prompt, system_prompt=None, history_messages=None, **kwargs):
        counter["in"] += max(1, (len(prompt) + len(system_prompt or "")) // 4)
        out = await inner(
            prompt, system_prompt=system_prompt, history_messages=history_messages, **kwargs
        )
        counter["out"] += max(1, len(out) // 4)
        return out

    return _wrapped


class LightRAGQAEngine:
    name = "lightrag"
    fidelity = "real-e2e"

    def __init__(self, *, llm_model_func: Any, embedding_func: Any, work_root: str | None = None):
        self._counter = {"in": 0, "out": 0}
        self._llm_func = make_counting_llm_func(llm_model_func, self._counter)
        self._embedding_func = embedding_func
        self._work_root = work_root

    def _new_rag(self, working_dir: str):
        from lightrag import LightRAG

        return LightRAG(
            working_dir=working_dir,
            llm_model_func=self._llm_func,
            embedding_func=self._embedding_func,
        )

    def build_kg(self, corpus) -> BuildResult:
        t0 = time.perf_counter()
        before = dict(self._counter)
        workdir = tempfile.mkdtemp(prefix="lightrag_", dir=self._work_root)
        rag = self._new_rag(workdir)

        async def _build():
            await rag.initialize_storages()
            for doc in corpus.documents:
                await rag.ainsert(doc.text)

        asyncio.run(_build())
        handle = {"rag": rag, "workdir": workdir}
        return BuildResult(
            handle=handle,
            input_tokens=self._counter["in"] - before["in"],
            output_tokens=self._counter["out"] - before["out"],
            latency_s=time.perf_counter() - t0,
        )

    def answer(self, handle, question: str) -> AnswerResult:
        from lightrag import QueryParam

        t0 = time.perf_counter()
        before = dict(self._counter)
        rag = handle["rag"]
        text = asyncio.run(rag.aquery(question, param=QueryParam(mode="hybrid")))
        return AnswerResult(
            text=text or "",
            retrieved_fact_ids=(),  # LightRAG doesn't surface retrieved ids; see spec note
            input_tokens=self._counter["in"] - before["in"],
            output_tokens=self._counter["out"] - before["out"],
            latency_s=time.perf_counter() - t0,
        )
```

NOTE for the implementer: confirm `QueryParam` import path (`from lightrag import QueryParam` per the docs) and whether `aquery` needs `await rag.finalize_storages()` on teardown — if the harness reuses one engine across questions, finalize once after the run loop (the harness doesn't expose a teardown hook today; either add one in a follow-up or accept the tempdir leak for a benchmark run). Mode `hybrid` is LightRAG's default recommended mode; the spec's LLM-judge secondary compares on each engine's documented-best path, so `hybrid` is the right choice for the headline.

- [ ] **Step 4: Run to verify it passes** (in the lightrag venv): `python -m pytest tests/test_qa_lightrag_smoke.py -v` → PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/qa_e2e/engines/lightrag.py \
        packages/python/goldenmatch/benchmarks/er-kg-bench/tests/test_qa_lightrag_smoke.py
git commit -m "feat(qa-e2e): LightRAG engine adapter + cost-seam smoke test"
```

---

## Task 2: Wire LightRAG into the CLI

**Files:**
- Modify: `erkgbench/qa_e2e/run_qa_e2e.py` (`_build_engine`)

- [ ] **Step 1: Extend `_build_engine`** to construct the real LightRAG engine:

```python
    if name == "lightrag":
        from lightrag.llm.openai import gpt_4o_mini_complete, openai_embed

        from .engines.lightrag import LightRAGQAEngine

        return LightRAGQAEngine(
            llm_model_func=gpt_4o_mini_complete, embedding_func=openai_embed
        )
```

- [ ] **Step 2: py_compile + ruff** the file (no test run needed — covered by the lane).

- [ ] **Step 3: Commit**

```bash
git add packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/qa_e2e/run_qa_e2e.py
git commit -m "feat(qa-e2e): select --engine lightrag in the CLI"
```

---

## Task 3: CI — LightRAG isolated-venv smoke lane + add to the opt-in real lane

**Files:**
- Create: `.github/workflows/bench-lightrag-smoke.yml` (key-free smoke, isolated `lightrag-hku` venv)
- Modify: `.github/workflows/bench-graphrag-qa.yml` (add a `lightrag` job, real LLM)

- [ ] **Step 1: Smoke lane** (`bench-lightrag-smoke.yml`, `push` on the adapter path + `workflow_dispatch`):

```yaml
name: bench-lightrag-smoke
on:
  workflow_dispatch:
  push:
    paths:
      - "packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/qa_e2e/engines/lightrag.py"
      - "packages/python/goldenmatch/benchmarks/er-kg-bench/tests/test_qa_lightrag_smoke.py"
      - ".github/workflows/bench-lightrag-smoke.yml"
permissions:
  contents: read
jobs:
  lightrag-smoke:
    runs-on: ubuntu-latest
    timeout-minutes: 20
    steps:
      - uses: actions/checkout@df4cb1c069e1874edd31b4311f1884172cec0e10  # v6
      - uses: actions/setup-python@a309ff8b426b58ec0e2a45f0f869d46889d02405  # v6.2.0
        with:
          python-version: "3.12"
      - name: Install lightrag + goldenmatch (isolated)
        run: python -m pip install --upgrade pip pytest numpy "lightrag-hku" goldenmatch
      - name: LightRAG adapter smoke (protocol + cost seam, no LLM)
        working-directory: packages/python/goldenmatch/benchmarks/er-kg-bench
        env:
          POLARS_SKIP_CPU_CHECK: "1"
        run: python -m pytest tests/test_qa_lightrag_smoke.py -v
```

- [ ] **Step 2: Add a `lightrag` job** to `bench-graphrag-qa.yml` (mirrors the `goldengraph` job but installs `lightrag-hku` instead of building the native wheel, and runs `--engine lightrag`):

```yaml
  lightrag:
    runs-on: large-new-64GB
    timeout-minutes: 60
    steps:
      - uses: actions/checkout@df4cb1c069e1874edd31b4311f1884172cec0e10  # v6
      - uses: actions/setup-python@a309ff8b426b58ec0e2a45f0f869d46889d02405  # v6.2.0
        with:
          python-version: "3.12"
      - name: Install lightrag + goldenmatch + datasets (isolated)
        run: python -m pip install --upgrade pip pytest numpy "lightrag-hku" goldenmatch datasets
      - name: Run LightRAG end-to-end (real LLM, budget-capped)
        working-directory: packages/python/goldenmatch/benchmarks/er-kg-bench
        env:
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
          POLARS_SKIP_CPU_CHECK: "1"
        run: |
          python -m erkgbench.qa_e2e.run_qa_e2e \
            --engine lightrag \
            --corpus "${{ inputs.corpus }}" \
            --max-questions "${{ inputs.max_questions }}" \
            --budget-usd "${{ inputs.budget_usd }}" \
            --out-md results/RESULTS_QA_E2E_lightrag.md \
            --out-json results/results_qa_e2e_lightrag.json
      - uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a  # v4
        with:
          name: graphrag-qa-results-lightrag
          path: packages/python/goldenmatch/benchmarks/er-kg-bench/results/results_qa_e2e_lightrag*.*
          if-no-files-found: ignore
```

NOTE: each engine writes its own `results_qa_e2e_<engine>.{md,json}` so the parallel jobs don't clobber a shared file; the headline `RESULTS_QA_E2E.md` is merged from the per-engine JSONs in PR D (the final board refresh).

- [ ] **Step 3: Validate YAML** (`python -c "import yaml; yaml.safe_load(...)"` for both files) and **Commit**:

```bash
git add .github/workflows/bench-lightrag-smoke.yml .github/workflows/bench-graphrag-qa.yml
git commit -m "ci(qa-e2e): LightRAG isolated-venv smoke lane + add lightrag to the real lane"
```

---

## Done criteria for PR B

- `LightRAGQAEngine` conforms to `QAEngine`; the smoke test (protocol + cost seam) passes in the `lightrag-hku` isolated venv.
- `--engine lightrag` builds the real LightRAG engine.
- `bench-graphrag-qa` can run LightRAG end-to-end (real LLM, budget-capped) for the head-to-head.
- No change to goldengraph or the pure-Python harness; PR C (MS-GraphRAG) and PR D (Graphiti) follow the same shape.

## Open questions (surface during execution)

- **Engine teardown:** the harness has no teardown hook, so LightRAG's `finalize_storages` + tempdir cleanup either go in a follow-up harness change or are accepted as a per-run leak. Recommend a tiny `QAEngine.close()` optional method in PR B if it's clean; otherwise defer.
- **LightRAG version pin:** pin `lightrag-hku` to the version validated in the lane and record it in `RESULTS_QA_E2E.md` (the spec's "pin + record framework versions" requirement).
