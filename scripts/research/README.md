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
| `recon_er_experiment.py` | **#3** self-supervised mutual reconstructability | Does masked-field reconstruction from cluster-mates rank clusterings by F1? (the likelihood-viability gate for the 1+3+6 program) — **step 1**, results in `RESULTS-3-reconstructability.md` |
| `amortized_partition_er.py` | **#1** amortized neural partition posterior | Can one trained net emit a calibrated posterior over the ER partition, with a *learned* microclustering prior (no size penalty)? — **step 2**, results in `RESULTS-2-amortized-partition.md` |
| `active_partition_er.py` | **#6** EIG active design over the partition posterior | Does picking labels by partition-EIG (transitivity-aware) beat per-pair uncertainty at equal budget? — **step 3**, results in `RESULTS-6-active-design.md` |
| `real_schema_encoder.py` | **#1** learned string encoder + zero-shot transfer | Does a head trained on a string simulator transfer zero-shot to real schemas? — **step 4**, `RESULTS-real-schema-encoder.md` (answer: **no** with a from-scratch encoder — sim-to-real gap dominates) |
| `pretrained_transfer_er.py` | **#1** frozen pretrained encoder + zero-shot transfer | Does a FROZEN pretrained text encoder fix step 4's transfer failure? — **step 5**, `RESULTS-pretrained-transfer.md` (answer: **largely yes** — real F1 jumps 6–14x and is calibrated, though still below a tuned baseline). `--simulator {basic,rich}` selects the step-6 fix. |
| `richer_simulator.py` | **#1** realistic simulator (closes the boundary gap) | Does a richer simulator (high diversity + realistic corruption) fix step 5's over-merging? — **step 6**, `RESULTS-rich-simulator.md` (answer: **yes** — over-merge fixed, exact 60/60 cluster count, real F1 ~doubles to 0.61–0.68) |
| `diff_er_pipeline.py` | **#4** joint differentiable blocking+matching | Can a single global clustering loss backprop through a differentiable blocker so it learns to retain true pairs? |
| `landscape_er.py` | **topology/geometry** — ER as a sculpted attractor landscape | Does the landscape mechanism (carve basins / raise ridges / global re-flow) beat a discrete split/merge loop optimising the SAME objective? — `RESULTS-landscape-er.md` (answer: **no — COSMETIC**. With a calibrated objective it gives the *identical* partition to the discrete loop; an earlier "+0.10" win was a θ-calibration artifact) |
| `recall_certificate.py` | **recall assurance** — unsupervised recall estimation via capture-recapture | Can capture-recapture across decorrelated matchers estimate a matcher's recall with NO labels? — `RESULTS-recall-certificate.md` (answer: **yes, early PASS** — tracks true recall within ~0.03–0.09 MAE with disjoint-field-group matchers; the first idea to clear its kill-criterion. Caveats: small-scale, accuracy-vs-independence tension) |

## Second research arc: ER as topology/geometry (2026-06-07, in progress)

A separate exploration (after the 1+3+6 arc above) reframing ER as **landscape
sculpting**: records are marbles dropped into a potential surface; stranded
marbles carve new basins (recall), impure basins get a ridge raised to split
them (precision); iterate. A six-angle prior-art scan found this **genuinely
open** — ER has never been framed as records settling into attractor basins of a
sculpted landscape (no Hopfield/energy/attractor ER exists), and the
bidirectional add/split polarity is unattested in density-landscape clustering;
the only crowded angle is the iterative-refinement *loop* itself (pBlocking,
Gruenheid, Sayari), so novelty rests on the *mechanism*. `landscape_er.py` is the
kill-criterion prototype and it **FAILS**: with a calibrated correlation-clustering
objective the landscape mechanism gives the *identical* partition to a discrete
split/merge loop (cosmetic), and a plain threshold often beats both. An earlier
apparent "+0.10 F1" win was a θ-calibration artifact that vanished once the
objective was fixed — see `RESULTS-landscape-er.md`. Same outcome shape as the
1+3+6 arc: novelty validated by the scan, competitiveness not. Arc closed.

## Third direction: unsupervised recall assurance (2026-06-07, early PASS)

Derived *from* the two failures (don't compete on accuracy on saturated
benchmarks at the clustering layer; find an unsaturated axis with no incumbent
baseline). The unsolved operational problem: precision is cheap to estimate,
recall is not — every ER deployment ships blind on recall. `recall_certificate.py`
estimates recall with **no labels** via capture-recapture (the census-undercount
dual-system math) across decorrelated matchers, and **clears its kill-criterion**:
it tracks true recall within ~0.03 MAE (Febrl3, ≈unbiased) and ~0.09 MAE
(DBLP-ACM, conservative). The first direction to pass a fair test — see
`RESULTS-recall-certificate.md` for the trajectory (the accuracy-vs-independence
tension) and the honest caveats (small-scale, schema-dependent sweet spot).

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
