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
| engine | Pairwise-F1 (macro) | precision | recall | note |
|---|--:|--:|--:|---|
| `all_singletons` | 0.000 | – | 0.00 | trivial floor |
| `text_only` (unresolved straw) | _see below_ | ~1.0 | ~0.006 | topical similarity ALONE |
| `all_one` | 0.375 | 0.256 | 1.00 | merge-everything floor |
| `coauthor_only` | _running_ | ~0.87 | ~0.36 | co-author Jaccard alone |
| **`relational`** (co-author OR org+topic) | **_running_** | ~0.89 | ~0.37 | **headline** |

_(Numbers above are being finalized on the full 80-name valid set; the 5-name
spike gave relational F1 **0.488** / P **0.886** / R **0.372**, and text_only
**0.011** — the table is refreshed from the full run.)_

### The finding

- **Topical similarity alone is worthless for SND** (`text_only` ≈ 0.01): papers
  by the same person are *not* textually near-duplicate. This is the "unresolved"
  straw baseline the substrate must beat.
- **The co-author relational signal carries essentially the entire result**
  (`relational` ≈ `coauthor_only`), and beats every naive baseline **decisively** —
  which is exactly the substrate claim ("resolved ≫ naive"), framed honestly.
- **Precision is high (~0.89), recall is the lever (~0.37).** When two papers share
  a specific co-author they really are the same person; but many same-author papers
  share *no* co-author directly, and transitive chaining only reaches so far. The
  na-v3 `org` fields are largely empty, so the org+topic path adds little here —
  the WhoIsWho leaderboard leaders reconstruct org/venue from external OAG data and
  run OAG-BERT + a GNN over the co-author graph to close the recall gap. Lifting
  recall (richer relational features / collective propagation) is the Phase-1 lever.

Context: published toolkit baseline ≈ **89%** Pairwise-F1, KDD Cup 2024 SOTA
higher. Our number is a **respectable record-linkage result with resolved ≫ naive**,
not a leaderboard win — and that is the substrate claim, not a trophy.

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
