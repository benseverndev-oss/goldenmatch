# Atomic-name-soundex blocking

**Date:** 2026-08-01
**Status:** shipped, gated `GOLDENMATCH_FS_ATOMIC_NAME_BLOCKING`, **default off** pending the full ER panel.

## The gap

`build_blocking` emits person-name blocking passes on the **composites**
(`full_name`, `first_and_surname`) via soundex, but no pass on the **atomic**
single names (`surname`, `first_name`). So one corrupted name breaks the whole
composite key: a pair with a mangled first name but an intact surname is never
co-blocked, even though a `surname` soundex would catch it.

This is the recall bottleneck. On `historical_50k` (leak-free), production
candidate-recall is **0.8855**; of the 34,803 missed true pairs, **65.6% are
recoverable by a cheap single-field key** the blocking doesn't use —
surname-soundex alone would co-block 49%, first-initial+surname another 26%.
This is the lever the local-LLM-ER investigation was actually looking for:
scoring/relabeling can't touch pairs blocking never generated (label-constrained
EM capped at ΔF1 +0.0006–0.0020), but blocking can generate them.

## The fix

`_add_atomic_name_soundex_blocking` (autoconfig.py): when a composite-name
soundex pass exists but the atomic single-name soundex pass doesn't, add an
**additive** `lowercase+soundex` pass for each given/family field present.

- **`additive=True`** — co-locates the missed pairs WITHOUT demoting the atomic
  name field from EM scoring (demoting a strong name discriminator collapses
  recall; see the orthogonal-anchor precedent).
- The downstream `_bound_probabilistic_blocking_pairs` bounds/drops any pass
  whose candidate-pair count explodes, so a common-surname mega-block can't OOM.
- The **scorer still decides precision** — a soundex block only generates
  candidates.

## SOUNDEX, not STRIP (the key design point)

An atomic-name **strip** (exact) pass co-blocks every exact-same-name pair —
mostly unrelated people — and **over-merges**: measured `historical_50k` B³
precision −0.05 (the documented failure the existing orthogonal-anchor overlap
gate correctly drops). An atomic-name **soundex** pass is a *broader* net yet
**precision-safe**, because the pairs it adds beyond strip are
corrupted-spelling variants (`Smith`/`Smyth`) that are disproportionately TRUE
matches — so recall rises while precision holds. This is why the existing overlap
gate (which only ever tested strip candidates) drops atomic-name passes, but the
soundex variant is safe.

## Measurement (leak-free, real `dedupe_df`, B³)

| dataset | | B³ P | B³ R | B³ F1 | ΔF1 |
|---|---|---|---|---|---|
| historical_50k | off | 0.9708 | 0.7752 | 0.8620 | — |
| historical_50k | **+ atomic soundex** | 0.9680 | 0.7879 | **0.8688** | **+0.0067** |
| historical_50k | + atomic **strip** (contrast) | 0.9203 | 0.7985 | 0.8551 | −0.0070 |
| febrl3 | + atomic soundex | 1.0000 | 0.9946 | 0.9973 | +0.0000 (no-op) |

+0.0067 B³ F1 on the hard PII set (recall +1.27pp, precision −0.0027), no-op on
clean data. **13× the entire scoring-side oracle ceiling.** The strip contrast
reproduces the documented over-merge, validating the harness.

## Disposition

Ships **gated, default off**. `auto` fires only on person-shaped data
(`_dataset_is_person_shaped`), a structural no-op elsewhere; `on` forces it.
Default-off (not `auto`) because only historical_50k + febrl3 are validated so
far — the `bench_er_headtohead` / `qis_gate` panel should bless it before the
default flips, matching the `tf_adjustment` / FD-negative-evidence posture.

## The LLM pass-selector idea — investigated and shelved

The hypothesized generalization was a **match-oracle-driven pass selector**:
`blocking_pass_selection` ranks candidate passes by an unsupervised "≥2 fields
agree = likely match" proxy; the pinned 1.5B, reading the actual values, would be
a better oracle on corrupted data and keep passes the proxy under-values.

**Measured, and it doesn't hold on historical_50k.** Running `select_passes` with
the atomic-name passes in the candidate set, the proxy **keeps** `surname` and
`first_name` soundex at *every* pruning floor (min_marginal_weak_positive =
1 / 1000 / 5000) — they add many new candidate pairs, so their marginal
likely-match yield is high (surname ~5k, first_name ~130k) regardless of the
proxy's per-pair accuracy. The proxy isn't the bottleneck: a recall-worthy pass is
kept because it adds *volume*, not because the proxy correctly labels each pair.
So an LLM oracle in the selector changes no keep/drop decision here, and the
selector is default-off anyway. (A minor real nit surfaced: `_default_discriminative_fields`
can pick a near-unique row-id column — e.g. `unique_id` — which never agrees
across rows and is dead weight in the proxy; harmless, not worth an LLM.)

**Net for the local-LLM-in-blocking question:** the accessible recall was captured
by the *static* atomic-name-soundex lever (no LLM). Of the residual misses, ~65%
were cheap-key-recoverable (this lever + existing passes), and the remaining ~35%
need a *semantic/embedding-ANN* net, not LLM pair-labeling. The 1.5B's value stays
where it was measured to pay — as the local matcher itself, and as a local
`llm_scorer` backend on product/messy-text domains where LLM scoring moves F1 —
**not** in the FS scoring or blocking loop on structured PII.

<!-- superseded pitch retained for provenance:
The pinned 1.5B, reading the actual values, is a better oracle there — keeping
recall-worthy passes and pruning wasteful ones on any schema, over an
O(N)-per-pass sample.
-->

