# WhoIsWho SND — external entity-resolution validation for goldenmatch

**What this proves:** goldenmatch resolves entities correctly on a respected,
human-curated corpus **we did not build** — so the "our nodes are correctly
resolved" claim survives the "you rigged the benchmark" objection.

**Benchmark:** [WhoIsWho](https://github.com/THUDM/WhoIsWho) / OAG-Bench (THUDM,
KDD'23 + KDD Cup 2024), **SND** (From-scratch Name Disambiguation). Public
leaderboard, human-curated ground truth, official Pairwise-F1 metric.

---

## The task, mapped to goldenmatch

For each ambiguous author **name**, partition all papers bearing that name into
clusters — **one per distinct real person**. The blocking key (the name) is
*given*, so this is exactly what `dedupe_df` does: cluster within a blocked
record set. We build **one goldenmatch frame per name** (rows = papers) and call
`dedupe_df` (or `run_graph_er`); the resulting `clusters` are the predicted
authors.

The name string is **constant within a block → zero signal.** What discriminates
two real people who share a name is *who they publish with* — so **co-author set
overlap is the make-or-break feature**, expressed as a first-class positive-weight
scorer (`scorers.SetJaccardScorer`, registered as `set_jaccard`) rather than
string-fuzz on a concatenation.

| goldenmatch column | source | role |
|---|---|---|
| `__paper_id__` | paper `id` | maps clusters back to papers |
| `coauthors` | other authors' names, normalized+sorted set | **primary (relational)** |
| `orgs` | affiliations on the paper, normalized set | strong (sparse in na-v3) |
| `venue` | venue | medium |
| `text` | title + abstract + keywords | topical |
| `year` | year | weak |

Set-valued cells are `"|"`-delimited sorted strings (`normalize.encode_set`);
`set_jaccard` decodes them back to sets and returns exact Jaccard.

## Metric

**Pairwise-F1, macro-averaged over names** (the WhoIsWho SND leaderboard metric).
Per name: TP/FP/FN over same-cluster paper *pairs*; F1 per name; mean over names.
`score.pairwise_f1_macro` is the standalone implementation, **parity-tested
against goldenmatch's own `core.evaluate.evaluate_clusters`** (`tests/test_score.py`)
so the number is defensible.

## Results — na-v3 **valid** (80 names, 46,367 papers)

Single fixed ground truth (`sna_valid_ground_truth.json`), so it is scorable
offline. gpt-free, deterministic. `GOLDENMATCH_NATIVE=0`.

<!-- RESULTS_TABLE -->
| engine | Pairwise-F1 (macro) | precision | recall | wall | note |
|---|--:|--:|--:|--:|---|
| `all_singletons` | 0.000 | – | 0.000 | 10s | trivial floor |
| `text_only` (unresolved straw) | 0.009 | 0.952 | 0.005 | 777s | topical similarity ALONE |
| `all_one` | 0.375 | 0.256 | 1.000 | 10s | merge-everything floor |
| `coauthor_only` | 0.440 | 0.824 | 0.332 | 18s | co-author Jaccard alone |
| **`relational`** (co-author OR org+topic) | **0.452** | **0.844** | 0.352 | 799s | **headline** |

### How it stacks up against the published SND field

Same corpus, same Pairwise-F1 metric ([OAG-Bench](https://arxiv.org/html/2402.15810v2), [WhoIsWho KDD'23](https://arxiv.org/pdf/2302.11848)):

| method | Pairwise-F1 | what it is |
|---|--:|---|
| `all_one` floor | 0.375 | merge everything |
| **goldenmatch `relational`** | **0.452** | co-author Jaccard, zero-tuning |
| IUAD | 0.616 | published baseline |
| LAND | 0.611 | published baseline |
| G/L-Emb | 0.635 | graph + local embeddings baseline |
| SND-all (toolkit) | 0.892 | full reference pipeline |
| KDD Cup 2024 winner | 0.891 | tuned competition system |

**Honest placement: below the field.** A first-pass, hand-thresholded application
of a general-purpose ER engine lands under even the weakest published SND
baselines (~0.61) and well under the ~0.89 full pipelines. It is NOT competitive
with methods purpose-built and tuned for SND — and this benchmark does not claim
to be. Two things make the gap the *right* kind:

- **The published research validates the axis.** OAG-Bench finds Word2Vec beats
  OAG-BERT and semantic embeddings alone underperform -- the discriminative signal
  is *relational*, not semantic. Our co-author-first design is aimed correctly; it
  is under-featured, not mis-directed.
- **The gap is a concrete Phase-1 list**, not a redirection: per-name adaptive
  clustering (learned/DBSCAN distance per block) instead of one global Jaccard
  threshold; co-author *graph* structure fused with `record_embedding` (already in
  goldenmatch, unwired here); external OAG org/venue data (na-v3's `org` is empty).

The claim this benchmark DOES support -- **resolved ≫ naive** (0.452 vs text-only
0.009 and all-one 0.375, with the co-author signal carrying 97% of the result) --
holds decisively, on a corpus we did not build. That is the substrate claim,
framed honestly; "beat the SND leaderboard" was never the goal and we are not there.

### The finding

- **Topical similarity alone is worthless for SND** (`text_only` ≈ 0.01): papers
  by the same person are *not* textually near-duplicate. This is the "unresolved"
  straw baseline the substrate must beat — and it beats it by ~45×.
- **The co-author relational signal carries ~97% of the result.** `relational`
  (0.452) adds only **+0.012 F1** over `coauthor_only` (0.440) — and pays 45× the
  wall (799s vs 18s) for it, because the org+topic path's `token_sort` over long
  text is the whole cost. The relational signal is the engine; org+topic is a thin
  garnish on na-v3 (whose `org` fields are largely empty).
- **Precision is high (~0.84), recall is the lever (~0.35).** When two papers share
  a specific co-author they really are the same person; but many same-author papers
  share *no* co-author directly, and transitive chaining only reaches so far. The
  WhoIsWho leaderboard leaders reconstruct org/venue from external OAG data and run
  OAG-BERT + a GNN over the co-author graph to close the recall gap. Lifting recall
  (richer relational features / collective propagation) is the Phase-1 lever.

## Engines (`run_snd.py --engine`)

| engine | what |
|---|---|
| `relational` | `dedupe_df` — co-author Jaccard **OR** org+topic (headline) |
| `coauthor_only` | co-author Jaccard alone (relational ablation) |
| `text_only` | topical similarity only (unresolved straw baseline) |
| `zero_config` | `dedupe_df(df)` unassisted |
| `graph_er` | `run_graph_er` collective/relational propagation — **WIP** (see below) |
| `all_one` / `all_singletons` | trivial metric-calibrating floors |

**`graph_er` status (decision "also try the evidence-propagation path"):** wired
and runnable, but currently collapses to singletons on SND. `run_graph_er`
re-ingests each entity from CSV and its collective path seeds from the paper
entity's *attribute*-derived candidate pairs; with na-v3's near-empty org/text
attributes there are effectively no seed pairs, and (a suspected) `__row_id__`
reassignment on re-ingest breaks the paper↔co-author join that would supply the
relational pairs. The validated realization of the co-author signal is the
first-class `set_jaccard` scorer in the `dedupe_df` `relational` engine; fixing
the `graph_er` wiring for the relation-primary shape is tracked Phase-1 work
(tunable via `SND_GRAPHER_ALPHA` / `SND_GRAPHER_REL_THRESHOLD`).

## Running

```bash
# from packages/python/goldenmatch (with goldenmatch importable)
export GOLDENMATCH_NATIVE=0 POLARS_SKIP_CPU_CHECK=1

# headline on the full valid set
python benchmarks/whoiswho-snd/run_snd.py --split valid --engine relational

# fast spike on the first 5 names
python benchmarks/whoiswho-snd/run_snd.py --split valid --engine relational --limit 5

# tests (offline, no corpus, ~1s)
python -m pytest benchmarks/whoiswho-snd/tests/ -q
```

**CI:** `.github/workflows/bench-whoiswho-snd.yml` (`workflow_dispatch`, not
ci-required). `runner=direct` runs on the GH runner (valid set fits ubuntu-latest);
`runner=modal` drives a Modal box for the full v3 scale (needs `MODAL_TOKEN_ID` /
`MODAL_TOKEN_SECRET`; see `modal_app.py`).

## Data

Fetched on demand from AMiner's public LFS
(`https://lfs.aminer.cn/misc/ND-data/na-v3/…`) into a **gitignored** `data/` dir
— the corpus is research-use / redistribution-restricted and is **never
committed**. `fetch.py` handles download + cache; `WHOISWHO_DATA_DIR` overrides
the cache location. Valid split ≈ 110 MB; full v3 (train+test) ≈ 470 MB.

## Layout

```
fetch.py       download + cache na-v3 (gitignored data/)
normalize.py   name/org/set normalization (shared by frame + scorer)
to_frame.py    WhoIsWho JSON -> per-name goldenmatch DataFrame
scorers.py     set_jaccard plugin scorer (co-author/org set overlap)
configs.py     relational / coauthor_only / text_only configs
score.py       Pairwise-F1 macro (+ parity with core.evaluate)
run_snd.py     fetch -> dedupe/graph_er -> score, per engine
modal_app.py   Modal app for the beefy-box / full-v3 run
tests/         offline unit + end-to-end smoke (no network)
```
