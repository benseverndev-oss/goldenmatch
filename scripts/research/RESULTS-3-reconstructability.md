# Experiment #3 results — reconstructability vs F1 on real ER data

Runner: `scripts/research/recon_er_experiment.py`. Reproduce:

```bash
uv pip install --system polars pyarrow recordlinkage rapidfuzz   # deps
# DBLP-ACM: fetched once from the Leipzig mirror into datasets/DBLP-ACM/
python scripts/research/recon_er_experiment.py --dataset febrl3   --kernel char
python scripts/research/recon_er_experiment.py --dataset febrl3   --kernel idf
python scripts/research/recon_er_experiment.py --dataset dblp-acm --kernel char --datasets-dir datasets
python scripts/research/recon_er_experiment.py --dataset dblp-acm --kernel idf  --datasets-dir datasets
```

First real-data run: 2026-06-07. `rapidfuzz` present (char kernel = Jaro-Winkler).
Background sample `k_bg=24`; small run-to-run jitter (~±0.01 Spearman) from
the seeded background draw.

## Headline matrix

| Dataset | Shape | Kernel | Spearman(recon, F1) | gold argmax | STEP-1 gate |
|---|---|---|---|---|---|
| **Febrl3** (5000 rec, 2000 clusters) | personal records / PII | char | **+0.944** | yes | **PASS** |
| Febrl3 | PII | idf | +0.891 | yes | PASS |
| **DBLP-ACM** (4910 rec, 2686 clusters) | bibliographic / templated | char | **+0.591** | no | **FAIL** |
| DBLP-ACM | bibliographic | idf | **+0.647** | no | **PASS** |

## What it means

1. **The likelihood is viable — strongly so on distinctive data.** On Febrl3
   (names, DOB, SSN, address), reconstructability ranks clusterings by F1 at
   Spearman **+0.944** with gold cleanly on top, and decays monotonically across
   over-merge, under-merge, and random-mix perturbations. Masked-field
   reconstruction from cluster-mates *is* a usable, label-free co-reference
   signal. This clears the design note's step-1 gate.

2. **The similarity kernel is data-dependent — and that is a design signal, not
   noise.** On bibliographic DBLP-ACM the parameter-free char kernel FAILS
   (+0.591): titles/venues share vocabulary, so two unrelated VLDB papers look
   co-referent and the background contrast collapses. Switching to an
   **IDF-weighted token kernel** (discount ubiquitous tokens — the
   `name_freq_weighted_jw` idea goldenmatch already ships) lifts it to **+0.647**
   and flips the gate to PASS. Conversely, IDF is slightly *worse* than char on
   Febrl3 (+0.891 vs +0.944) because short PII fields need char-level
   typo-tolerance, not token overlap.
   **=> The amortized net (step 2) should *learn* the field reconstructor, not
   hard-code a kernel.** A fixed kernel is right for one data shape and wrong for
   the other.

3. **Over-merge precision-blindness persists on bibliographic data regardless of
   kernel.** Even with IDF, the over-merge rows stay flat/rising and gold is not
   the strict argmax on DBLP-ACM — merging papers that share distinctive title
   tokens still reconstructs well. Reconstructability is a *recall*/
   information-recovery signal; it cannot see false merges on its own. The crude
   size prior does not rescue this (clusters are tiny, so the size penalty is
   negligible: Spearman moves +0.591→+0.609 at most).
   **=> This is the empirical case for the microclustering PRIOR in program
   (1+3+6).** The experiment localises the split: reconstructability (#3) owns
   the recall likelihood; a *learned* prior (#1) must own the precision pressure
   on cluster size — a fixed `size^2` penalty is not enough.

## Verdict

Step 1 **passes** (viable likelihood, especially on PII; viable on bibliographic
with a frequency-aware kernel). Two concrete carry-forwards into step 2:
- learn the field reconstructor instead of fixing a similarity kernel;
- pair the reconstruction likelihood with a *learned* microclustering prior, not
  a size penalty — over-merge precision is the prior's job.
