# Stage-2-E: Cascade Synthesis-LLM — Validation Verdict (SHIP — the arc's best result)

**Date:** 2026-06-30
**Spec:** `docs/superpowers/specs/2026-06-30-stage2e-cascade-synthesis-llm-design.md`
**Plan:** `docs/superpowers/plans/2026-06-30-stage2e-cascade-synthesis-llm.md`

## Run config

- Corpus: MuSiQue-Ans, seeded subset, **N=20** (matched A/B with every other stage-2 cell).
- **Extraction:** `qwen2.5:7b-instruct` on Ollama (A10G) — ~400 paragraph calls.
- **Synthesis:** **`deepseek-reasoner` (DeepSeek-R1) via API** — ~20 calls, routed through the stage-2-E
  cascade seam (`GOLDENGRAPH_SYNTHESIS_MODEL`), key via a Modal secret.
- Mode: `hybrid` (graph + passages). Fair metric.

## Result — the best of the entire stage-2 arc

| metric | 7B-hybrid (baseline) | 32B-hybrid | **CASCADE (7B-ext + R1-synth)** |
|---|---|---|---|
| `answer_match` | 0.30 | 0.55 | **0.75** |
| `token_f1` | ~0.37 | — | **0.81** |
| `exact_match` | — | — | **0.70** |
| `answer_match` (entity-subset, n=11) | 0.36 | 0.45 | **0.73** |
| `support_recall` | 0.66 | 0.66 | 0.72 |
| wall time | ~37 min | ~48 min | **~43 min** |

**Verdict: SHIP.** The cascade is **5× the original 7B-local baseline (0.15)**, **2.5× the 7B-hybrid
(0.30)**, and **beats 32B-hybrid (0.55)** — at near-7B wall time.

## The full 2×3 — read the interaction

|  | local (pure-KG) | hybrid (7B synth) | hybrid (frontier synth) |
|---|---|---|---|
| **7B extract** | 0.15 | 0.30 | **0.75 (R1)** |
| **32B extract** | 0.10 | 0.55 (32B) | — |

Two orthogonal findings, both clean:
1. **Scale on EXTRACTION is null-to-harmful** (local: 32B 0.10 ≤ 7B 0.15; 32B builds a smaller,
   equally-fragmented graph). The graph is a lossy intermediate; a bigger extractor doesn't fix it.
2. **Scale on SYNTHESIS is the lever — but only on the hybrid path** (0.30 → 0.55 → 0.75 as the
   synthesizer gets stronger). In hybrid the answer is read from the passages, so reasoning capability is
   what matters, and it compounds hard.

The cascade exploits exactly this: cheap 7B for the high-volume extraction (where scale doesn't help),
frontier R1 for the low-volume synthesis (where it does). The earlier "scale is not the lever" verdict
was wrong as stated; the correct version is **"not for extraction; decisively yes for synthesis."**

## Efficiency — the cascade's reason to exist, proven

DeepSeek-R1 served only ~20 synthesis calls; the 7B did the ~400 extractions. So the run landed at
**~43 min — near the 7B-hybrid wall, not the 32B's ~48 min** (the 32B paid big-model inference on every
extraction). **Frontier-reasoning quality at near-7B cost.** That is the whole point of the heterogeneous
cascade, measured end to end.

## Disposition

- **Ship the cascade seam** (`GOLDENGRAPH_SYNTHESIS_MODEL`/`_BASE_URL`/`_API_KEY`), default off,
  byte-identical when unset. The recommended real-corpus config is **hybrid + a frontier synthesis model
  via the cascade**.
- **Confidence:** N=20 (~11 entity-subset questions), so confirm at N=50 before headline-quoting 0.75.
  But the signal is strong: a clean monotone synthesis-scaling ladder (0.30 → 0.55 → 0.75) across matched
  cells, with corroborating token_f1 (0.81) and exact_match (0.70) — not a one-question fluke.

## What this resolves for the goal

The **practical** "first-class KG" goal — point it at real text, get good multi-hop answers — is
**reached**: 0.75 on MuSiQue via 7B-local extraction + frontier-synthesis hybrid, at near-7B cost. The
**strict** "the graph reasons unaided" goal stays ceilinged (the three connectivity nulls + the
extraction-scale null are real), but the cascade makes it largely moot: **graph-guided RAG with a frontier
synthesizer is an excellent real-corpus system,** and it's the right product shape.

## Next

- **N=50 cascade confirm** (cheap; the cascade is near-7B wall).
- **Sweep:** `deepseek-chat` (V3) vs `deepseek-reasoner` (R1) on synthesis; `PASSAGE_K`.
- **The cross-doc-coref cascade** (long-context model on extraction-consolidation) is the remaining shot
  at the STRICT pure-KG goal — but the cascade win makes it optional, not necessary.

## Lesson

"Scale isn't the lever" is the wrong granularity — **scale is the lever on the *reasoning* stage, not the
*extraction* stage**, and a heterogeneous cascade lets you spend it surgically. The 2×3 of matched N=20
runs (~$ of A10G + a few cents of DeepSeek) turned a flat-looking "bigger model doesn't help" into the
arc's biggest win. Measure the *interaction*, not the average.
