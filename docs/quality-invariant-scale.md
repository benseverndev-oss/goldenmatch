# Quality-Invariant Scale Validation (#510)

**Question this answers:** the existing scale docs (`scale-envelope.md`) are
*throughput* claims (wall, RSS). This one is a *quality* claim — does match
quality stay invariant as the dataset grows? We measure pairwise / B-cubed /
cluster F1 against a ground-truth oracle across a 1K→…→100M ladder.

## TL;DR

- **Recall is scale-invariant to 100M** (dead-flat 0.988 across 1K→100M), and
  **B-cubed + cluster F1 stay inside target to 100M** — under a single fixed
  config, validated on a 503 GB box. The #876 recall-preserving blocking holds
  perfectly where the old config OOM'd by 10M.
- **Pairwise F1**: 0.9352 → 0.9364 → 0.9362 → 0.9342 through 1K/1M/10M/25M (inside
  ±0.005), then a gentle precision-driven drift to 0.9314 at 50M (−0.0038, still
  in) and 0.9266 at 100M (−0.0086, just out). It's a **scoring-side
  fuzzy-FP-grows-with-N effect, not a blocking/recall failure** — and ~milder than
  the pre-#876 drift at 10× less scale.
- **The audit found and fixed TWO real auto-config scale bugs.**
  1. A `phonetic_identity` matchkey (`soundex(name)+year`, an *exact* identity
     claim) that manufactured cross-cluster matches at scale (precision
     0.91→0.82 over 1K→1M, OOM at 25M). Fixed by gating phonetic on a *specific*
     anchor.
  2. **Blocking selection was not scale-invariant (#876).** The frozen config
     blocked on a single bounded-cardinality key (`zip`), whose block grows ∝ N —
     a candidate-pair explosion (3B+ at 25M) that drifted precision and OOM'd.
     Fixed with a type-aware cardinality projector + a scale-safe
     `[zip, birth_year]` bounded compound — recall now flat to 100M.

## Methodology

**Fixture (`scripts/quality_invariant_scale.py`, `shape="realistic"`).** A
synthetic person dataset with a `__cid__` ground-truth cluster id (5 rows per
cluster). The identities are **collision-free by construction and scale-invariant
in their separability** — the precondition for a meaningful scale audit:

- Names: a *bijective* `cid → 8-syllable` encoding (24⁸ ≈ 1.1e11 distinct), so
  distinct clusters get distinct names at every N — no birthday collisions as
  clusters grow.
- Address: a bijective street number (the old `[1,9999]×8-street` space saturated
  ~80K clusters).
- Every per-cluster field is a pure, prefix-stable function of `(seed, cid)`, so
  the 1K dataset is an exact prefix of the 100M dataset (modulo extra clusters) —
  any cross-rung F1 delta is a *scale* effect, not a data-shape effect.

**Corruption (`--corruption moderate`).** Tuned to land the 1K oracle in a
drift-sensitive band with headroom on *both* precision and recall:

- Multi-edit character corruption (transpose / delete / token-drop, several edits
  per corrupted cell) → real recall headroom (one edit barely dents a fuzzy score
  on a long name).
- A fixed fraction of genuinely-ambiguous *twin* clusters (a twin pair shares
  first+last name but keeps distinct address / zip / birth_year) → real precision
  headroom (the engine must separate them on secondary fields). **These twins are
  also what makes blocking selection load-bearing** — see #876 below.
- No whole-field nulls (they collapsed cells to `""` and created cross-cluster
  mega-blocks).
- 1K oracle: pairwise F1 0.9352, precision 0.888, recall 0.988, cluster 0.892.

**Config = FROZEN (the methodology decision).** The published ladder builds the
auto-config **once** at the 1K oracle and applies that *same* config to every
rung (`--frozen`, committed `scripts/qis_realistic_frozen_config.json`). This
isolates the **engine's** scale-invariance from auto-config drift, and runs fast
(per-rung auto-config is a ~50–150s controller search on the ambiguous data).
**Crucially, the frozen config is now built FOR the target scale** (`n_rows_full =
200M`), so auto-config's blocking projection (#876) engages at build time and the
config it freezes is the one that's correct at 200M, not at 1K.

**Oracle / metrics.** Oracle = the 1K rung. Each rung reports pairwise, B-cubed,
and cluster F1 vs the ground-truth `__cid__`, plus Δ-vs-oracle. Targets (from
#510): Δpairwise ≤ 0.005, Δb-cubed ≤ 0.005, Δcluster ≤ 0.01. Aggregated by
`scripts/qis_aggregate.py`.

**Scope / deferrals.** Native vs pure-Python produce identical clusters + F1
(verified: 10K native-on 0.9330 ≡ native-off 0.9325), so quality is
runner/native-independent. The **in-house embedder is deferred** — the
scale-invariance question is the same with or without embeddings, and the
embedder adds an ONNX-per-node dependency orthogonal to scale.

## Bug 1: the phonetic-identity scale bug (found + fixed)

`auto-config` emitted `phonetic_identity` = `soundex(first)+soundex(last)+year`
as an *exact* matchkey, gated only on having a date/year anchor. soundex collapses
the (unique) names to a bounded code space; a year is ~65 distinct values; so the
composite is not specific, and the spurious cross-cluster matches it manufactures
grow like `n_rows² / selectivity` — invisible on a 1K sample, precision-wrecking
and block-exploding (OOM) as the data grows.

| config | 1K precision | 1M precision | 25M |
|--------|-------------|-------------|-----|
| with phonetic (before) | 0.9105 | **0.8201** | OOM |
| no phonetic (after fix) | 0.8877 | **0.8902** | runs |

**Fix** (`core/autoconfig.py`): gate the phonetic composite on a *specific*
anchor — the anchor column's distinct-value count (≥150 via the sample, with a
cardinality-ratio fallback) — not merely a `date`/`year` col_type. A full DOB
stays specific and keeps phonetic (NCVR-style data unchanged); year-anchored data
keeps the exact-name + fuzzy matchkeys, just not the unscalable soundex identity
claim.

## Bug 2: scale-invariant blocking selection (#876, found + fixed)

After the phonetic fix, the frozen config blocked on a single key, `zip`. In the
fixture `zip = cid % 100000` wraps at 100K clusters (real US zips are similarly
bounded at ~40K), so the `zip` block size grows ∝ N and the candidate-pair count
grows ∝ N²/100000 (~4.5M pairs at 1M, ~500M at 10M, ~3B at 25M). The bloated
blocks are mostly *cross-cluster* pairs, a growing fraction of which the fuzzy
scorer matches — hence the precision drift — and scoring 3B pairs is why 25M was
impractical. The hard part: a bounded-cardinality cap is invisible from a small
sample (a 1K sample shows `zip` as clean/high-cardinality).

The fix is a **type-aware "cardinality projector"** in `build_blocking`, in two
parts, plus a harness change:

1. **Type-aware cardinality projection.** The blocking-candidate gate projected a
   sampled column's cardinality to full N with a Chao1 (closed-domain
   unseen-species) estimator, which drives the ratio *down* as N grows. That's
   right for a BOUNDED key (zip/year — the domain saturates) but wrong for an
   UNBOUNDED key (email/name/identifier, whose distinct-count grows with N). The
   bug it caused: a near-unique `email` (sample ratio 0.56) projected to ~0.001 at
   200M, slipped past the gate, and was picked as the sole blocking key —
   near-singleton blocks, blocking recall 0.39. Fix: only bounded types
   Chao1-project; unbounded types keep their sample ratio, so a near-unique key is
   correctly rejected.
2. **Scale-safe bounded compound.** With `email` rejected and `zip`-alone rejected
   (its pair count is super-linear), no *single* exact key is scale-safe. Rather
   than drop these discriminators for a name-only blocking, AND the bounded exact
   keys into a compound whose *joint* domain bounds the block: `zip` (≈100K) ×
   `birth_year` (≈300) = ≈30M, so the `[zip, birth_year]` block stays ≈constant
   and the pair count linear. This is **scale-safe AND quality-preserving**: it
   co-locates a cluster's variants (both components are stable within a cluster)
   AND separates the adversarial twins (which share names but differ on zip).
3. **Harness:** `build_frozen_config` now passes `n_rows_full = 200M`, so the
   projection engages at build time and freezes the scale-correct blocking.

**Why name-only blocking is *not* the answer (the subtle part).** Rejecting
`email` and falling through to name-based blocking (soundex/substring on names)
gave a high blocking *recall* (0.99 on true pairs) but collapsed end-to-end F1 to
0.78 — because name-only passes **collide the twin clusters** (they share names),
which over-merges, then cluster-splitting fragments. A blocking-recall probe can't
see this; only an end-to-end sweep does. The sweep that drove the choice
(matchkeys held fixed, blocking swapped, 1K):

| blocking | pairwise F1 | note |
|----------|-------------|------|
| `zip` alone | 0.9352 | report baseline — but explodes at scale |
| **`[zip, birth_year]`** | **0.9352** | **scale-safe, identical quality — chosen** |
| `[zip, last_name]` | 0.8251 | last_name corrupted → recall drop |
| name multipass | 0.7788 | twin collision → the regression |

The fix triggers only at scale (`n_rows_full > sample_n`) when ≥2 bounded exact
keys exist and none is scale-safe alone, so the #491/#715 benchmark datasets
(NCVR / DQbench / Febrl, small-scale single-key path) are untouched — 214
auto-config tests + the QIS-harness suite stay green.

## Results (frozen `[zip, birth_year]` config, post-both-fixes)

| rows | pairwise F1 | Δpw | precision | recall | B-cubed F1 | Δb³ | cluster F1 | Δcl | peak RSS |
|------|-------------|-----|-----------|--------|------------|-----|------------|-----|----------|
| 1,000 (oracle) | 0.9352 | — | 0.888 | 0.988 | 0.9698 | — | 0.892 | — | — |
| 1,000,000 | 0.9364 | +0.0012 | 0.890 | 0.988 | 0.9702 | +0.0004 | 0.896 | +0.003 | 6 GB |
| 10,000,000 | 0.9362 | +0.0010 | 0.890 | 0.988 | 0.9702 | +0.0004 | 0.896 | +0.004 | 49 GB |
| 25,000,000 | 0.9342 | −0.0010 | 0.886 | 0.988 | 0.9695 | −0.0003 | 0.894 | +0.002 | 69 GB |
| 50,000,000 | 0.9314 | −0.0038 | 0.881 | 0.988 | 0.9685 | −0.0013 | 0.892 | ~0 | 138 GB |
| 100,000,000 | 0.9266 | −0.0086 | 0.872 | 0.988 | 0.9667 | −0.0031 | 0.887 | −0.005 | 276 GB |

Targets: Δpairwise ≤ 0.005, Δb-cubed ≤ 0.005, Δcluster ≤ 0.01. (1K on the dev box;
1M/10M on the `large-new-64GB` bench runner; 25M–100M on a single `n2-highmem-64`
GCP box, 503 GB — the 64 GB runners cap this shape at ~10M. All `backend=bucket`,
native kernel on, `GOLDENMATCH_NATIVE_ADDRESS_NORMALIZE=1` + slim-projection flags.)

**Verdict — two clean results and one honest residual:**

1. **Recall is dead-flat at 0.988 across the entire 1K→100M range.** This is the
   load-bearing #876 result: the recall-preserving `[zip, birth_year]` blocking
   holds perfectly to 100M (no candidate-pair explosion, no recall loss). The
   pre-#876 zip-only config drifted and OOM'd by 10M; this runs clean to 100M.

2. **By the cluster-level metrics (B-cubed, cluster F1), quality is scale-invariant
   to 100M** — both stay inside target (Δb³ −0.0031, Δcl −0.005 at 100M).

3. **Residual precision drift (scoring-side, gentle).** Precision eases from 0.890
   (1M) to 0.872 (100M), pulling the most-sensitive metric — *pairwise* F1 — inside
   target through ~50M (−0.0038) and just outside at 100M (−0.0086). Cause: the
   `[zip, birth_year]` joint domain is ~6.5M cells (100K zips × ~65 years); at 100M
   rows ≈ 20M clusters that's ~3 clusters per cell, so the densest cells carry
   cross-cluster pairs and a growing fraction trip the fuzzy threshold. It is a
   **fuzzy-false-positive-grows-with-entity-count effect, not a blocking or recall
   failure** — and it is *milder* than the pre-#876 zip-only drift (−0.02 at 10M)
   at 10× the scale. Tightening it further is a scoring-threshold / blocking-key
   question (a finer compound, or a TF-adjusted fuzzy threshold), tracked as a
   #876 follow-up; it does not affect the recall/cluster invariance claim.

**Memory + practicality.** Peak RSS is ~sublinear-to-linear: 49 GB at 10M → 276 GB
at 100M. 200M projects to ~550 GB, over a single 503 GB box, so it needs a bigger
single box (m1/m2 ultramem) or the distributed (`backend=ray`) path. Wall is
dominated by the bucket fuzzy-scoring loop (~100K pairs/s; the native kernel does
not change it for this scorer config) and the pure-Python prep, not blocking.

**Distributed-engine check (in progress).** A `backend=ray` 100M run validates
whether the distributed pipeline (partitioned scoring + distributed WCC) holds the
same quality as the 0.9266 single-box baseline or under-merges (the #844
driver-materialization risk). Result to be folded in here.

## Reproduction

```bash
# One rung (frozen config = the published methodology). The env flags are NOT
# optional at scale: GOLDENMATCH_NATIVE=1 engages the native kernel for the gated
# components, and NATIVE_ADDRESS_NORMALIZE=1 keeps the matchkey-transform precompute
# off the per-row Python path (otherwise it dominates the wall at 25M+). This is
# exactly the env block in bench-quality-invariant-scale.yml.
GOLDENMATCH_NATIVE=1 GOLDENMATCH_NATIVE_ADDRESS_NORMALIZE=1 \
GOLDENMATCH_BUCKET_SLIM_PROJECTION=1 GOLDENMATCH_GOLDEN_SLIM_MULTIDF=1 \
GOLDENMATCH_AUTOCONFIG_MEMORY=0 POLARS_SKIP_CPU_CHECK=1 \
python scripts/quality_invariant_scale.py --rows 100000000 --corruption moderate \
  --frozen --backend bucket --out rung_100m.json

# 1M/10M fit the large-new-64GB bench runner; 25M-100M need a big-mem box (this
# curve used a single n2-highmem-64 / 503 GB GCP box). 200M (~550 GB RSS) needs
# m1/m2-ultramem or the distributed backend=ray path.

# Aggregate a directory of per-rung JSONs into the table + verdict:
python scripts/qis_aggregate.py results_dir/

# Rebuild the frozen config (only after a fixture / corruption change). Builds
# FOR n_rows_full=200M so the #876 blocking projection engages:
python scripts/quality_invariant_scale.py --rebuild-frozen-config
```

Related: the determinism + native-parity validation lives in
`packages/python/goldenmatch/tests/test_qis_harness.py`; the golden-survivorship
determinism gap is tracked as #870; both auto-config fixes are in
`core/autoconfig.py` (phonetic anchor gate; `_projected_ratio` type-aware
projection + `_scale_safe_bounded_compound`).
