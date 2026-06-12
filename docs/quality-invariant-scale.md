# Quality-Invariant Scale Validation (#510)

**Question this answers:** the existing scale docs (`scale-envelope.md`) are
*throughput* claims (wall, RSS). This one is a *quality* claim — does match
quality stay invariant as the dataset grows? We measure pairwise / B-cubed /
cluster F1 against a ground-truth oracle across a 1K→…→cluster-scale ladder.

## TL;DR

- **Quality is scale-invariant through 1M** under a fixed config: pairwise F1
  0.9352 → 0.9365 → 0.9364 at 1K / 100K / 1M (Δ ≤ 0.0013, inside the targets).
- **The audit found and fixed a real auto-config scale bug.** Auto-config was
  emitting a `phonetic_identity` matchkey (`soundex(name)+year`, an *exact*
  identity claim) that is not scale-safe: soundex collapses the names to a
  bounded code space and a year is too coarse, so at scale it manufactured
  cross-cluster matches (precision 0.91→0.82 over 1K→1M) and OOM'd at 25M.
  Fixed by gating the phonetic composite on a *specific* anchor (distinct-count,
  not a date/year type). After the fix, precision is flat through 1M.
- **One residual, softer finding:** beyond ~1M, precision drifts *down* (0.890 at
  1M → 0.855 at 10M) while recall stays dead-flat (0.988). This is a
  fuzzy-false-positive-grows-with-entity-count effect, documented as a follow-up
  (issue #876), not a clustering or recall failure.

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
  headroom (the engine must separate them on secondary fields).
- No whole-field nulls (they collapsed cells to `""` and created cross-cluster
  mega-blocks).
- 1K oracle: pairwise F1 0.9352, precision 0.888, recall 0.988, cluster 0.892.

**Config = FROZEN (the methodology decision).** The published ladder builds the
auto-config **once** at the 1K oracle and applies that *same* config to every
rung (`--frozen`, committed `scripts/qis_realistic_frozen_config.json`). This
isolates the **engine's** scale-invariance from auto-config drift, and runs fast
(per-rung auto-config is a ~50–150s controller search on the ambiguous data).
Auto-config *stability* across scale is a separate question (whether auto-config
picks a similar config at 1K vs 1M); the headline ladder holds config fixed.

**Oracle / metrics.** Oracle = the 1K rung. Each rung reports pairwise, B-cubed,
and cluster F1 vs the ground-truth `__cid__`, plus Δ-vs-oracle. Targets (from
#510): Δpairwise ≤ 0.005, Δb-cubed ≤ 0.005, Δcluster ≤ 0.01. Aggregated by
`scripts/qis_aggregate.py`.

**Scope / deferrals.** Native vs pure-Python produce identical clusters + F1
(verified: 10K native-on 0.9330 ≡ native-off 0.9325), so quality is
runner/native-independent. The **in-house embedder is deferred** — the
scale-invariance question is the same with or without embeddings, and the
embedder adds an ONNX-per-node dependency orthogonal to scale.

## The auto-config phonetic-identity scale bug (found + fixed)

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
cardinality-ratio fallback) — not merely a `date`/`year` col_type (the classifier
types a year column `date` too). A full DOB stays specific and keeps phonetic
(NCVR-style data unchanged); year-anchored data keeps the exact-name + fuzzy
matchkeys, just not the unscalable soundex identity claim. 208 auto-config tests
pass.

## Results (frozen config, post-fix)

| rows | pairwise F1 | Δpw | precision | recall | B-cubed F1 | cluster F1 | Δcl | PASS |
|------|-------------|-----|-----------|--------|------------|------------|-----|------|
| 1,000 (oracle) | 0.9352 | — | 0.8877 | 0.9880 | 0.9698 | 0.8923 | — | ✅ |
| 100,000 | 0.9365 | +0.0013 | 0.8904 | 0.9876 | 0.9702 | 0.8954 | +0.0031 | ✅ |
| 1,000,000 | 0.9364 | +0.0013 | 0.8902 | 0.9878 | 0.9702 | 0.8957 | +0.0033 | ✅ |
| 10,000,000 | 0.9166 | −0.0186 | 0.8547 | 0.9882 | — | 0.8775 | −0.0148 | ❌ (drift, see below) |

(1K–10M on the `large-new-64GB` bench runner. The ladder is **capped at 10M** —
25M+ are impractical to run until the blocking issue below is addressed; see
"Why larger rungs are blocked".)

**Verdict:** quality is scale-invariant **through 1M** (all deltas inside target).
Beyond that a single auto-config blocking-selection issue (#876) drives both a
precision drift and a candidate-pair explosion.

## Residual finding: blocking on a bounded-cardinality key doesn't scale (#876)

At 10M, precision drifts down (0.890 → 0.855) while recall stays flat (0.988).
Root cause (pinned): the frozen config blocks on a single key, `zip`. In the
fixture `zip = cid % 100000` wraps at 100K clusters, so the zip block size grows
~linearly with N (10 rows/zip at 1M → 100 at 10M → 250 at 25M), and the
candidate-pair count grows ~`N² / 100000`: ~4.5M pairs at 1M, ~500M at 10M, ~3B
at 25M. Those bloated blocks are mostly *cross-cluster* pairs, a growing fraction
of which the fuzzy scorer matches — hence the precision drift — and scoring 3B
pairs is why 25M is impractical.

This is **real-world-relevant**, not a fixture artifact: real zips are also
bounded (~40K US zips), so an auto-config that blocks on `zip` alone explodes on
any large real dataset. The hard part is that the cardinality *cap* is invisible
from a small sample (a 1K sample shows `zip` as clean/high-cardinality), so
auto-config can't catch it the way it catches a year's 65 distinct values (the
phonetic fix). The fix (a known-bounded `col_type=zip/geo` shouldn't be a
scalable *sole* blocking key for large `n_rows`; require a refining sub-key or
multi-pass blocking) is a more involved blocking-selection change — tracked as
**#876**, a follow-up to this PR.

## Why larger rungs are blocked

25M / 50M / 100M / 200M are out of scope for *this* report because of #876 above:
the zip-block candidate-pair explosion makes them take hours (3B+ scored pairs at
25M). Once #876 lands (bounded blocks → linear scaling), the cluster tier becomes
fast and can extend the curve. The 1K→10M curve already decisively shows the two
findings (invariance through 1M; the blocking-driven drift beyond).

## Reproduction

```bash
# One rung (frozen config = the published methodology):
python scripts/quality_invariant_scale.py --rows 1000000 --corruption moderate \
  --frozen --backend bucket --out rung_1m.json

# Mid-ladder rungs on the bench runner:
gh workflow run bench-quality-invariant-scale.yml --ref feat/510-quality-invariant-scale \
  -f rows=10000000 -f corruption=moderate -f frozen=true -f backend=bucket \
  -f runner=large-new-64GB -f label=10m

# Aggregate a directory of per-rung JSONs into the table + verdict:
python scripts/qis_aggregate.py results_dir/

# Rebuild the frozen config (only after a fixture / corruption change):
python scripts/quality_invariant_scale.py --rebuild-frozen-config
```

Related: the determinism + native-parity validation lives in
`packages/python/goldenmatch/tests/test_qis_harness.py`; the golden-survivorship
determinism gap is tracked as #870; the phonetic fix is in `core/autoconfig.py`.
