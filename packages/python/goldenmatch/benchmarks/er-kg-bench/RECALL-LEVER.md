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

## Semantic blocking shipped (in-product) -- now with per-source confirming scorers

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

Re-measured with the confirming scorers wired (`GOLDENMATCH_NATIVE=0`, in-house
embedder, numpy ANN fallback -- reproducible by anyone, no key):

| metric | auto+fields | auto+fields+semantic | delta |
|---|---|---|---|
| **overall F1** | 0.6018 | 0.6018 | +0.000 |
| overall P | 0.7857 | 0.7857 | +0.000 |
| overall R | 0.4877 | 0.4877 | +0.000 |
| abbreviation F1 | 0.7727 | 0.7727 | +0.000 |
| nickname_alias F1 | 0.8539 | 0.8539 | +0.000 |
| synonym_brand F1 | 0.1667 | 0.1667 | +0.000 |
| cross_lingual F1 | 0.7692 | 0.7692 | +0.000 |
| typo F1 | 1.0000 | 1.0000 | +0.000 |
| org_suffix F1 | 1.0000 | 1.0000 | +0.000 |
| temporal_version F1 | 0.5714 | 0.5714 | +0.000 |
| cross_document_exact F1 | 1.0000 | 1.0000 | +0.000 |
| same_name_collision F1 | 0.3556 | 0.3556 | +0.000 |

The two rows are still **byte-identical** (both `tp=198 fp=54 fn=208`). No class
moved. Wiring the confirming scorers (the prior diagnosis' proposed fix) did
**not** unstick the headline on this corpus.

### Acceptance verdict vs 0.602

**NOT MET (headline did not move).** `auto+fields+semantic` F1 = `auto+fields`
F1 = **0.6018** (rounds to the documented 0.602; fractionally below 0.602 as a
raw float). The capability still ships opt-in at **zero precision cost** (`fp`
unchanged at 54 -- the additive union never adds a false merge here), but it is
not a headline mover on ER-KG-Bench.

### Precision cost (measured, not pre-guarded)

**Zero, measured.** The approved stance was "measure the precision cost, don't
pre-guard it." On this corpus the cost is nil: `fp` is identical (54) between the
two rows, and **no** class's precision regressed -- no initialism collision, no
alias-table error materialized, because the confirming pairs that survive to
clustering are all pairs the baseline already merged. There is no precision
finding to report and no scoped follow-up needed on the precision side.

### Why it's STILL a no-op: two distinct walls, both now isolated

Per-source confirming scorers are the right design, but two corpus-specific
mechanisms keep the headline pinned. Both were isolated by instrumenting the
pipeline at the `_semantic_blocking_pairs` return, the post-`_apply_postflight`
pair set, and the `build_cluster_frames` call site:

1. **The confirming scorers can't confirm the corpus's NOISY surface forms.**
   `initialism_match` / `alias_match` are correct on clean expansions --
   measured `initialism_match("IBM", "International Business Machines") = 1.0`,
   `("NATO", "North Atlantic Treaty Organisation") = 1.0`,
   `("WHO", "World Health Organization") = 1.0`. But ER-KG-Bench mentions carry
   real-world suffix/parenthetical noise, and the strict check collapses the
   moment it appears: `initialism_match("IBM", "International Business Machines
   Corporation (Armonk, NY)") = 0.0`, `("IBM", "IBM Corp.") = 0.0`. So for the
   actual gold abbreviation pairs the confirming scorer returns **0.0**, and the
   only score those pairs carry into `all_pairs` is the **ANN char-ngram cosine**
   (e.g. `IBM` <-> `International Business Machines Corporation (Armonk, NY)`
   lands at **0.863**, not 1.0). `alias` adds nothing here (0 non-singleton
   blocks on this corpus -- the `given_names`/`business` seed doesn't cover
   these orgs).

2. **Auto-config's postflight raises the threshold to 0.895 and drops exactly
   those candidates.** The committed config is RED on this corpus
   (`stop_reason=BUDGET_ITERATIONS`, `failing_subprofile=blocking`: with `name`
   at cardinality 0.95 most blocks are singletons), and `_apply_postflight`
   applies a `threshold` adjustment **0.8 -> 0.895**. Measured pre-postflight,
   `all_pairs` holds 2871 pairs; postflight drops 1873 of them, with the dropped
   mass concentrated in the **0.8 band (1638 pairs)** and **0.9 band (214)** --
   i.e. precisely the 0.83-0.89 char-ngram-cosine scores the abbreviation pairs
   land at. `IBM` <-> `International Business Machines...` (0.863) and three of
   the four `NATO` variants (0.836, 0.844, 0.835) are dropped here. The few
   confirmed/near-exact pairs that DO survive postflight (score >= 0.99, e.g.
   `IBM` <-> `IBM Corp.` at 1.0) are pairs **string blocking already generated
   and the baseline already merged**, so after `dedup_pairs_max_score` the
   canonical cluster-input pair set is **219 in both runs, 0 new** -- which is
   why the clustering output is byte-identical.

Per-class, which sources *can* move what, and why they didn't here:

- **initialism -> abbreviation:** fires at 1.0 on clean expansions, **0.0** on
  the noisy ones this corpus actually contains; the surviving signal for those
  pairs is ANN cosine ~0.83-0.86, which postflight's 0.895 threshold rejects.
- **alias -> known synonyms in the seed table only:** bounded by the small
  `given_names` / `business` refdata seed; 0 non-singleton blocks here. The
  `synonym_brand` class (e.g. `Coumadin`/`warfarin`) is the same class the
  embedder sweep found walled for *every* embedder (max 0.18) -- a knowledge-base
  problem, not an embedder/scorer problem.
- **ANN -> typo / suffix / transliteration:** these classes are *already* at
  F1 1.0 (`typo`, `org_suffix`) under `auto+fields`, so there's no recall left to
  add; the cross-lingual gains the multilingual model showed need a *semantic*
  embedder, not the char-ngram one this offline path uses.

**Bottom line:** the recall-lever capability is real, shipped opt-in, and now
scores each candidate source with its own confirming scorer at zero precision
cost. But on ER-KG-Bench the headline does not move, for two independent reasons:
(a) the strict initialism/alias confirmers return 0.0 on the corpus's noisy
real-world surface forms, leaving only the char-ngram ANN cosine (~0.83-0.86) for
the genuine abbreviation pairs; and (b) auto-config's postflight raises the match
threshold to **0.895** on this RED config and drops exactly that 0.83-0.89 band.
Moving the headline here needs *both* (i) a noise-tolerant confirmer (strip
`Corp./Inc./(City, ST)` suffixes + organisation/-zation spelling normalization
before the initialism/alias check) **so the confirmer returns 1.0 on the noisy
mentions and the pair clears any threshold**, and (ii) a world-knowledge semantic
embedder for the candidates the char-ngram path can't generate at all.
synonym/brand stays walled regardless.

### Scoped follow-ups (recall side -- precision side has none)

1. **Noise-tolerant initialism/alias confirmers.** Normalize the expansion
   before the initialism check: drop trailing legal suffixes (`Corp.`, `Inc.`,
   `Corporation`), parentheticals (`(Armonk, NY)`, `(WHO)`), and reconcile
   `-ization`/`-isation`. This is what turns the measured 0.0 back into 1.0 on
   the noisy mentions, so the confirmed pair clears the threshold regardless of
   postflight. (Highest-leverage fix: it is the difference between the
   abbreviation pairs scoring ~0.86 and scoring 1.0.)
2. **A world-knowledge semantic embedder for the ANN source** (free multilingual
   model, or the two-model ensemble) for the candidates the char-ngram cosine
   cannot generate at all -- the lever the earlier embedder sweep proved
   (abbreviation 0.21 -> 0.80-0.85, cross-lingual 0.35 -> 0.91).

Neither needs an initialism-block-size cap or any precision guard: the measured
precision cost on this corpus is zero, so capping is solving a problem the data
does not show.
