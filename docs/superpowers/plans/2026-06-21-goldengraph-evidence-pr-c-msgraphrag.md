# GoldenGraph Evidence #1 / PR C — MS-GraphRAG competitor adapter Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Add Microsoft GraphRAG to the qa-e2e harness — the headline GraphRAG name. This is the program's **long pole**: it has a Python API, but indexing is a multi-workflow pipeline with config + dataframe IO, and its LLM is config-driven (awkward cost seam).

**Architecture:** A `MSGraphRAGQAEngine` over GraphRAG's **async Python API** (`graphrag.api.build_index` → `graphrag.api.local_search`), driven via `asyncio.run`. `build_index` writes parquet artifacts to a working dir; `answer` loads those artifacts and calls `local_search`. The LLM is driven by a programmatic `GraphRagConfig` (model + `OPENAI_API_KEY`); cost is read from GraphRAG's pipeline result if exposed, else estimated (flagged approximate).

**Tech Stack:** `graphrag` (Microsoft, pinned), `pandas`/`pyarrow`, `asyncio`, the `erkgbench/qa_e2e` harness, OpenAI via config.

**Prerequisite:** PR A (#1156, merged). Independent of PRs B/D.

**Confirmed API (Context7 `/microsoft/graphrag`):**
- `import graphrag.api as api`
- `index_result = await api.build_index(config=<GraphRagConfig>, input_documents=<pandas.DataFrame>)` — input is a documents dataframe; outputs are written under the config's output dir.
- Query via `await api.local_search(...)` / `api.global_search(...)` — these take the **loaded index artifacts** (entities/relationships/communities/community_reports/text_units dataframes) + the query.
- `GraphRagConfig` from `graphrag.config.load_config(root_dir)` (settings.yaml) OR constructed programmatically. The CLI is built on this API.
- **CONFIRM during impl (read graphrag source / the api_overview notebook):** the minimal programmatic `GraphRagConfig` (output/storage dir, the default chat + embedding models block), `build_index`'s exact `input_documents` column contract, and `local_search`'s exact parameter list + which parquet files to load.

**Design obstacles (load-bearing):**
1. **Config + dataframe IO, not a one-liner.** `build_index` needs a `GraphRagConfig` (a temp root dir, an output dir, LLM/embedding model config) and a documents dataframe; `local_search` needs the resulting parquet artifacts loaded back. Encapsulate all of this in the adapter; this is the bulk of the work.
2. **Config-driven LLM → awkward cost seam.** GraphRAG owns its LLM calls via config; there's no inject-a-counting-func hook like LightRAG. Read token usage from `build_index`'s `PipelineRunResult` if exposed; otherwise estimate (corpus size × model) and **mark `cost_usd` approximate** in the results. Do NOT fake precision.
3. **Indexing is expensive** (many LLM calls per chunk — the spec's "long pole"). Keep `--max-questions` small and the corpus small for the headline; the budget cap still applies.
4. **Version churn.** GraphRAG's API moves fast; pin the version and record it. If the programmatic `build_index`/`local_search` API differs from the pinned docs, the CONFIRM step catches it before coding.

---

## File Structure

| File | Responsibility |
|---|---|
| `erkgbench/qa_e2e/engines/ms_graphrag.py` | `MSGraphRAGQAEngine` + `_build_config` + `_load_artifacts`. |
| `tests/test_qa_ms_graphrag_smoke.py` | Protocol conformance + config-builder unit test (no index, no LLM). |
| `erkgbench/qa_e2e/run_qa_e2e.py` | Add `ms_graphrag` to `_build_engine`. |
| `.github/workflows/bench-msgraphrag-smoke.yml` | Isolated `graphrag` venv; protocol/config smoke (no LLM). |
| `.github/workflows/bench-graphrag-qa.yml` | Add an `ms_graphrag` job (real LLM, budget-capped). |

---

## Task 1: Adapter + config builder (testable seam)

**Files:** Create `erkgbench/qa_e2e/engines/ms_graphrag.py`; Test `tests/test_qa_ms_graphrag_smoke.py`.

- [ ] **Step 1: Failing test** — protocol conformance + that `_build_config(workdir, model)` returns a usable `GraphRagConfig` with the output dir set (NO index, NO LLM):

```python
# tests/test_qa_ms_graphrag_smoke.py
"""Protocol conformance + config builder. Does NOT run build_index/local_search
(those need real LLM + are expensive) -- that's the opt-in bench-graphrag-qa lane."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

pytest.importorskip("graphrag")

from erkgbench.qa_e2e.engines.ms_graphrag import MSGraphRAGQAEngine  # noqa: E402
from erkgbench.qa_e2e.harness import QAEngine  # noqa: E402


def test_ms_graphrag_engine_conforms_to_protocol():
    eng = MSGraphRAGQAEngine(model="gpt-4o-mini")
    assert isinstance(eng, QAEngine)
    assert eng.name == "ms_graphrag"
    assert eng.fidelity == "real-e2e"


def test_build_config_sets_output_dir(tmp_path):
    eng = MSGraphRAGQAEngine(model="gpt-4o-mini")
    cfg = eng._build_config(str(tmp_path))
    # the config points indexing output under the given working dir
    assert str(tmp_path) in repr(cfg)
```

- [ ] **Step 2: run -> fail** (graphrag venv). **Step 3: Implement** `engines/ms_graphrag.py`:
  - `_build_config(workdir, model) -> GraphRagConfig` — programmatic config: root/output/storage dir under `workdir`, default chat model `model` + an embedding model, `api_key` from `OPENAI_API_KEY`. (CONFIRM the minimal config shape from graphrag source.)
  - `_load_artifacts(output_dir) -> dict[str, pandas.DataFrame]` — read the parquet outputs (`entities`, `relationships`, `communities`, `community_reports`, `text_units`) `local_search` needs. (CONFIRM file names.)
  - `MSGraphRAGQAEngine(*, model="gpt-4o-mini")`:
    - `build_kg(corpus)`: `workdir = mkdtemp`; `cfg = _build_config(workdir)`; `df = pandas.DataFrame({"id":[d.id...], "text":[d.text...]})`; `result = asyncio.run(api.build_index(config=cfg, input_documents=df))`; handle = `{cfg, output_dir}`; tokens from `result` stats if exposed else 0 (flag approximate).
    - `answer(handle, question)`: `arts = _load_artifacts(output_dir)`; `resp, _ctx = asyncio.run(api.local_search(config=cfg, query=question, **arts, community_level=2, response_type="single sentence"))`; `text = resp`; `retrieved_fact_ids=()`.
  - `asyncio.run` drives the async calls.

- [ ] **Step 4: run -> pass** (2 tests, no index). **Step 5: commit** `feat(qa-e2e): MS-GraphRAG engine adapter + config-builder smoke`.

---

## Task 2: Wire `ms_graphrag` into the CLI

- [ ] Add to `_build_engine`: `if name == "ms_graphrag": from .engines.ms_graphrag import MSGraphRAGQAEngine; return MSGraphRAGQAEngine(model="gpt-4o-mini")`. py_compile + ruff. Commit.

---

## Task 3: CI — protocol smoke lane + real lane job

- [ ] **`bench-msgraphrag-smoke.yml`** (`push` on the adapter path; `ubuntu-latest`, no LLM): `pip install graphrag goldenmatch pytest pandas pyarrow numpy`; run `tests/test_qa_ms_graphrag_smoke.py`.
- [ ] **`bench-graphrag-qa.yml`** — add an `ms_graphrag` job mirroring the others: `pip install graphrag goldenmatch datasets pandas pyarrow`, run `--engine ms_graphrag` with the budget cap, write `results_qa_e2e_ms_graphrag.json`. Same `OPENAI_API_KEY` secret. `timeout-minutes: 90` (indexing is slow).
- [ ] Validate YAML; commit `ci(qa-e2e): MS-GraphRAG smoke lane + real job`.

---

## Done criteria
- `MSGraphRAGQAEngine` conforms; the config-builder smoke passes in the `graphrag` venv.
- `bench-graphrag-qa` `ms_graphrag` job runs `build_index` → `local_search` end-to-end (real LLM, budget-capped).
- Results mark MS-GraphRAG's `cost_usd` **approximate** (config-driven LLM) + `retrieved_fact_ids=()`.

## Open questions
- The three CONFIRM items (minimal `GraphRagConfig`, `build_index` input columns, `local_search` params + artifact filenames) — resolve FIRST during impl from graphrag source / the `api_overview` + `input_documents` notebooks. If the programmatic config is too heavy to build by hand, fall back to writing a minimal `settings.yaml` into the workdir and `graphrag.config.load_config(workdir)`.
- Token accounting: prefer real usage from `PipelineRunResult`; document the fallback estimate if it isn't exposed.
