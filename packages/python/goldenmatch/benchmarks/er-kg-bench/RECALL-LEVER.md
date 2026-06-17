# The recall lever: semantic candidate generation on ER-KG-Bench

> Measurement summary. Reproduce with `scripts/embed_recall_probe.py` (in-house /
> openai locally; the free downloadable tiers via the `embed-recall-probe`
> `workflow_dispatch`, since they need torch). Not a committed board row -- this
> records *why* the hard classes fail and *what* moves them, to gate a future
> product change (semantic blocking) on measured need.

## The wall

ER-KG-Bench scores how well a tool recognizes that two differently-written names
are the same entity. Every system on the board -- goldenmatch included -- fails the
same three classes: **abbreviation** (`IBM` = `International Business Machines`),
**synonym/brand** (`Coumadin` = `warfarin`), and **cross-lingual** (`München` =
`Monaco di Baviera`).

These fail at **candidate generation, not scoring.** Every tool only compares names
that look alike *by spelling*. `IBM` and `International Business Machines` share
almost no characters, so they are never put in front of the matcher -- the correct
pair is never generated. Three independent measurements all landed here:

- **Phase-2 embedder** (cosine OR-term on framework rows): byte-identical on the
  dominant classes -- a measured no-op.
- **Keyed `auto+llm`** (LLM pair filter): can't create a pair that blocking never
  generated -- a precision filter, not a recall generator.
- **Phase-3 mem0 LLM merge layer**: net-negative (0.048 < its 0.066 floor); it
  recognizes variants but stores them separately. (See `adapters/FIDELITY.md`.)

The lever they all point at: **generate candidate pairs by *meaning*, not spelling**
-- a semantic embedding that "knows" `IBM` ≈ `International Business Machines`.

## What we measured

`goldenmatch(emb-ann)` already does embedding-ANN candidate generation; the embedder
is swappable. We swept the cosine threshold per embedder tier over the 206-record
corpus and scored per failure class. Best-overall-F1 row per tier:

| candidate-gen embedder | overall F1 | abbreviation | cross-lingual | synonym/brand |
|---|---|---|---|---|
| in-house char-ngram (free, **no world knowledge** -- today's `emb-ann`) | 0.451 | 0.21 | 0.35 | 0.12 |
| MiniLM English (free download: `all-MiniLM-L6-v2`) | 0.515 | **0.85** | 0.21 | 0.08 |
| MiniLM multilingual (free download: `paraphrase-multilingual-MiniLM-L12-v2`) | 0.451 | 0.50 | **0.91** | 0.02 |
| OpenAI semantic (paid: `resolve_provider("openai")`) | 0.558 | 0.80 | 0.77 | 0.18 |

(in-house / OpenAI measured locally; free tiers from CI run `27715172974`.)

## Findings

1. **The lever is real and large.** World-knowledge candidate generation lifts the
   wall classes ~2-4x over the char-ngram baseline: abbreviation 0.21 → 0.80-0.85,
   cross-lingual 0.35 → 0.77-0.91.

2. **The win is FREE -- and free even beats paid, per class.** The free English
   model tops abbreviation (0.85 > OpenAI 0.80); the free multilingual model tops
   cross-lingual (0.91 > OpenAI 0.77). No paid key is required.

3. **No single free model does *both* as well as OpenAI does both at once** (English
   misses cross-lingual; multilingual is softer on abbreviation). The natural free
   answer is to **ensemble** the two (union their candidate pairs), which should
   beat OpenAI on both for $0 -- to be confirmed.

4. **synonym/brand is walled for *every* embedder** (max 0.18, even paid).
   `Coumadin` ↔ `warfarin` is specialized domain knowledge a general embedding does
   not carry. That class is a different problem (a knowledge base / domain model),
   not "a bigger embedder."

## Two constraints for any product change

- **Free ≠ zero-dependency.** These free models need torch/sentence-transformers;
  goldenmatch's true *zero-config* default is the torch-free char-ngram embedder.
  So semantic candidate generation is a **free, opt-in capability** (install an
  extra / enable a flag), not the no-deps default -- unless a torch-free (ONNX)
  semantic embedder is added later. On this benchmark it is a committable free row
  (CI installs the extra).

- **Name-only emb-ANN (best free 0.515) is still below goldenmatch's `auto+fields`
  (0.602).** The headline only moves if semantic candidate generation is wired
  **into** the multi-field auto pipeline, so `auto+fields` also gets the
  abbreviation/cross-lingual recall on top of its existing scoring.

## Next (NOT built -- gate on measured need)

Wire semantic-ANN candidate generation into goldenmatch's blocking as an opt-in
"semantic blocking" pass (free multilingual model, or a two-model ensemble by
default), so the multi-field auto pipeline gains the abbreviation/cross-lingual
recall. **Gate on a measurement:** `auto+fields` + semantic blocking must clear
0.602 to count as a headline move. The synonym/brand class will remain walled
regardless and should be scoped out (or addressed separately via a domain/knowledge
source). This is a real product change and gets its own spec + plan before any code.
