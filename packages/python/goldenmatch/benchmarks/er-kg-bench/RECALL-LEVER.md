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

## Semantic blocking shipped (in-product) -- recall lever working (abbreviation +5.3pp)

Semantic blocking is a real, opt-in capability: `dedupe_df(df,
semantic_blocking=True)` unions three FREE deterministic candidate sources onto
the normal candidate set, all offline / no key / no faiss (numpy ANN fallback),
and -- as of the per-source-confirm change -- scores **each source with its OWN
confirming scorer** instead of the multi-field name scorer:

- **in-house char-ngram ANN** (the `emb-ann` embedder, as a candidate-gen pass)
  -> kept at its cosine score, gated by `ann_threshold` (default 0.5);
- **initialism** (acronym block: `IBM` <- `International Business Machines`)
  -> confirmed by `initialism_match` (1.0 when one name is the other's
  initialism; raw string similarity is ~0);
- **refdata alias** (canonicalization on `given_names` / `business` seed tables)
  -> confirmed by `alias_match` (1.0 when both canonicalize to the same alias).

The union is **additive by construction** -- new pairs are appended before the
`dedup_pairs_max_score` seam, which keeps the max score per canonical pair, so it
can never drop an existing pair. The bench exercises it as a new row,
`goldenmatch(auto+fields+semantic)`, identical to `auto+fields` except for the
flag (`adapters/goldenmatch_adapter.py`, mode `auto_fields_semantic`).

### Measured: auto+fields vs auto+fields+semantic (206 records, offline)

Measured with the full lever wired -- per-source confirming scorers, noise-tolerant
+ acronym-aware initialism, AND raw-name keying (`GOLDENMATCH_NATIVE=0`, in-house
embedder, numpy ANN fallback -- reproducible by anyone, no key):

| metric | auto+fields | auto+fields+semantic | delta |
|---|---|---|---|
| **overall F1** | 0.602 | **0.612** | **+0.010** |
| overall P | 0.786 | 0.790 | +0.004 |
| overall R | 0.488 | 0.500 | +0.012 |
| **abbreviation F1** | 0.773 | **0.826** | **+0.053** |
| abbreviation R | 0.630 | 0.704 | **+0.074** |
| abbreviation P | 1.000 | 1.000 | +0.000 |

All other classes unchanged. The headline moves on the back of abbreviation recall,
at **zero precision cost** (abbreviation precision stays 1.000; overall `fp` does
not rise).

### Acceptance verdict vs 0.602

**MET.** `auto+fields+semantic` = **0.612 > 0.602**, a real recall-positive move
driven by the abbreviation class (+5.3pp F1, +7.4pp recall). A/B confirms it's
load-bearing: with the semantic sources keyed off the *standardized* (title-cased)
name the row is byte-identical to `auto+fields` (the old wall); keyed off the *raw*
name it adds abbreviation pairs the ANN source alone did not catch.

### Precision cost (measured, not pre-guarded)

**Zero, measured.** The approved stance was "measure, don't pre-guard." abbreviation
precision stays **1.000** and overall `fp` does not rise -- the confirmed initialism
pairs are true matches on this corpus (no initialism collision materialized). No
precision guard (block-size cap, secondary signal) is warranted: it would solve a
problem the data does not show.

### How it was unstuck: four walls, the last was upstream title-casing

Moving the headline took clearing four walls in sequence, each isolated by
instrumenting `_semantic_blocking_pairs`, the post-`_apply_postflight` pair set,
and the `build_cluster_frames` call site:

1. **Per-source confirming scorers.** The union first scored its acronym/alias
   candidates with the multi-field *name* ensemble, which scores `IBM` <->
   `International Business Machines` ~0. Fixed by scoring each source with its own
   confirming scorer (`initialism_match` / `alias_match` at 1.0; ANN keeps cosine).
2. **Noise-tolerant + acronym-aware initialism.** A 1-token acronym (`IBM`) has no
   multi-word initialism, and real expansions carry suffix/parenthetical noise
   (`...Corporation (Armonk, NY)`). Fixed in `derive_initialism`: strip
   parentheticals + legal-form tokens anywhere, and treat a short all-caps token
   as its own initialism key -- so `IBM` and its noisy expansion both derive `IBM`.
3. **(superseded theory)** an earlier postflight-threshold guess (0.8 -> 0.895
   dropping the 0.83-0.89 ANN-cosine band) -- real on an all-sources run, but NOT
   the blocker once the initialism confirmer returns 1.0 (a 1.0 pair clears any
   threshold).
4. **Upstream standardize title-casing (the actual last wall).** auto-config's
   standardize step title-cases the name *before* semantic blocking runs, so by
   the time `derive_initialism` sees it, `IBM` is `Ibm` and the all-caps acronym
   signal is gone -> no block key -> never co-locates with the expansion. **Fixed
   by keying the semantic-blocking sources off the RAW, pre-standardize name**
   (captured into an internal `__raw__<col>` before standardize, gated behind
   `semantic_blocking`, stripped from output; ANN keeps the standardized column).
   This is the change that moved abbreviation 0.773 -> 0.826.

### What still does NOT move (honest scope)

- **synonym_brand (e.g. `Coumadin`/`warfarin`): walled, F1 0.167.** Bounded by the
  small `given_names`/`business` alias seed (0 non-singleton alias blocks on these
  orgs). Same class the embedder sweep found walled for *every* embedder (max
  ~0.18) -- a knowledge-base problem, not a scorer/embedder one. Extending the seed
  table or wiring a domain KB is the lever; out of scope here.
- **cross_lingual: unchanged.** The free in-house char-ngram ANN can't generate
  cross-lingual candidates (no shared characters); the multilingual gains the
  embedder sweep showed (0.35 -> 0.91) need a *semantic* embedder -- the documented
  opt-in upgrade (`ann_model="local:<st-model>"`), not the free offline default.
- **typo / org_suffix: already F1 1.000** under `auto+fields` -- no recall left.

### Scoped follow-ups (optional, recall side -- precision side needs nothing)

1. **Extend the alias seed / wire a domain KB** to crack synonym_brand.
2. **Semantic embedder for the ANN source** (free local multilingual model) for the
   cross-lingual candidates the char-ngram path can't generate -- already a
   documented opt-in (`ann_model`), just not the offline default.
