# GoldenGraph local OSS-LLM bench lane Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let `bench-graphrag-qa` run goldengraph against a local OSS LLM served by Ollama in the runner (OpenAI-compatible), so the real extraction->resolve->store->retrieve->synthesize path runs $0 / no API key as a stopgap until OpenAI credits are topped off.

**Architecture:** Two small backward-compatible code seams -- `OpenAIProvider.embed` honors `OPENAI_BASE_URL`; `_build_engine` reads chat/embed model names from env -- plus a `workflow_dispatch` `use_local_llm` path that stands up Ollama and swaps the run-step env. Non-gating, informational.

**Tech Stack:** Python 3.12, pytest, ruff, GitHub Actions, Ollama. Off main.

**Spec:** `docs/superpowers/specs/2026-06-27-goldengraph-local-oss-llm-lane-design.md`

---

## Conventions for every task

- Worktree: `D:\show_case\gg-local-llm`, branch `feat/goldengraph-local-llm-lane` (off main).
- `PYEXE="D:/show_case/goldenmatch/.venv/Scripts/python.exe"`; set `POLARS_SKIP_CPU_CHECK=1` for any run importing goldenmatch.
- Bench dir = `packages/python/goldenmatch/benchmarks/er-kg-bench`.
- Ruff-clean per commit. Env-var names introduced by this lane: `OPENAI_MODEL`, `OPENAI_EMBED_MODEL` (project-defined; the SDK does not read them). `OPENAI_BASE_URL` is the SDK's own (honored automatically by `OpenAIClient`).
- Verified reuse facts (do NOT re-derive):
  - `goldenmatch/embeddings/providers.py::OpenAIProvider.embed` builds the literal `"https://api.openai.com/v1/embeddings"`, reads `OPENAI_API_KEY`, uses `urllib.request.urlopen`. `os` + `urllib.request` already imported in that module (CONFIRM at edit; add if absent).
  - `erkgbench/qa_e2e/run_qa_e2e.py::_build_engine(name)` hardcodes `model="gpt-4o-mini"`; `os` already imported (it reads `FALKORDB_HOST` etc.). goldengraph branch builds `OpenAIClient(model="gpt-4o-mini")` + `GoldenmatchEmbedder(provider="openai")`. The `goldenmatch_rag`/`goldenmatch_entity_rag` branches also build `OpenAIClient(model="gpt-4o-mini", ...)`.
  - `OpenAIClient(model=...)`; `GoldenmatchEmbedder(provider="openai", model=None)` forwards model to the provider. `OpenAIClient.complete` uses `openai.OpenAI()` (honors `OPENAI_BASE_URL`).
  - `bench-graphrag-qa.yml` goldengraph job: `runs-on: large-new-64GB`; install step `python -m pip install --upgrade pip maturin pytest goldenmatch datasets openai` + maturin-builds the wheel + `pip install --no-deps -e packages/python/goldengraph`; run step env `OPENAI_API_KEY: ${{ secrets.GOLDENGRAPH_OPENAI_API_KEY }}`. CONFIRM exact step names + the run command (`python -m erkgbench.qa_e2e.run_qa_e2e ...`) at edit time.
- Commit footer:
  ```
  Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01Sc5aGSaVBTWBWpytNH99QE
  ```

## File structure

- Modify `packages/python/goldenmatch/goldenmatch/embeddings/providers.py` -- `OpenAIProvider.embed` base-url.
- Modify `packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/qa_e2e/run_qa_e2e.py` -- model env.
- Create `packages/python/goldenmatch/benchmarks/er-kg-bench/tests/test_qa_local_llm_config.py` -- unit tests for both seams.
- Modify `.github/workflows/bench-graphrag-qa.yml` -- `use_local_llm` input + Ollama setup + env swap.
- (Rollout sweep, optional) `docs-site` tuning note: document `OPENAI_BASE_URL`/`OPENAI_MODEL`/`OPENAI_EMBED_MODEL`.

---

## Task 1: OpenAIProvider.embed honors OPENAI_BASE_URL

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/embeddings/providers.py`
- Test: `packages/python/goldenmatch/benchmarks/er-kg-bench/tests/test_qa_local_llm_config.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_qa_local_llm_config.py
"""Local OSS-LLM lane config seams -- wheel-free (no Ollama, no network)."""
from __future__ import annotations

import goldenmatch.embeddings.providers as providers


def test_embed_url_defaults_to_openai(monkeypatch):
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    captured = {}

    def _fake_urlopen(req):  # capture the URL, return a 1-vector response
        captured["url"] = req.full_url
        import io, json
        return _Resp(io.BytesIO(json.dumps({"data": [{"index": 0, "embedding": [0.1, 0.2]}]}).encode()))

    monkeypatch.setattr(providers.urllib.request, "urlopen", _fake_urlopen)
    providers.OpenAIProvider(model="m").embed(["hello"])
    assert captured["url"] == "https://api.openai.com/v1/embeddings"


def test_embed_url_honors_base_url(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "ollama")
    captured = {}

    def _fake_urlopen(req):
        captured["url"] = req.full_url
        import io, json
        return _Resp(io.BytesIO(json.dumps({"data": [{"index": 0, "embedding": [0.1]}]}).encode()))

    monkeypatch.setattr(providers.urllib.request, "urlopen", _fake_urlopen)
    providers.OpenAIProvider(model="m").embed(["hi"])
    assert captured["url"] == "http://localhost:11434/v1/embeddings"


class _Resp:  # context-manager shim for urlopen
    def __init__(self, fh): self._fh = fh
    def __enter__(self): return self._fh
    def __exit__(self, *a): return False
```

NOTE Step 2 may need to adapt `_fake_urlopen`/`_Resp` to the real `urlopen` usage (the provider does `with urlopen(req) as resp: resp.read()`). Confirm the read shape when running.

- [ ] **Step 2: Run to verify it fails**

Run: `cd <bench dir> && POLARS_SKIP_CPU_CHECK=1 PYTHONPATH="$(pwd);D:/show_case/gg-local-llm/packages/python/goldengraph" "$PYEXE" -m pytest tests/test_qa_local_llm_config.py -k embed -v`
Expected: FAIL (`test_embed_url_honors_base_url` -> still the hardcoded openai URL).

- [ ] **Step 3: Write minimal implementation**

In `OpenAIProvider.embed`, replace the literal URL:

```python
        base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        req = urllib.request.Request(
            f"{base}/embeddings",
            ...
        )
```

(Confirm `os` is imported in the module; it is used elsewhere -- if not, add `import os`.) Keep the `# noqa: S310` (now a configurable endpoint -- update the comment to note the default is the fixed https endpoint, override via OPENAI_BASE_URL).

- [ ] **Step 4: Run to verify it passes**

Run: `... -k embed -v` -> PASS. `ruff check providers.py tests/test_qa_local_llm_config.py` -> clean.

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldenmatch/goldenmatch/embeddings/providers.py packages/python/goldenmatch/benchmarks/er-kg-bench/tests/test_qa_local_llm_config.py
git commit -m "feat(goldenmatch): OpenAIProvider embeddings honor OPENAI_BASE_URL"
```

---

## Task 2: _build_engine reads chat/embed model from env

**Files:**
- Modify: `packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/qa_e2e/run_qa_e2e.py`
- Test: `packages/python/goldenmatch/benchmarks/er-kg-bench/tests/test_qa_local_llm_config.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_qa_local_llm_config.py
def test_build_engine_reads_model_env(monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "qwen2.5:7b-instruct")
    monkeypatch.setenv("OPENAI_EMBED_MODEL", "nomic-embed-text")
    from erkgbench.qa_e2e import run_qa_e2e
    eng = run_qa_e2e._build_engine("goldengraph")
    # the engine wraps the llm; reach the OpenAIClient.model (CONFIRM the attribute path in Step 2)
    assert _engine_chat_model(eng) == "qwen2.5:7b-instruct"


def test_build_engine_default_model(monkeypatch):
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    from erkgbench.qa_e2e import run_qa_e2e
    eng = run_qa_e2e._build_engine("goldengraph")
    assert _engine_chat_model(eng) == "gpt-4o-mini"
```

Step 2 MUST first grep how `GoldenGraphQAEngine` stores the llm (it wraps it in `_CountingLLM(llm)` per `engines/goldengraph.py:164`), and define `_engine_chat_model(eng)` to reach `eng`'s counting-wrapper -> inner `OpenAIClient.model`. If the wrapper doesn't expose the inner llm, assert via a lighter seam: factor the model read into a module-level `_chat_model()` helper and unit-test THAT (`run_qa_e2e._chat_model() == ...`) instead of reaching through the engine. Pick the non-brittle path in Step 2.

- [ ] **Step 2: Confirm the engine's llm attribute, run to verify the test fails**

Grep: `grep -n "_CountingLLM\|self._llm\|self.llm\|def __init__" packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/qa_e2e/engines/goldengraph.py`. Define `_engine_chat_model` accordingly, OR switch to the `_chat_model()` helper seam (preferred if the inner llm isn't reachable). Run: `... -k build_engine -v` -> FAIL.

- [ ] **Step 3: Write minimal implementation**

At the top of `run_qa_e2e._build_engine` (or module scope), read env with defaults and thread:

```python
def _chat_model() -> str:
    return os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

def _embed_model():
    return os.environ.get("OPENAI_EMBED_MODEL")  # None -> provider default

# goldengraph branch:
    llm=OpenAIClient(model=_chat_model()),
    embedder=GoldenmatchEmbedder(provider="openai", model=_embed_model()),
# goldenmatch_rag / goldenmatch_entity_rag branches: model=_chat_model()
```

Leave LightRAG/MS-GraphRAG/Graphiti branches unchanged (out of v1 scope; they pin their own model funcs).

- [ ] **Step 4: Run to verify it passes**

Run: `... -k "build_engine or embed" -v` -> PASS. `ruff check run_qa_e2e.py` -> clean.

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/qa_e2e/run_qa_e2e.py packages/python/goldenmatch/benchmarks/er-kg-bench/tests/test_qa_local_llm_config.py
git commit -m "feat(er-kg-bench): _build_engine reads OPENAI_MODEL/OPENAI_EMBED_MODEL (default unchanged)"
```

---

## Task 3: bench-graphrag-qa.yml local-LLM path (Ollama)

**Files:**
- Modify: `.github/workflows/bench-graphrag-qa.yml`

- [ ] **Step 1: Add the `use_local_llm` input + small N default**

Under `workflow_dispatch.inputs`, add:
```yaml
      use_local_llm:
        description: "goldengraph: run against a LOCAL Ollama OSS model (no OpenAI key, non-gating)"
        type: boolean
        default: false
      local_chat_model:
        description: "Ollama chat model when use_local_llm=true"
        default: "qwen2.5:7b-instruct"
      local_embed_model:
        description: "Ollama embedding model when use_local_llm=true"
        default: "nomic-embed-text"
```
If the workflow has an `n_questions`-style input, note in the PR that the first local dispatch should use a SMALL N (~12). If there is NO such input, do not add one here unless trivial -- bound scale via the existing ambiguity/corpus inputs and say so.

- [ ] **Step 2: Add Ollama setup steps to the goldengraph job (guarded by the input)**

Before the "Run goldengraph end-to-end" step, add:
```yaml
      - name: Start local OSS LLM (Ollama)
        if: ${{ inputs.use_local_llm }}
        run: |
          curl -fsSL https://ollama.com/install.sh | sh
          ollama serve > ollama.log 2>&1 &
          for i in $(seq 1 30); do curl -sf http://localhost:11434/api/version && break || sleep 2; done
          ollama pull "${{ inputs.local_chat_model }}"
          ollama pull "${{ inputs.local_embed_model }}"
```

- [ ] **Step 3: Make the run step's env conditional**

The existing run step sets `OPENAI_API_KEY: ${{ secrets.GOLDENGRAPH_OPENAI_API_KEY }}`. Add the local overrides so that when `use_local_llm` is true the engine talks to Ollama. Cleanest: keep ONE run step and express env with conditionals:
```yaml
        env:
          OPENAI_API_KEY: ${{ inputs.use_local_llm && 'ollama' || secrets.GOLDENGRAPH_OPENAI_API_KEY }}
          OPENAI_BASE_URL: ${{ inputs.use_local_llm && 'http://localhost:11434/v1' || '' }}
          OPENAI_MODEL: ${{ inputs.use_local_llm && inputs.local_chat_model || '' }}
          OPENAI_EMBED_MODEL: ${{ inputs.use_local_llm && inputs.local_embed_model || '' }}
          POLARS_SKIP_CPU_CHECK: "1"
```
VERIFY: an EMPTY `OPENAI_BASE_URL`/`OPENAI_MODEL` must behave as UNSET for the default path. Empty `OPENAI_BASE_URL` -> `os.environ.get("OPENAI_BASE_URL", default)` returns `""` (NOT the default!) -> `f"{''.rstrip('/')}/embeddings"` = `"/embeddings"` (broken). So the code must treat empty as unset: use `os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1"` in Task 1, and `os.environ.get("OPENAI_MODEL") or "gpt-4o-mini"` in Task 2. GO BACK and adjust Tasks 1+2 to the `or`-default form (empty-string-safe), add a test with `monkeypatch.setenv("OPENAI_BASE_URL", "")` asserting the default URL. (This is the load-bearing cross-task correctness point.)

- [ ] **Step 4: Validate yaml + commit**

```bash
python -c "import yaml; yaml.safe_load(open('.github/workflows/bench-graphrag-qa.yml')); print('yaml ok')"
git add .github/workflows/bench-graphrag-qa.yml
git commit -m "ci(bench-graphrag-qa): local OSS-LLM (Ollama) run path for goldengraph, non-gating"
```

---

## Final verification (before finishing the branch)

- [ ] `... pytest tests/test_qa_local_llm_config.py -v` -> PASS (both seams, incl. the empty-string-as-unset cases).
- [ ] `ruff check` on providers.py + run_qa_e2e.py + the test -> clean.
- [ ] Existing default behavior unchanged: env unset/empty -> openai endpoint + gpt-4o-mini (covered by tests).
- [ ] `python -c "import yaml; ..."` on the workflow -> ok.
- [ ] Use superpowers:finishing-a-development-branch: push (benzsevern account), open PR targeting main. This is a CI + 2 tiny code seams -- the unit tests are the local validator; the REAL validator is a manual `workflow_dispatch` of `bench-graphrag-qa` with `use_local_llm=true` at small N (Ben triggers, or note it for him). Do NOT claim real numbers until that dispatch runs. Arm `gh pr merge --auto` after CI green. Record memory (the local-LLM lane + the empty-string-as-unset gotcha + the env var names).
- [ ] Note for the dispatch: report the measured wall-clock + answer-match in the PR once the first run lands; if 7B overruns the cap, fall back to `qwen2.5:3b-instruct`.

## Known unknowns to resolve during implementation (call out, don't guess)

- Empty-string env vs unset (Task 3 Step 3): MUST use `or`-default form, not `.get(k, default)`. Load-bearing -- adjust Tasks 1+2 and add empty-string tests.
- The engine's inner-llm attribute path for `_engine_chat_model` (Task 2 Step 2): reach `_CountingLLM` -> `OpenAIClient.model`, or fall back to unit-testing a `_chat_model()` helper directly.
- Exact `bench-graphrag-qa.yml` step names + run command + whether an `n_questions`/scale input already exists (Task 3 Step 1).
- `urlopen` mock shape in the provider test (the `with urlopen(req) as resp: resp.read()` pattern) -- adapt the `_Resp` shim when running Task 1.
- Ollama `qwen2.5:7b-instruct` exact tag availability in the Ollama registry (confirm the tag string; `qwen2.5:7b-instruct` is standard, but verify in the dispatch).
