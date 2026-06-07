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

## Key finding (real data, 2026-06-07 — full results in `RESULTS-3-reconstructability.md`)

Run on **real Febrl3 and DBLP-ACM**, not just the synthetic harness:

| Dataset | Kernel | Spearman(recon, F1) | gold argmax | gate |
|---|---|---|---|---|
| Febrl3 (PII) | char | **+0.944** | yes | PASS |
| DBLP-ACM (bibliographic) | char | +0.591 | no | FAIL |
| DBLP-ACM (bibliographic) | idf | **+0.647** | no | PASS |

Three takeaways, all carried into the design note:
1. **The likelihood is viable** — reconstructability ranks clusterings by F1 at
   +0.944 on distinctive PII (gold on top, monotone decay). Step-1 gate clears.
2. **The kernel is data-dependent**: char Jaro-Winkler wins on PII; bibliographic
   text needs an **IDF-weighted token kernel** (discount shared venue/title
   vocabulary) to clear the gate. => step 2 should *learn* the reconstructor,
   not fix a kernel.
3. **Over-merge precision-blindness persists** on bibliographic data regardless
   of kernel — a pure reconstruction *likelihood* can't see false merges, and a
   crude size penalty doesn't rescue it. => empirical case for pairing it with a
   *learned* **microclustering prior (#1)**, which is the precision half of the
   objective.
