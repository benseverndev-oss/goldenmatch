# Step 3 results — EIG active design over the ER partition posterior

Runner: `scripts/research/active_partition_er.py`. Builds on step 2: draws a
Monte-Carlo posterior over partitions from the trained amortized head, then
compares pair-selection strategies under a noiseless same-entity oracle.
First run: 2026-06-07, torch 2.12 CPU. Reproduce:

```bash
python scripts/research/active_partition_er.py --train-epochs 220 --eval-datasets 12
```

## Setup

All three strategies share the **same** posterior pool and the **same**
must-link/cannot-link transitive-closure conditioning; only the SELECTION rule
differs, isolating its value:

- **eig** — argmax binary-entropy `H_b(p_ij)` over the CURRENT (conditioned)
  posterior. Under a noiseless oracle this is the exact Bayesian-optimal single
  query: `EIG(i,j) = I(A_ij; Z) = H_b(p_ij)`. Re-scored every round, so pairs
  resolved by transitivity (`p_ij -> 0/1`) are skipped.
- **static** — argmax `H_b` over the INITIAL posterior, fixed order. Classic
  per-pair uncertainty sampling; ignores transitivity in selection.
- **random** — random unlabelled pair.

## Headline (12 eval datasets, ~16 records/sim, pool=160, budget=24)

Consensus pairwise-F1 vs #labels:

| #labels | eig | static | random |
|---:|---:|---:|---:|
| 0 | 0.669 | 0.669 | 0.669 |
| 4 | 0.828 | 0.828 | 0.670 |
| 8 | **0.900** | 0.883 | 0.753 |
| 12 | **0.943** | 0.923 | 0.753 |
| 16 | 0.945 | 0.943 | 0.768 |
| 24 | **0.956** | 0.945 | 0.783 |

Total posterior uncertainty (sum of `H_b` over unlabelled pairs) vs #labels:

| #labels | eig | static | random |
|---:|---:|---:|---:|
| 0 | 20.03 | 20.03 | 20.03 |
| 8 | **10.38** | 11.32 | 18.78 |
| 16 | **3.90** | 6.27 | 17.61 |
| 24 | **1.18** | 3.18 | 16.87 |

## Honest read

1. **Uncertainty collapse is the robust, large win.** eig drives the posterior's
   total pairwise uncertainty down ~**2.7x faster** than per-pair static (1.18 vs
   3.18 bits left at 24 labels) and ~14x faster than random. This is the metric
   EIG directly optimises, and the dominance is consistent across the whole
   curve — the faithful demonstration of the framing-#6 claim.

2. **F1: a consistent small edge.** eig >= static at every budget (e.g. 0.900 vs
   0.883 at 8 labels), but the gap is modest once enough labels accumulate —
   pairwise-F1 is a downstream proxy both strategies eventually do well on. The
   advantage WIDENS in sparse/harder regimes: a smaller-pool smoke run had eig
   reach 90% of its final F1 in **16 labels vs static's 24** (~33% fewer), and
   eig 1.000 vs static 0.939 at budget. So the label-efficiency win is real but
   regime-dependent; on easy averaged settings it shows up mostly as faster
   uncertainty reduction rather than higher F1.

3. **Random is far behind throughout** — confirms naive selection wastes labels;
   most random pairs are already-certain non-matches in a sparse-positive regime.

4. **Transitivity is doing the work.** eig beats static purely because it
   re-scores after each label: the must-link/cannot-link closure makes induced
   pairs certain, and eig spends its next label elsewhere while static keeps
   querying pairs that are already logically resolved.

## Step-3 gate: PASS

partition-aware EIG matches-or-beats per-pair uncertainty on F1 at every budget,
and collapses posterior uncertainty substantially faster, at equal label cost.

## Caveats / carry-forward

- **Noiseless oracle + logical conditioning only.** Real labels are noisy, and a
  full Bayesian update would also shift belief on *similar* undetermined pairs
  (soft correlational update), not just the logically-forced ones. The runner
  flags this as a TODO (importance-reweighting / SMC over the head).
- **Simulated vector-records**, small N. The real-program version needs the
  learned real-schema encoder (step-1 finding) and posterior sampling validated
  on a real dataset; and a comparison to a true active-ER baseline (DIAL-style)
  rather than the static-uncertainty stand-in.
- Greedy myopic (one-step) EIG; batch/non-myopic acquisition is future work.
