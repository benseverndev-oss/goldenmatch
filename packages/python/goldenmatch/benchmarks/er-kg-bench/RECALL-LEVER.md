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

## Semantic blocking shipped (in-product)

Semantic blocking is now a real, opt-in capability: `dedupe_df(df,
semantic_blocking=True)` unions three FREE deterministic candidate sources onto
the normal candidate set, all offline / no key / no faiss (numpy ANN fallback):

- **in-house char-ngram ANN** (the `emb-ann` embedder, as a candidate-gen pass);
- **initialism** (acronym block: `IBM` <- `International Business Machines`);
- **refdata alias** (canonicalization on `given_names` / `business` seed tables).

The union is **additive by construction** -- new pairs are appended before the
`dedup_pairs_max_score` seam, which keeps the max score per canonical pair, so it
can never drop an existing pair. The bench exercises it as a new row,
`goldenmatch(auto+fields+semantic)`, identical to `auto+fields` except for the
flag (`adapters/goldenmatch_adapter.py`, mode `auto_fields_semantic`).

### Measured: auto+fields vs auto+fields+semantic (206 records, offline)

Run with the two goldenmatch rows over `dataset/records.csv`
(`GOLDENMATCH_NATIVE=0`, in-house embedder, numpy ANN fallback -- reproducible by
anyone, no key):

| metric | auto+fields | auto+fields+semantic | delta |
|---|---|---|---|
| **overall F1** | 0.6018 | 0.6018 | +0.000 |
| overall P | 0.786 | 0.786 | +0.000 |
| overall R | 0.488 | 0.488 | +0.000 |
| abbreviation F1 | 0.773 | 0.773 | +0.000 |
| nickname_alias F1 | 0.854 | 0.854 | +0.000 |
| synonym_brand F1 | 0.167 | 0.167 | +0.000 |
| cross_lingual F1 | 0.769 | 0.769 | +0.000 |
| typo F1 | 1.000 | 1.000 | +0.000 |
| org_suffix F1 | 1.000 | 1.000 | +0.000 |
| temporal_version F1 | 0.571 | 0.571 | +0.000 |
| cross_document_exact F1 | 1.000 | 1.000 | +0.000 |
| same_name_collision F1 | 0.356 | 0.356 | +0.000 |

The two rows are **byte-identical** (both `tp=198 fp=54 fn=208`). No class moved.

### Acceptance verdict vs 0.602

**Tied, not cleared.** `auto+fields+semantic` F1 = `auto+fields` F1 = **0.6018**
(rounds to the documented 0.602 baseline, fractionally below 0.602 as a raw
float). So:

- **>= baseline: MET (exactly tied)** -- the additive union guarantees recall
  can't fall, and precision held (`fp` unchanged at 54), so it never regresses.
- **>= 0.602 (headline move): NOT MET** -- it does not *raise* the headline on
  this corpus. The capability ships (opt-in, zero precision cost), but it is not
  a headline mover here.

### Why it's a no-op here: the wall moved from blocking to scoring

Semantic blocking is working -- it generates **3,466 extra candidate pairs**. But
the committed auto-config matchkey scores them on `name`+`entity_type`+`context`
(weighted `ensemble`, threshold **0.8**), and only **1,108** of the new pairs
clear 0.8. Every one of those 1,108 is a pair the **baseline blocking already
generated and already merged** -- exact duplicates (`IBM`/`IBM`) or word-reordered
forms (`FIFA World Cup 2018`/`2018 FIFA World Cup`) that string blocking catches
anyway. The genuinely *new* recall-lever pairs (the abbreviation / synonym /
cross-lingual cases where the strings do **not** look alike) score well below 0.8
on the name field and are rejected at scoring. So the candidate set grew, but the
final clustering didn't change.

This is exactly the wall the earlier embedder sweep predicted for the **offline
char-ngram** tier: its cosine approximates *character* overlap, not world
knowledge (`IBM` <-> `International Business Machines` ~0.05). The free in-house
ANN bridges typo / org-suffix / transliteration (shared characters), but cannot
generate the abbreviation/synonym pairs at all, and even when initialism/alias
*do* surface a pair, the name scorer still demands string similarity to confirm
it. Which classes the sources *can* move, and why they didn't here:

- **initialism -> abbreviation:** surfaces `IBM`/`International Business Machines`
  as a candidate, but `ensemble`(name) scores it ~0, < 0.8 -> rejected at scoring.
- **alias -> known synonyms in the seed table only:** bounded by the small
  `given_names` / `business` refdata seed; a synonym not in the seed (most of the
  `synonym_brand` class, e.g. `Coumadin`/`warfarin`) is never generated. This is
  the same class the embedder sweep found walled for *every* embedder (max 0.18).
- **ANN -> typo / suffix / transliteration:** these classes are *already* at
  F1 1.0 (`typo`, `org_suffix`) under `auto+fields`, so there's no recall left to
  add there; the cross-lingual gains the multilingual model showed need a
  *semantic* embedder, not the char-ngram one this offline path uses.

**Bottom line:** the recall-lever capability is real and now shipped opt-in and
zero-cost (additive, no precision hit), but on ER-KG-Bench the headline does not
move because (a) the free offline embedder lacks the world knowledge to *generate*
the abbreviation/synonym pairs, and (b) the auto-config name scorer's 0.8
threshold *rejects* the few semantic candidates that do get surfaced. To actually
move the headline you need both a world-knowledge (semantic) embedder for
candidate generation **and** a scorer that doesn't require string similarity to
confirm a semantically-blocked pair (e.g. the LLM scorer, or a semantic
similarity scoring field) -- the embedder swap alone is necessary but not
sufficient. synonym/brand stays walled regardless (knowledge-base problem, not an
embedder problem).
