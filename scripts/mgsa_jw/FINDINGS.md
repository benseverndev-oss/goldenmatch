# MGSA-JW — measured, does not earn the Rust port

**Verdict: do not port.** MGSA-JW works exactly as its abstract claims, and the
claim is still not a reason to add it to goldenmatch. It loses to `token_sort`,
a scorer this repo already ships with a Rust kernel, on 6 of 7 labeled datasets
while costing ~17x more per pair.

Prototype: `mgsa.py` (algorithm), `bench.py` (comparison), `sweep.py`
(hyperparameters), `tests/test_mgsa.py`. Nothing is registered as a goldenmatch
scorer, so the parity gate and coverage floors do not fire.

## Sources

- ConvJ / ConvJW — Rozinek & Mareš, *Fast and Precise Convolutional Jaro and
  Jaro-Winkler Similarity*, FRUCT 35 (2024). Eqs. (10)–(20), Algorithms 3–4.
- MGSA-JW — Rozinek, *A Bimodal Gaussian Convolutional Jaro-Winkler Similarity
  for Token-Aware Entity Matching*, ADBIS 2026 (submission 753067).

## The paper's claim reproduces

The failure mode is real and the fix works. On the abstract's own motivating
pair, `Mrs. Yvonne Abbott` / `Abbott, Yvonne`:

| comparator | score |
|---|---|
| ConvJW (published) | 0.116 |
| MGSA-JW | 0.846 |

That is the "under 5% F1, indistinguishable from noise" collapse, and the token
peak rescues it. The ablation (`token_peak=False`) confirms the second Gaussian
is what does the work, not the one-to-one assignment: removing it drops the pair
back to 0.263 and the 7-dataset mean from 0.8327 to 0.7786.

## …and it still does not matter here

The paper benchmarks against JW-class comparators. **goldenmatch never runs
Jaro-Winkler alone.** `token_sort` is a shipped scorer (`parity/goldenmatch.yaml`,
Rust kernel in `goldenfuzz-core`) that sorts tokens before comparing — it
already handles the exact swap MGSA-JW was built for, scoring that same pair
0.812. The relevant comparison is therefore against `token_sort` and against
ensembles, not against JW.

max-F1, 500 positives + 3x token-sharing hard negatives per dataset, seed
20260822:

| comparator | anchor_person | dblp_acm | febrl3 | historical_50k | ncvr_syn | orgs_hard | synthetic | **mean** |
|---|---|---|---|---|---|---|---|---|
| `token_sort` | 0.8707 | 0.9419 | **0.8335** | **0.7339** | 0.9136 | **0.6849** | 0.9087 | **0.8410** |
| `ens_max(jw,ts)` | **0.9668** | 0.9495 | 0.8309 | 0.6558 | 0.9151 | 0.6167 | **0.9497** | 0.8407 |
| **`mgsa_jw`** | 0.9229 | **0.9532** | 0.7950 | 0.6969 | 0.9113 | 0.6461 | 0.9038 | 0.8327 |
| `ens_mean(jw,ts)` | 0.8772 | 0.9496 | 0.7846 | 0.7303 | **0.9163** | 0.6519 | 0.9108 | 0.8315 |
| `jaro_winkler` | 0.9668 | 0.9473 | 0.7787 | 0.6499 | 0.9116 | 0.5444 | 0.9497 | 0.8212 |
| `mgsa_jw[no-token-peak]` | 0.9231 | 0.9406 | 0.7103 | 0.5510 | 0.8988 | 0.5229 | 0.9038 | 0.7786 |
| `convjw` | 0.9195 | 0.9406 | 0.7103 | 0.5486 | 0.8990 | 0.5172 | 0.9018 | 0.7767 |

MGSA-JW beats plain Jaro-Winkler (+0.0115) and published ConvJW (+0.0560). It
**loses to `token_sort` (−0.0083)** and to `ens_max` (−0.0080), and wins outright
on 1 of 7 datasets (`dblp_acm`, where it is the best comparator measured).

Cost, µs/pair (pure Python, so read ratios not absolutes):

| comparator | median | vs `token_sort` |
|---|---|---|
| `token_sort` | 5.5 | 1.0x |
| `jaro_winkler` | 11.1 | 2.0x |
| `mgsa_jw` | 96.1 | **17.5x** |

The greedy token alignment is O(t₁·t₂) token-pair quality evaluations — each
itself a Jaro-Winkler — before any character work begins. That is inherent to
the algorithm, not to Python, so a Rust port shrinks the constant but keeps the
shape.

## The knobs were swept, not assumed

Two things the ADBIS abstract does not specify had to be decided here: the token
quality measure `Q`, and whether misalignment is judged against the original
index or the peak that produced the match. A verdict resting on my guesses would
be worthless, so `sweep.py` sweeps σ ∈ {1,2,3,4}, p ∈ {0.5,1,2,4}, `Q` ∈
{jaro_winkler, convj}, and both misalignment conventions — 64 configurations per
dataset, on the three most token-swap-prone datasets:

| dataset | best incumbent | MGSA-JW best of 64 | gap |
|---|---|---|---|
| `orgs_hard` | `token_sort` 0.6780 | 0.6424 | **−0.0356** |
| `febrl3` | `token_sort` 0.8198 | 0.7834 | **−0.0364** |
| `historical_50k` | `token_sort` 0.7178 | 0.6402 | **−0.0776** |

MGSA-JW's *best* configuration loses on all three. The gap is not a tuning
problem.

One design call was vindicated: `misalign_vs_peak=True` occupies the entire
top-8 on every dataset, so scoring a match as aligned when it lands on the peak
that produced it is the right reading of eq. (17).

## Two defects found in the published ConvJ

1. **ConvJ is not bounded above by 1.** Algorithm 3 takes a per-character `max`
   with no one-to-one constraint, so several characters of S₁ can claim the same
   character of S₂: `M_w` is bounded by `|S1|` but not by `|S2|`, and the
   `M_w/|S2|` term of eq. (20) overflows. `convj("aa", "a")` returns **1.1183**
   unclamped. Pinned in `test_published_convj_can_exceed_one`. MGSA-JW's
   one-to-one assignment removes this structurally — the one place it is
   unambiguously the better construction.
2. **Eq. (18) and Algorithm 3 disagree** on `A_w`: the pseudocode reads a leaked
   loop variable `j` after its loop has ended. This prototype follows eq. (18).

## If it gets revisited

- **`dblp_acm` is the one win** (0.9532, best of everything measured). Long
  multi-token titles with reordering is the shape that suits it. If bibliographic
  or long-title matching becomes a priority, re-measure on that shape alone.
- **Don't port `convj` as published** — port the one-to-one construction.
- **Symmetry**: MGSA-JW is not symmetric (greedy assignment breaks ties by S₁
  index). A registered scorer would need a canonical argument order or explicit
  symmetrisation. Noted in `test_symmetry_is_not_assumed`.
- **Hyperparameters are anti-zero-config.** σ and p need tuning per data shape,
  and the North Star commitment is defaults that work without it. `token_sort`
  has no knobs.

## Reproduce

```
PYTHONPATH=. uv run pytest scripts/mgsa_jw/tests/test_mgsa.py -q
PYTHONPATH=. uv run python -m scripts.mgsa_jw.bench --datasets all --max-pos 500
PYTHONPATH=. uv run python -m scripts.mgsa_jw.sweep
```

`PYTHONPATH=.` is required because `scripts/` has no `__init__.py`.
