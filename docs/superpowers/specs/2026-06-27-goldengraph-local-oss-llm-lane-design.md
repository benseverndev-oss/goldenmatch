# GoldenGraph local OSS-LLM bench lane -- real results without an API key

**Status:** design
**Date:** 2026-06-27
**Owner:** Ben Severn
**Worktree:** `gg-local-llm` (branch `feat/goldengraph-local-llm-lane`, off main)

## Problem

The whole goldengraph evidence/QA program is split into deterministic key-free GATES (green) + opt-in
real-LLM CONFIRMATION lanes (`bench-graphrag-qa.yml`). The real-LLM lanes have been billing-blocked
since 2026-06-21 -- `OPENAI_API_KEY` is out of quota (429 `insufficient_quota`), so there are NO real
end-to-end numbers (extraction quality, multi-hop answer-match) for goldengraph. Ben wants real results
NOW, for free, as a stopgap until OpenAI credits are topped off.

## Goal

A `bench-graphrag-qa` run path that uses a **local OSS LLM served inside the GitHub runner** (an
OpenAI-compatible Ollama server) instead of the OpenAI API, so the goldengraph engine runs its genuine
extraction -> resolve -> store -> retrieve -> synthesize path against a real (if smaller) model -- $0,
no secret, no billing. The integration is near-free: goldengraph's `OpenAIClient` already honors
`OPENAI_BASE_URL` (it calls `openai.OpenAI()`), and the embedding provider needs one small
`OPENAI_BASE_URL`-honoring change. The LLM/embed MODEL names are threaded via env.

This is a THIRD tier between the existing two: deterministic gates (blocking) -> **local-OSS real-path
lane (free, non-gating, NEW)** -> frontier-model credible numbers (billing-gated). The new middle tier
is the "prove the real path works + get internally-consistent numbers without a key" lane.

## Non-goals (honest scope)

- **NOT a credible "goldengraph vs the field" headline.** A CPU-served 7B model is materially weaker
  than gpt-4o-mini at JSON-adherent extraction + multi-hop synthesis, so ABSOLUTE numbers are lower and
  not publishable as a frontier comparison. The value is (a) proving the real-LLM code path runs e2e
  without a key, and (b) internally-consistent goldengraph numbers + a FAIR same-model comparison where
  applicable. Frontier numbers stay on the billing-gated lane.
- **v1 switches goldengraph + the goldenmatch_rag / goldenmatch_entity_rag controls only** (they
  construct `OpenAIClient` / `GoldenmatchEmbedder`, which follow the env). LightRAG / MS-GraphRAG /
  Graphiti pin their own model funcs (`gpt_4o_mini_complete`, hardcoded model strings) and do NOT
  follow `OPENAI_MODEL` -- switching them to a local model is a separate, more invasive change, noted
  as future. So v1 is NOT the full 4-way local head-to-head; it is goldengraph's own real numbers.
- **NOT a HARD gate.** OSS-LLM output is non-deterministic (not byte-stable across llama.cpp/Ollama
  builds even at temperature 0), so the lane is INFORMATIONAL -- it writes the results artifact, never
  blocks the merge queue. The deterministic key-free gates stay the blocking signal.
- **No GPU.** GitHub gives no free GPU for public repos -> CPU inference on `large-new-64GB` (16c/64GB),
  which bounds the feasible model size + corpus scale (see Open risks).

## Architecture

### 1. Local server (CI, no code)

A new Ollama setup in the goldengraph job: install Ollama (official `curl | sh`), `ollama serve &`,
`ollama pull <chat-model>` + `ollama pull <embed-model>`. Ollama exposes an OpenAI-compatible API at
`http://localhost:11434/v1` (`/chat/completions`, `/embeddings`). Models:
- chat: `qwen2.5:7b-instruct` (Apache-2.0, strong JSON adherence at 7B; fallback `qwen2.5:3b-instruct`
  if 7B is too slow for the job cap).
- embeddings: `nomic-embed-text` (small, fast, OpenAI-compatible embeddings endpoint).

Env the run step sets: `OPENAI_BASE_URL=http://localhost:11434/v1`, `OPENAI_API_KEY=ollama` (dummy,
Ollama ignores it but the providers require non-empty), `OPENAI_MODEL=<chat-model>`,
`OPENAI_EMBED_MODEL=<embed-model>`.

### 2. Embedding provider honors OPENAI_BASE_URL (`goldenmatch/embeddings/providers.py`, MODIFY)

`OpenAIProvider.embed` hardcodes `https://api.openai.com/v1/embeddings`. Change to read the base from
the env (default unchanged), mirroring the openai SDK convention:

```
base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
url = f"{base}/embeddings"
```

Backward-compatible: env unset -> identical URL. This is the ONLY goldenmatch core change; it is a
general improvement (respect the standard OpenAI base-url override) and is unit-testable by asserting
the constructed URL under a set/unset env (mock `urlopen`).

### 3. Model names from env (`erkgbench/qa_e2e/run_qa_e2e.py`, MODIFY)

`_build_engine` hardcodes `model="gpt-4o-mini"`. Thread the chat + embed model from env (default
unchanged):

```
_CHAT_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
_EMBED_MODEL = os.environ.get("OPENAI_EMBED_MODEL")  # None -> provider default (OpenAI)

# goldengraph branch:
llm=OpenAIClient(model=_CHAT_MODEL),
embedder=GoldenmatchEmbedder(provider="openai", model=_EMBED_MODEL),
```

Apply `_CHAT_MODEL` to the OTHER `OpenAIClient`-based engines too (`goldenmatch_rag`,
`goldenmatch_entity_rag`) so the same-model comparison holds where the engines follow the env. The
LightRAG/MS-GraphRAG/Graphiti branches are LEFT AS-IS (their model funcs don't read the env; out of v1
scope). Unit-testable: set env -> `_build_engine("goldengraph")` constructs the LLM with the right
`.model`.

### 4. CI run path (`.github/workflows/bench-graphrag-qa.yml`, MODIFY)

Add a `workflow_dispatch` input `use_local_llm` (default `false`). When true, the goldengraph job:
(a) runs the Ollama setup steps, (b) sets the four env vars above on the run step INSTEAD of
`OPENAI_API_KEY: ${{ secrets... }}`, (c) installs `goldenmatch[embeddings]` is NOT needed (Ollama
serves embeddings via the openai provider + the base-url change). Keep the existing budget cap (Ollama
returns `usage`, so `BudgetTracker` still accounts; the cap is effectively a call/length guard, cost
is ~0). Bounded scale via the existing `n_questions`/ambiguity inputs (default to a SMALL N for the
first run -- see Open risks). The job stays `if: engine == 'all' || 'goldengraph'` and remains
non-gating (writes `results_qa_e2e_goldengraph.json` + the markdown artifact).

## Components / file structure

- `goldenmatch/embeddings/providers.py` (MODIFY): `OpenAIProvider.embed` honors `OPENAI_BASE_URL`.
- `packages/python/goldenmatch/.../qa_e2e/run_qa_e2e.py` (MODIFY): `_CHAT_MODEL`/`_EMBED_MODEL` from env,
  threaded into the OpenAIClient-based engines.
- `packages/python/goldenmatch/.../tests/test_qa_local_llm_config.py` (CREATE): wheel-free unit tests --
  (a) provider URL honors/ignores `OPENAI_BASE_URL`; (b) `_build_engine` reads `OPENAI_MODEL`/
  `OPENAI_EMBED_MODEL`.
- `.github/workflows/bench-graphrag-qa.yml` (MODIFY): `use_local_llm` input + Ollama setup steps +
  env-swap on the goldengraph run step.
- `docs-site` tuning note (optional, in the rollout sweep): document `OPENAI_BASE_URL`/`OPENAI_MODEL`/
  `OPENAI_EMBED_MODEL` as supported overrides.

## Error handling

- Provider with `OPENAI_BASE_URL` set but server down -> `urlopen` raises (URLError) -> the engine run
  fails loudly in the lane (correct: a broken local server should not silently pass).
- `OPENAI_API_KEY` still required non-empty by the provider; the lane sets a dummy `ollama`.
- Ollama model pull failure -> the `ollama pull` step fails the job (visible), no silent fallback.
- Env unset everywhere -> byte-identical to today (OpenAI endpoint, gpt-4o-mini) -> existing
  billing-gated lanes unchanged.

## Testing strategy (TDD)

The two code seams are wheel-free + deterministic -> unit tests (no Ollama, no network): monkeypatch
`OPENAI_BASE_URL` and assert the provider's request URL; monkeypatch `OPENAI_MODEL`/`OPENAI_EMBED_MODEL`
and assert `_build_engine` constructs the engine LLM/embedder with those model names (inject a fake
`urlopen`/capture the `OpenAIClient.model`). The CI lane is the real end-to-end validator (dispatch
`use_local_llm=true` at small N, confirm a non-zero answer-match + a written artifact). No HARD gate.

## Open risks

- **CPU inference speed vs the 60-min job cap.** A 7B Q4 on 16 cores ~ 10-15 tok/s. Per-doc extraction +
  per-question synthesis over a corpus can be hundreds of calls; the job ALREADY spends 15-40 min on the
  wheel build + extraction. MITIGATION: default the first dispatch to a SMALL N (e.g. 10-15 questions,
  one ambiguity), and fall back to `qwen2.5:3b-instruct` if the 7B run approaches the cap. Document the
  measured wall in the PR. If even small-N overruns, the Railway box (bench-gen pattern) is the escape
  hatch (future).
- **Model quality floor.** If the 7B model can't produce parseable extraction JSON reliably, extraction
  F1 collapses and the numbers are uninformative. MITIGATION: qwen2.5-instruct is chosen for JSON
  adherence; the lane PRINTS extraction parse-failure counts (goldengraph already defends JSON parsing)
  so a too-weak model is visible, not silent.
- **Determinism.** Non-deterministic -> informational only, never a gate (stated). Re-runs vary; report
  a single run's numbers as indicative, not a frozen baseline.
- **Embeddings via Ollama.** `nomic-embed-text` returns a different dimensionality than OpenAI
  `text-embedding-3-large`; goldengraph's retrieval is cosine over whatever the embedder returns
  (self-consistent within a run), so dimensionality mismatch is fine as long as the SAME embedder is
  used for query + entities (it is). No cross-provider vector mixing.
- **Scope honesty.** v1 = goldengraph (+ goldenmatch_rag/entity_rag) on the local model, NOT the 4-way
  head-to-head. The PR + artifact must say so, so a reader doesn't mistake it for a frontier comparison.
