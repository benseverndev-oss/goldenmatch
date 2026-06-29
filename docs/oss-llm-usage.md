# How we use OSS LLMs (goldengraph local lane)

A practical summary of how open-source LLMs are wired into the goldengraph KG/RAG
evidence harness and the er-kg-bench head-to-head. The point: run the whole pipeline
on a **free, key-free, self-hosted model** instead of a paid API.

## Why

- **Billing-free real numbers.** goldengraph's QA pipeline normally calls `gpt-4o-mini`.
  When the OpenAI key is out of quota (or we just don't want to pay), a local OSS model
  serves the same role at $0 and no secret.
- **It's also the research substrate.** The whole "0.15 -> 0.672" quality arc was measured
  on a local 7B; the conclusion (schema-constrained deterministic ingest beats a 32B) only
  means something *because* it's the same free model throughout.

## Models

| role | model | notes |
|------|-------|-------|
| chat (extraction + synthesis + query classification) | `qwen2.5:7b-instruct` | the default / workhorse |
| chat (teacher-ceiling probe) | `qwen2.5:32b` | A100 only; measured 0.569 (<= the 7B) |
| embeddings | `nomic-embed-text` (768-dim) | served by Ollama; replaces `text-embedding-3-large` |

All served through **Ollama's OpenAI-compatible endpoint** (`http://localhost:11434/v1`),
so nothing in our code needs an Ollama-specific client.

## The seam — how a local model plugs in

The pipeline talks to "OpenAI"; we just repoint that at Ollama via env vars:

```bash
OPENAI_BASE_URL=http://localhost:11434/v1   # -> Ollama instead of api.openai.com
OPENAI_API_KEY=ollama                        # dummy; Ollama ignores it
OPENAI_MODEL=qwen2.5:7b-instruct             # chat model (project-defined var)
OPENAI_EMBED_MODEL=nomic-embed-text          # embedding model (project-defined var)
```

- The `openai` SDK **auto-reads `OPENAI_BASE_URL`/`OPENAI_API_KEY`**, so any engine built on
  it (goldengraph's `OpenAIClient`, the RAG baselines, LightRAG's openai funcs) hits Ollama
  for free — the *only* code change needed was to stop hardcoding the model name and read it
  from `OPENAI_MODEL` / `OPENAI_EMBED_MODEL` instead.
- `goldenmatch/embeddings/providers.py::OpenAIProvider.embed` was the one hardcoded endpoint;
  it now honors `OPENAI_BASE_URL`.
- **Empty-string gotcha:** the CI lane sets these to `''` when *not* local. Always read with
  `os.environ.get(k) or <default>` (not `get(k, default)`) so an empty value falls back.

## Two serving lanes

### 1. GitHub Actions — `bench-graphrag-qa.yml`
Installs Ollama in the runner (`large-new-64GB`, CPU inference), `ollama serve &`, pulls the
chat + embed models, swaps the run-step env to the local endpoint. Driven by the `local_llm`
dispatch input (empty = use OpenAI). Honest but slow (~2 min/question for a 7B on CPU) — keep
`max_questions` small. Non-gating / informational; the deterministic gates stay the blocking
signal.

### 2. Modal GPU — `scripts/distill/modal_bench.py` (the iteration loop, ~20x faster)
An `@app.function` (A10G default, A100 for 32B) builds the goldengraph native wheel + pulls the
Ollama models (all cached on a Modal Volume), starts `ollama serve` on the GPU, points the bench
at the local endpoint, runs the eval, persists the result markdown to the Volume.

```bash
# creds from Infisical (project a99885f0-..., env dev); never echo the secret values
modal run --detach scripts/distill/modal_bench.py \
  --engine goldengraph --eval end_to_end --n 60 --ambiguity 0.0 --spawn \
  --opts $'GOLDENGRAPH_QA_MODE=auto\nGOLDENGRAPH_RELATION_VOCAB=works_at,located_in,acquired,authored,part_of\nGOLDENGRAPH_SCHEMA_CANON=1'
```

`--gpu a100 --chat qwen2.5:32b` runs the bigger teacher; `--engine <name>` swaps the engine for
the head-to-head; `--merged auto` serves a fine-tuned model (see below).

**Modal ops that matter** (the box OOM-reaps the local `modal.exe` ~1 min in):
- Use `modal run --detach` + `.spawn()` (fire-and-forget, server-side) so a local kill can't
  cancel the run.
- Results land on a Volume (`results/*.md`); poll the Volume with a Monitor, don't tail the CLI.

## How the OSS model is used inside the pipeline

For goldengraph the same model does three jobs: **extraction** (text -> typed triples),
**synthesis** (subgraph -> answer), and **query classification** (route the question). A small
model extracts a *noisy* graph (paraphrased predicates, reversed edges, under-merged entities),
so the win was making the graph clean deterministically rather than asking the model to be smarter:

- **Schema-constrained + direction-canonical ingest** (`goldengraph/schema.py`, gated
  `GOLDENGRAPH_SCHEMA_CANON=1` + `GOLDENGRAPH_RELATION_VOCAB`): snap each predicate to a closed
  vocab, flip passive/inverse-phrased edges to canonical direction, drop out-of-schema edges.
  This took the 7B from 0.586 -> **0.672** end-to-end and **beats the 32B (0.569)** — task
  structure, not model size, was the lever.

## Fine-tuning a local model (key-free QLoRA) — `scripts/distill/`

We also tried to *train* the 7B to fix its defects, entirely free (no teacher API):

- `gen_gold_pairs.py` synthesizes `(text -> canonical triple)` training pairs from the
  engineered corpus's ground truth (no teacher LLM), with reverse-phrased examples to teach
  direction.
- `modal_train.py` QLoRA-fine-tunes `Qwen/Qwen2.5-7B-Instruct` on Modal, self-evals on a disjoint
  heldout, and merges + persists the model to the `gg-distill` Volume.
- Serving a fine-tuned model: Ollama imports the merged HF safetensors directly
  (`run_bench_distilled --merged auto`) — copy the model off the Volume symlink first (Ollama
  rejects symlink-escaping `FROM` paths).

**Verdict:** the fine-tune did **not** beat the free deterministic path — it couldn't train out
the base model's "subject = first-mentioned entity" prior (`reverse_direction_acc` 0.0 -> 0.078),
and its predicate accuracy (0.82) was worse than deterministic vocab-snapping. The harness is kept
for a future task where a real model-quality gap is shown.

## Head-to-head on the local 7B

`run_qa_e2e --engine <name>` runs any engine on the same local model. The openai-SDK engines
(`text_rag`, `goldenmatch_rag`, `goldenmatch_entity_rag`) just needed the model name threaded from
the env. The KG competitors differ in effort: **LightRAG** is pip-installable + injected local
funcs; **MS-GraphRAG** needs a `settings.yaml` `api_base` + is slow/expensive on a 7B; **Graphiti**
needs an external FalkorDB service. goldengraph is compared at its best config (schema-constrained).

## Canonical references

- Spec / investigation record: `docs/superpowers/specs/2026-06-28-goldengraph-distilled-extractor-design.md`
- Tuning flags: every `GOLDENGRAPH_*` env var is documented in the tuning reference.
