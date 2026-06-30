# Stage-2-E: Cascade Synthesis-LLM Seam — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the bench engine route the synthesis stage (`ask`) to a separate, larger reasoning model (own model/endpoint/key) while extraction stays on the injected 7B — opt-in, default-off byte-identical.

**Architecture:** A module-level `_build_synthesis_llm(default_llm)` in the bench engine returns the extraction llm when `GOLDENGRAPH_SYNTHESIS_MODEL` is unset, else a separate `_CountingLLM(OpenAIClient(model=…, client=openai.OpenAI(base_url=…, api_key=…)))`. `__init__` builds `self._synth_llm`; `answer` uses it (arg + token counters); `build_kg` untouched.

**Tech Stack:** Python (stdlib + openai), pytest, the existing `scripts/distill/modal_bench.py` harness.

**Spec:** `docs/superpowers/specs/2026-06-30-stage2e-cascade-synthesis-llm-design.md`
**Branch:** `feat/stage2e-cascade-synthesis-llm` (off the stage-2-D branch; rebase onto main after #1326 lands).

---

## Environment notes (read before starting)

- **Box-safe tests** (the engine module's heavy imports are lazy; the *unset* test needs no openai, the *set* test constructs `openai.OpenAI(...)` which does NOT make a network call):
  ```bash
  cd packages/python/goldenmatch/benchmarks/er-kg-bench
  PYTHONPATH="." POLARS_SKIP_CPU_CHECK=1 "D:/show_case/goldenmatch/.venv/Scripts/python.exe" \
    -m pytest tests/test_synthesis_llm_seam.py -q -p no:cacheprovider
  ```
- **Do NOT run the whole suite locally.** `ruff check` + `py_compile` before commit. GitHub auth: `GH_TOKEN=$(gh auth token --user benzsevern)`.
- `os` is imported at module top; `_CountingLLM` (line ~136, `self._inner = inner`) and `OpenAIClient` exist.

## File structure

- **Modify:** `erkgbench/qa_e2e/engines/goldengraph.py` — `_build_synthesis_llm` (after the `_CountingLLM` class, before the engine class); `self._synth_llm` in `__init__`; `answer` swaps the `ask(llm=)` arg + the before/after token counters to `self._synth_llm`.
- **Create:** `tests/test_synthesis_llm_seam.py` — the unset + set unit tests.
- **Create (Task 2, key-gated):** `docs/superpowers/reports/2026-06-30-stage2e-cascade-synthesis-llm.md`.

---

### Task 1: the synthesis-llm seam

**Files:**
- Modify: `erkgbench/qa_e2e/engines/goldengraph.py`
- Test: `tests/test_synthesis_llm_seam.py`

- [ ] **Step 1: Write the failing tests** (create the file)

```python
# tests/test_synthesis_llm_seam.py
"""Cascade synthesis-LLM seam (stage-2-E): synthesis can use a separate model from extraction.
Default off = the extraction llm (same object). Pure (openai.OpenAI constructs without network)."""
from __future__ import annotations

from erkgbench.qa_e2e.engines.goldengraph import _build_synthesis_llm


def test_synthesis_unset_reuses_extraction_llm(monkeypatch):
    monkeypatch.delenv("GOLDENGRAPH_SYNTHESIS_MODEL", raising=False)
    sentinel = object()
    assert _build_synthesis_llm(sentinel) is sentinel        # byte-identical: same object


def test_synthesis_set_builds_separate_client(monkeypatch):
    monkeypatch.setenv("GOLDENGRAPH_SYNTHESIS_MODEL", "deepseek-reasoner")
    monkeypatch.setenv("GOLDENGRAPH_SYNTHESIS_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("GOLDENGRAPH_SYNTHESIS_API_KEY", "sk-test-not-real")
    sentinel = object()
    synth = _build_synthesis_llm(sentinel)
    assert synth is not sentinel                              # a separate object is built
    assert synth._inner.model == "deepseek-reasoner"          # _CountingLLM -> OpenAIClient.model
```

- [ ] **Step 2: Run, verify FAIL** (`_build_synthesis_llm` undefined).

- [ ] **Step 3: Implement** — in `engines/goldengraph.py`, add the helper AFTER the `_CountingLLM` class (so `_CountingLLM` is defined), BEFORE the engine class:

```python
def _build_synthesis_llm(default_llm):
    """The synthesis-stage LLM. When GOLDENGRAPH_SYNTHESIS_MODEL is set, build a SEPARATE client (own
    model + endpoint + key) so a frontier reasoning model handles the ~20 low-volume synthesis calls
    while the cheap 7B keeps the ~400 parallel extractions -- the cascade. Unset -> the extraction llm
    (byte-identical). `openai.OpenAI` + `OpenAIClient` are imported lazily so the unset path adds no deps."""
    model = os.environ.get("GOLDENGRAPH_SYNTHESIS_MODEL") or ""
    if not model:
        return default_llm
    import openai

    from goldengraph.llm import OpenAIClient

    base = os.environ.get("GOLDENGRAPH_SYNTHESIS_BASE_URL") or None
    key = os.environ.get("GOLDENGRAPH_SYNTHESIS_API_KEY") or None
    client = openai.OpenAI(base_url=base, api_key=key) if (base or key) else openai.OpenAI()
    return _CountingLLM(OpenAIClient(model=model, client=client))
```

In `__init__`, right after `self._llm = _CountingLLM(llm)`:
```python
        # Cascade seam: synthesis may use a separate (larger) model; extraction stays on self._llm.
        self._synth_llm = _build_synthesis_llm(self._llm)
```

In `answer`, change the THREE `self._llm` references (and only those — `build_kg` keeps `self._llm`):
```python
        before_in, before_out = self._synth_llm.input_tokens, self._synth_llm.output_tokens
        ...
        text = ask(
            question,
            handle["store"],
            llm=self._synth_llm,            # was self._llm
            ...
        )
        ...
            input_tokens=self._synth_llm.input_tokens - before_in,
            output_tokens=self._synth_llm.output_tokens - before_out,
```

(When unset, `self._synth_llm is self._llm`, so all three are identical to today.)

- [ ] **Step 4: Run, verify PASS** (2 tests). If `openai` is not importable on the box, the *unset* test still passes (no openai); note it and run the *set* test on CI.

- [ ] **Step 5: ruff + py_compile + commit**

```bash
"D:/show_case/goldenmatch/.venv/Scripts/python.exe" -m ruff check erkgbench/qa_e2e/engines/goldengraph.py tests/test_synthesis_llm_seam.py
"D:/show_case/goldenmatch/.venv/Scripts/python.exe" -m py_compile erkgbench/qa_e2e/engines/goldengraph.py
git add erkgbench/qa_e2e/engines/goldengraph.py tests/test_synthesis_llm_seam.py
git commit -m "feat(er-kg-bench): cascade synthesis-LLM seam (separate synthesis model, default off)

GOLDENGRAPH_SYNTHESIS_MODEL routes synthesis (ask) to a separate OpenAIClient (own endpoint/key) while
extraction stays on the 7B. answer() uses self._synth_llm for the ask arg AND token accounting; build_kg
untouched. Unset -> self._synth_llm is self._llm (byte-identical). Reuses OpenAIClient injectable client
+ _CountingLLM. NOTE: _CountingLLM has no complete_many, so synthesis self-consistency won't engage
through the synth client (status quo). 2 tests green."
```

---

### Task 2: validation (KEY-GATED — defer if no DeepSeek key yet)

**Files:**
- Create: `docs/superpowers/reports/2026-06-30-stage2e-cascade-synthesis-llm.md`

**Prerequisite:** a DeepSeek (or other OpenAI-compatible frontier-OSS) API key. **The Task-1 wiring ships
regardless** (tested, default-off); this validation only runs once a key exists. If no key, STOP after
Task 1, open the PR, and leave this task unchecked with a note.

- [ ] **Step 1: Push the branch**

```bash
GH_TOKEN=$(gh auth token --user benzsevern) git push -u origin feat/stage2e-cascade-synthesis-llm
```

- [ ] **Step 2: Create a Modal secret for the key (NOT via --opts — Modal logs call args)**

```bash
# the secret exposes GOLDENGRAPH_SYNTHESIS_API_KEY on the run function's env
modal secret create goldengraph-synth GOLDENGRAPH_SYNTHESIS_API_KEY=<deepseek-key>
```
Then attach it to `run_bench` (and `run_bench_big`) in `scripts/distill/modal_bench.py`:
```python
@app.function(image=image, gpu="A10G", volumes={"/cache": cache}, timeout=5400,
              secrets=[modal.Secret.from_name("goldengraph-synth")])
```
(Small `modal_bench` edit; commit it with the report. The MODEL + BASE_URL are non-secret and go via `--opts`.)

- [ ] **Step 3: Fire the N=20 hybrid + frontier-synthesis run**

```bash
P="a99885f0-c5af-4ae1-9dc8-255cc60aa129"
export MODAL_TOKEN_ID=$(infisical.cmd secrets get MODAL_TOKEN_ID --projectId "$P" --env dev --plain --silent)
export MODAL_TOKEN_SECRET=$(infisical.cmd secrets get MODAL_TOKEN_SECRET --projectId "$P" --env dev --plain --silent)
M="D:/show_case/goldenmatch/.venv/Scripts/modal.exe"
PYTHONIOENCODING=utf-8 "$M" run --detach scripts/distill/modal_bench.py \
  --engine goldengraph --eval end_to_end --corpus musique --n 20 --spawn \
  --opts $'GOLDENGRAPH_QA_MODE=hybrid\nGOLDENGRAPH_SYNTHESIS_MODEL=deepseek-reasoner\nGOLDENGRAPH_SYNTHESIS_BASE_URL=https://api.deepseek.com'
```
Extraction runs on the local 7B (Ollama); synthesis on DeepSeek. **Wall-time check:** should land near the 7B-hybrid wall (~35-45 min), NOT the 32B's ~48 min — the big model serves ~20 synthesis calls, not ~400 extractions. Poll with a Monitor (`volume get` -> `/tmp/s2e.md`).

- [ ] **Step 4: Aggregate + write the verdict report**

```bash
grep -iE "support_recall=|musique \| 0" /tmp/s2e.md | head -2
```
Report `docs/superpowers/reports/2026-06-30-stage2e-cascade-synthesis-llm.md`:
- Config (N=20, 7B extract + DeepSeek-R1 synth, hybrid) + the matched 7B-hybrid baseline (0.30).
- Before/after `answer_match`, buckets, `support_recall`, AND the **wall time** (the cascade-efficiency claim).
- **Verdict:**
  - *> 0.30* → scale IS the lever surgically on synthesis → cascade works; recommend it; revise "scale isn't the lever" to "not for extraction, yes for synthesis."
  - *≈ 0.30* → frontier synthesis over good passages doesn't beat 7B → bottleneck is retrieval/question difficulty, not synthesis.

- [ ] **Step 5: Commit the report (+ the modal_bench secret edit)**

```bash
git add docs/superpowers/reports/2026-06-30-stage2e-cascade-synthesis-llm.md scripts/distill/modal_bench.py
git commit -m "docs(stage-2e): cascade synthesis-LLM validation verdict"
```

---

## Done criterion

- Task 1 merged behind green tests (unset byte-identical + set builds-separate) + no-regression.
- EITHER a committed validation report (key available) OR the PR opened with Task 2 left unchecked + a one-line "validation pending DeepSeek key" note.
- Open a PR; arm auto-merge once CI is green. (The seam lands default-off regardless of the validation.)
