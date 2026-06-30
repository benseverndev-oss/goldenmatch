# Stage-2-D: Hybrid Passages via Local nomic — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route goldengraph's hybrid passage embedder through the env model (nomic on the local lane) instead of the hardcoded `text-embedding-3-large`, so `hybrid` answer mode runs on the local Ollama stack — then validate whether passage-augmented synthesis beats graph-only `local` on real-corpus MuSiQue.

**Architecture:** A tiny module-level `_passage_embed_model()` helper in the bench engine (mirrors `run_qa_e2e._rag_embed_model`, intentional duplicate to avoid a cross-module import) feeds the hybrid block's `_OpenAIEmbedderAdapter`. `OpenAI()` already inherits the Ollama base-url/key from env on the local lane. Default off-local (env unset) is byte-identical (`text-embedding-3-large`).

**Tech Stack:** Python (stdlib), pytest, the existing `scripts/distill/modal_bench.py --corpus musique` Modal harness.

**Spec:** `docs/superpowers/specs/2026-06-30-stage2d-hybrid-nomic-passages-design.md`
**Branch:** `feat/stage2d-hybrid-nomic-passages` (already created off `origin/main`).

---

## Environment notes (read before starting)

- **Box-safe test** (the engine module's heavy imports — openai/goldengraph/goldenmatch — are LAZY inside methods, so importing the module for the helper is light):
  ```bash
  cd packages/python/goldenmatch/benchmarks/er-kg-bench
  PYTHONPATH="." POLARS_SKIP_CPU_CHECK=1 "D:/show_case/goldenmatch/.venv/Scripts/python.exe" \
    -m pytest tests/test_passage_embed_model.py -q -p no:cacheprovider
  ```
- **Do NOT run the whole suite locally.** `ruff check` + `py_compile` before commit. GitHub auth: `GH_TOKEN=$(gh auth token --user benzsevern)`.
- **Intentional duplication:** `_passage_embed_model()` is byte-for-byte the same logic as `run_qa_e2e._rag_embed_model()`. This is deliberate (the engine should not import the CLI module); call it out in the commit so it isn't flagged as accidental.

## File structure

- **Modify:** `erkgbench/qa_e2e/engines/goldengraph.py` — add `_passage_embed_model()` (module level, near the other module helpers / above the engine class); use it in the hybrid block (line ~233); update the stale `build_kg` comment (lines ~222-226).
- **Create:** `tests/test_passage_embed_model.py` — the helper unit test.
- **Create (Task 2):** `docs/superpowers/reports/2026-06-30-stage2d-hybrid-nomic-passages.md`.

---

### Task 1: route the passage embedder through the env model

**Files:**
- Modify: `erkgbench/qa_e2e/engines/goldengraph.py`
- Test: `tests/test_passage_embed_model.py`

- [ ] **Step 1: Write the failing test** (create the file)

```python
# tests/test_passage_embed_model.py
"""Hybrid passage embedder model selection (stage-2-D): the local lane's OPENAI_EMBED_MODEL (nomic)
routes the passage half through Ollama; unset falls back to the OpenAI default. Pure, no network."""
from __future__ import annotations


def test_passage_embed_model_env(monkeypatch):
    from erkgbench.qa_e2e.engines.goldengraph import _passage_embed_model

    monkeypatch.delenv("OPENAI_EMBED_MODEL", raising=False)
    assert _passage_embed_model() == "text-embedding-3-large"      # unset -> OpenAI default

    monkeypatch.setenv("OPENAI_EMBED_MODEL", "nomic-embed-text")
    assert _passage_embed_model() == "nomic-embed-text"            # local lane -> nomic

    monkeypatch.setenv("OPENAI_EMBED_MODEL", "")                   # empty -> default (falsy `or`)
    assert _passage_embed_model() == "text-embedding-3-large"
```

- [ ] **Step 2: Run, verify FAIL**

Run the box-safe command above. Expected: FAIL (`_passage_embed_model` undefined / ImportError).

- [ ] **Step 3: Implement** — in `engines/goldengraph.py`, add the helper at module level (after the imports block, before the `_PassageRetriever` class):

```python
def _passage_embed_model() -> str:
    """Passage-retriever embedding model: the local lane's OPENAI_EMBED_MODEL (e.g. nomic-embed-text via
    Ollama) when set, else the OpenAI default. Routes the passage half through the SAME endpoint as the
    chat/graph halves on the local stack, unblocking hybrid mode without OpenAI spend. (Intentional
    duplicate of run_qa_e2e._rag_embed_model -- the engine must not import the CLI module.)"""
    return os.environ.get("OPENAI_EMBED_MODEL") or "text-embedding-3-large"
```

Then in the hybrid block (currently ~line 233) replace:
```python
            adapter = _OpenAIEmbedderAdapter(OpenAI(), "text-embedding-3-large")
```
with:
```python
            adapter = _OpenAIEmbedderAdapter(OpenAI(), _passage_embed_model())
```

And update the stale `build_kg` comment just above (lines ~222-226). Replace the sentence that says the
passage embedder is "a SEPARATE OpenAI embedder (text-embedding-3-large, matching goldenmatch_rag/
text_rag)" with one that reflects the env routing, e.g.:
```python
        # Hybrid mode also indexes the raw paragraphs for answer-time passage retrieval, using the
        # passage-embedding model from `_passage_embed_model()` (OPENAI_EMBED_MODEL -> nomic on the local
        # lane, text-embedding-3-large on the OpenAI lane). Same model as goldenmatch_rag/text_rag on
        # each lane, so the passage half stays comparable; the graph half stays the store's job.
        # Embedding calls here are NOT charged to the engine token budget (parity with text_rag).
```

- [ ] **Step 4: Run, verify PASS** (1 test).

- [ ] **Step 5: ruff + py_compile + commit**

```bash
"D:/show_case/goldenmatch/.venv/Scripts/python.exe" -m ruff check erkgbench/qa_e2e/engines/goldengraph.py tests/test_passage_embed_model.py
"D:/show_case/goldenmatch/.venv/Scripts/python.exe" -m py_compile erkgbench/qa_e2e/engines/goldengraph.py
git add erkgbench/qa_e2e/engines/goldengraph.py tests/test_passage_embed_model.py
git commit -m "feat(er-kg-bench): route hybrid passage embedder via OPENAI_EMBED_MODEL (nomic on local)

Unblocks goldengraph hybrid mode on the Ollama stack. _passage_embed_model() (intentional duplicate of
run_qa_e2e._rag_embed_model) replaces the hardcoded text-embedding-3-large; OpenAI() already inherits the
Ollama base-url/key. Off-local (env unset) is byte-identical. Stale comment updated."
```

---

### Task 2: N=20 MuSiQue hybrid validation → ship-or-null report

**Files:**
- Create: `docs/superpowers/reports/2026-06-30-stage2d-hybrid-nomic-passages.md`

A MEASUREMENT, not code. Detached Modal pattern.

- [ ] **Step 1: Push the branch**

```bash
GH_TOKEN=$(gh auth token --user benzsevern) git push -u origin feat/stage2d-hybrid-nomic-passages
```

- [ ] **Step 2: Fire the N=20 hybrid run**

```bash
P="a99885f0-c5af-4ae1-9dc8-255cc60aa129"
export MODAL_TOKEN_ID=$(infisical.cmd secrets get MODAL_TOKEN_ID --projectId "$P" --env dev --plain --silent)
export MODAL_TOKEN_SECRET=$(infisical.cmd secrets get MODAL_TOKEN_SECRET --projectId "$P" --env dev --plain --silent)
M="D:/show_case/goldenmatch/.venv/Scripts/modal.exe"
PYTHONIOENCODING=utf-8 "$M" run --detach scripts/distill/modal_bench.py \
  --engine goldengraph --eval end_to_end --corpus musique --n 20 --spawn \
  --opts $'GOLDENGRAPH_QA_MODE=hybrid'
```
Result: `results/end_to_end_20_goldengraph-qwen2.5-7b-instruct-musique.md`. Poll with a Monitor (`volume get --force` to `/tmp/s2d.md`, grep `support_recall=`).

**Reality check:** hybrid needs Ollama to expose `/v1/embeddings` for `nomic-embed-text` (the modal_bench image already `ollama pull`s the embed model). If the first answer 500s on the embeddings endpoint, that is the failure to debug — NOT a null. The N=20 hybrid run is also heavier than local (it embeds the whole corpus once); the 90-min cap holds.

**Baseline:** the matched `local` N=20 control is the stage-2-C bridge-OFF run (`GOLDENGRAPH_QA_MODE=auto`, same seed/subset). If that result is not still on hand, fire it (`--opts GOLDENGRAPH_QA_MODE=auto`, N=20) so the A/B is on the identical questions.

- [ ] **Step 3: Aggregate** (pull the result file first)

```bash
grep -iE "support_recall=|musique \| 0" /tmp/s2d.md | head -2
grep -oE "(EXTRACTION|RETRIEVAL-BROKEN-CHAIN|SYNTHESIS)" /tmp/s2d.md | sort | uniq -c | sort -rn
```

- [ ] **Step 4: Write the verdict report** — `docs/superpowers/reports/2026-06-30-stage2d-hybrid-nomic-passages.md`:
  - Run config (N=20, model, `GOLDENGRAPH_QA_MODE=hybrid`, passage embedder = nomic, date) + the matched `local` baseline.
  - Before/after: `answer_match` (local vs hybrid), bucket distribution, `support_recall`.
  - **Verdict per the pre-committed gate:**
    - *hybrid > local* → SUCCESS: passages carry the answer; recommend hybrid as the real-corpus mode. **State the two caveats as part of the verdict:** (1) this is graph-guided RAG, not pure-KG multi-hop; (2) nomic passages are a LOWER BOUND (a stronger passage embedder could do better).
    - *hybrid ≈ local* → NULL: passages don't rescue it either → the construction ceiling is deep; **32B extractor** is the next experiment.
  - Confidence statement scaled to N=20.

- [ ] **Step 5: Commit the report**

```bash
git add docs/superpowers/reports/2026-06-30-stage2d-hybrid-nomic-passages.md
git commit -m "docs(stage-2d): hybrid-via-nomic validation verdict"
```

---

## Done criterion

- Task 1 merged behind a green test (helper env-selection) + no-regression (existing engine tests unaffected; off-local byte-identical).
- A committed verdict report with the matched `local` vs `hybrid` N=20 comparison and a ship-or-null verdict (with the RAG-posture + nomic-lower-bound caveats if it ships).
- Open a PR; arm auto-merge once CI is green. (Code lands regardless — the env routing is back-compat-safe; the report records whether hybrid is the real-corpus mode or whether 32B-extractor is next.)
