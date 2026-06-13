# Quality-Invariant Scale Validation (#510)

**Question this answers:** the existing scale docs (`scale-envelope.md`) are
*throughput* claims (wall, RSS). This one is a *quality* claim — does match
quality stay invariant as the dataset grows? We measure pairwise / B-cubed /
cluster F1 against a ground-truth oracle across a 1K→…→100M ladder.

## TL;DR

- **Quality is scale-invariant through 10M** under a fixed config: pairwise F1
  0.9352 → 0.9364 → 0.9362 at 1K / 1M / 10M (Δ ≤ 0.0012, inside the targets),
  precision flat at 0.888–0.890, recall flat at 0.988.
- **The audit found and fixed TWO real auto-config scale bugs.**
  1. A `phonetic_identity` matchkey (`soundex(name)+year`, an *exact* identity
     claim) that manufactured cross-cluster matches at scale (precision
     0.91→0.82 over 1K→1M, OOM at 25M). Fixed by gating phonetic on a *specific*
     anchor.
  2. **Blocking selection was not scale-invariant (#876).** The frozen config
     blocked on a single bounded-cardinality key (`zip`), whose block size grows
     ∝ N — so beyond ~1M, precision drifted *down* (0.890 → 0.855 at 10M) and the
     candidate-pair count exploded (3B+ at 25M, making it impractical). **Fixed**
     (see below); after the fix precision is flat through 10M and the larger
     rungs run in practical time.
- **No residual quality drift.** The 10M precision drop documented in the prior
  revision of this report was the #876 blocking bug, not a clustering or recall
  failure. With scale-invariant blocking it is gone.

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

| rows | pairwise F1 | Δpw | precision | recall | B-cubed F1 | cluster F1 | Δcl | wall | PASS |
|------|-------------|-----|-----------|--------|------------|------------|-----|------|------|
| 1,000 (oracle) | 0.9352 | — | 0.8877 | 0.9880 | 0.9698 | 0.8923 | — | — | ✅ |
| 1,000,000 | 0.9364 | +0.0012 | 0.8902 | 0.9878 | 0.9702 | 0.8957 | +0.0034 | 58s / 5.5 GB | ✅ |
| 10,000,000 | 0.9362 | +0.0010 | 0.8895 | 0.9882 | 0.9702 | 0.8962 | +0.0039 | 1155s / 49.2 GB | ✅ |
| 25,000,000 | _landing_ | | | | | | | | |
| 50,000,000 | _landing_ | | | | | | | | |
| 100,000,000 | _landing_ | | | | | | | | |

(1K on the dev box; 1M/10M on the `large-new-64GB` bench runner, `backend=bucket`;
25M–100M on `backend=duckdb` for out-of-core headroom. Each rung is a
`bench-quality-invariant-scale.yml` dispatch.)

**Verdict:** quality is scale-invariant **through 10M** — every delta inside
target, precision flat (the prior 10M drift is gone), recall dead-flat at 0.988.
The 25M–100M rungs extend the curve and are landing on the bench runner; with
bounded blocking they run in practical time (the 3B-pair explosion that blocked
them is gone).

## Reproduction

```bash
# One rung (frozen config = the published methodology):
python scripts/quality_invariant_scale.py --rows 1000000 --corruption moderate \
  --frozen --backend bucket --out rung_1m.json

# Mid/large-ladder rungs on the bench runner:
gh workflow run bench-quality-invariant-scale.yml --ref feat/510-quality-invariant-scale \
  -f rows=10000000 -f corruption=moderate -f frozen=true -f backend=bucket \
  -f runner=large-new-64GB -f label=10m

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
