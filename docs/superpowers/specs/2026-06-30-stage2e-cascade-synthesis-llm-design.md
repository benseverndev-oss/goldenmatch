# Stage-2-E: Cascade Synthesis-LLM Seam — Design

**Status:** Approved (brainstorm), pending implementation plan.
**Date:** 2026-06-30
**Context:** goldengraph real-corpus (stage-2) quality. Follows the 32B-extractor null (scaling the
extractor built a smaller, equally-fragmented graph — scale is not the lever *for extraction*) and the
hybrid win (graph-guided RAG, 0.15→0.30). The cascade idea: scale isn't the lever applied *uniformly*;
it may be the lever applied *surgically* to the low-volume reasoning stage (synthesis). Route a frontier
reasoning model to synthesis only, keep the cheap parallel 7B for the ~400 extractions — solving both
the wall-time problem and the "scale doesn't help" finding in one move.

## Goal

Let the bench engine route the **synthesis** stage (`ask`) to a separate, larger reasoning model (its own
model + endpoint + API key) while **extraction** (`build_kg` / `ingest_corpus`) stays on the injected 7B.
**Opt-in, default off** = byte-identical (synthesis reuses the extraction llm).

## Why this is well-founded

- The goldengraph CORE already separates the two LLM uses — `ingest_corpus(llm=)` (extraction, build
  time) vs `ask(llm=)` (synthesis, answer time). The constraint is ONLY the bench engine, which builds
  ONE `self._llm` and passes it to both.
- 32B-local proved scale does NOT help extraction; the SYNTHESIS bucket failures are weak 7B multi-hop
  reasoning. So the surgical placement (big model on synthesis, 7B on extraction) is exactly what the
  evidence points to.
- **Wall time:** the big model now serves ~20 synthesis calls instead of ~400 extractions, so the run
  stays near the 7B wall — the cascade's core benefit.

## Architecture

One change in `engines/goldengraph.py`, reusing two existing seams.

### 1. Gated synthesis-llm builder

```python
def _build_synthesis_llm(default_llm):
    """A SEPARATE synthesis LLM (own model + endpoint + key) when GOLDENGRAPH_SYNTHESIS_MODEL is set,
    else the extraction llm (byte-identical). The cascade: a frontier reasoning model on the ~20
    low-volume synthesis calls, the cheap 7B on the ~400 parallel extractions."""
    model = os.environ.get("GOLDENGRAPH_SYNTHESIS_MODEL") or ""
    if not model:
        return default_llm                       # unset -> reuse extraction llm (no change)
    import openai
    from goldengraph.llm import OpenAIClient
    base = os.environ.get("GOLDENGRAPH_SYNTHESIS_BASE_URL") or None
    key = os.environ.get("GOLDENGRAPH_SYNTHESIS_API_KEY") or None
    client = openai.OpenAI(base_url=base, api_key=key) if (base or key) else openai.OpenAI()
    return _CountingLLM(OpenAIClient(model=model, client=client))
```

- Reuses `OpenAIClient(model=, *, client=)`'s injectable client (points at DeepSeek / any
  OpenAI-compatible endpoint independent of the Ollama extraction endpoint).
- Reuses `_CountingLLM` (token accounting + `_with_retry` come free).

### 2. Build once in `__init__`

`self._synth_llm = _build_synthesis_llm(self._llm)`. When unset, `self._synth_llm is self._llm`.

### 3. `answer` uses the synthesis llm

`ask(..., llm=self._synth_llm, ...)` (was `self._llm`), and the per-answer token accounting reads
`self._synth_llm`'s counters (the synthesis calls now land on that client). `build_kg` is UNTOUCHED —
extraction stays on `self._llm`.

## Data flow

```
build_kg -> ingest_corpus(llm=self._llm)       [7B extraction, ~400 calls, Ollama]
answer   -> ask(llm=self._synth_llm)            [frontier synthesis, ~20 calls, DeepSeek]
```

Two models, two endpoints, one engine. Default off → both are the same object.

## Error handling / back-compat

- `GOLDENGRAPH_SYNTHESIS_MODEL` unset/empty → `self._synth_llm is self._llm`; every existing run is
  byte-identical.
- `openai.OpenAI(...)` constructs without a network call, so a missing/wrong key fails at the first
  synthesis CALL, not at engine construction — surfaced as a normal answer error.
- The synthesis llm is wrapped in `_CountingLLM`, so its `_with_retry` handles the DeepSeek endpoint's
  rate-limit/transient errors like the extraction path.

## Testing

- **`_build_synthesis_llm` unset → returns the EXACT default object** (the byte-identical guarantee; no
  openai dependency). The critical test.
- **set (MODEL+BASE+KEY) → returns a DIFFERENT `_CountingLLM`-wrapped `OpenAIClient` whose `.model` is
  the configured synthesis model** (`synth._inner.model`). Proves the separate client is built right.
  `openai.OpenAI(base_url=…, api_key=…)` constructs without network, so this is box-safe.
- **No-regression:** existing engine tests unaffected (default path unchanged).
- **Integration validation** = the N=20 hybrid run with a real synthesis endpoint.

## Scope / YAGNI

- **Synthesis only.** NOT a generic per-stage router — the cross-doc-coref model is a separate future
  sub-project.
- **Reuse, don't add** — the existing injectable client + `_CountingLLM`. No new client class.
- **No change** to extraction, build_kg, retrieval, or embeddings.

## Validation gate

- **Run:** N=20 MuSiQue, `GOLDENGRAPH_QA_MODE=hybrid` + `GOLDENGRAPH_SYNTHESIS_MODEL=deepseek-reasoner`
  (+ base_url + key), 7B extraction on Ollama. Compare to 7B-hybrid **0.30**.
- **Outcomes:**
  - **frontier-synth-hybrid > 0.30** → scale IS the lever, *surgically on synthesis* → the cascade works;
    recommend it as the real-corpus mode and revise the "scale isn't the lever" verdict to "not for
    extraction; yes for synthesis." Strong result.
  - **≈ 0.30** → even a frontier synthesizer over good passages doesn't beat the 7B → the bottleneck is
    passage retrieval / question difficulty, not synthesis reasoning. Cascade doesn't help here.
  - **Wall-time confirmation:** the run should land near the 7B-hybrid wall, NOT the 32B's ~48 min —
    proving the cascade's efficiency claim (big model only on the low-volume stage).

## Two run-time constraints (not part of the wiring, but required for validation)

1. **API-key handling:** the DeepSeek key must NOT be passed via `--opts` (Modal logs call args → key
   leak). Use a **Modal secret** exposing `GOLDENGRAPH_SYNTHESIS_API_KEY`, attached to the run function.
   This is a small `modal_bench` change handled in the validation step; the engine wiring is agnostic.
2. **Key availability:** the validation needs a DeepSeek (or other frontier-OSS) API key. The wiring
   ships tested + default-off regardless; only the validation run is gated on the key.

## Files

- Modify: `packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/qa_e2e/engines/goldengraph.py`
  (`_build_synthesis_llm` helper; `self._synth_llm` in `__init__`; use it in `answer`).
- Create: a unit test for `_build_synthesis_llm` under the bench `tests/`.
- Validation (separate, gated on a key): `scripts/distill/modal_bench.py` (a Modal secret for the
  synthesis key) + the N=20 hybrid + frontier-synthesis run.
- Report: `docs/superpowers/reports/2026-06-30-stage2e-cascade-synthesis-llm.md`.
