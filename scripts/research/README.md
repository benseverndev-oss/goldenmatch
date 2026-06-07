# Research prototypes — novel ER framings (2026-06-07)

Exploratory, **not** part of the shipped `goldenmatch` package and **not** wired
into CI. These back the deep-research white-space scan of six novel
entity-resolution framings and the design note:

> `docs/superpowers/specs/2026-06-07-amortized-bayesian-er-1plus3plus6-design.md`

The scan's verdict (all six are open white space to varying degrees; #2's
pairwise/LLM-compressor flanks are already taken, one with a negative result)
is summarised in that note. Two framings are prototyped here as **kill-criterion
experiments** — the cheapest test of each idea's riskiest assumption.

| File | Framing | Question it answers |
|---|---|---|
| `recon_er_experiment.py` | **#3** self-supervised mutual reconstructability | Does masked-field reconstruction from cluster-mates rank clusterings by F1? (the likelihood-viability gate for the 1+3+6 program) |
| `diff_er_pipeline.py` | **#4** joint differentiable blocking+matching | Can a single global clustering loss backprop through a differentiable blocker so it learns to retain true pairs? |

## Running

```bash
# #3 — reuses the committed dqbench_adapters loaders + F1 harness.
#      stdlib-only similarity/stats; prefers rapidfuzz when present.
python scripts/research/recon_er_experiment.py --dataset febrl3
python scripts/research/recon_er_experiment.py --dataset dblp-acm --datasets-dir datasets
#   (needs `pip install recordlinkage` for febrl3, or fetch DBLP-ACM via
#    `python scripts/run_benchmarks.py --datasets dblp-acm --download`)

# #4 — torch skeleton + synthetic-data sanity demo (no real dataset).
python scripts/research/diff_er_pipeline.py --epochs 150
```

Both degrade gracefully when their optional deps/data are absent (clean skip,
exit 0) so they never break a bare checkout.

## Key finding so far (from #3's local sanity runs)

Reconstructability **tracks F1 strongly on recall-side perturbations** (Spearman
~0.9 on the synthetic harness) but is **precision-blind to over-merges that keep
each record's twin** — a pure reconstruction *likelihood* cannot see false
merges. That is not a bug: it empirically localises *which half* of the 1+3+6
objective each piece owns — reconstructability (#3) supplies the recall
likelihood, and the **microclustering prior (#1)** supplies the missing
precision pressure on cluster size. A crude size-penalty stand-in is included
only to illustrate the gap; the real prior is the amortized net's job (step 2).
