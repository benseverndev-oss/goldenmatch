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

## Measurement — CORRECTED (2026-08-02): the lever REGRESSES its target

> **⚠️ The original "+0.0067 B³ F1" claim was wrong.** It was a single-run
> measurement against a stale pipeline state; main advanced between branch
> restarts and it did **not reproduce**. Re-measured on the canonical
> `bench_er_headtohead` panel (and confirmed across repeated runs + fixed hash
> seeds + the original script), the lever is a **regression** on its only target.

Canonical panel (`validate_fs_holdout.py`, real `dedupe_df`):

| dataset | pairwise F1 OFF→ON | ΔF1 | blocking_recall OFF→ON | threshold_loss OFF→ON |
|---|---|---|---|---|
| historical_50k | 0.8473 → 0.8325 | **−0.0148** | 0.886 → **0.946 (+6pp)** | 0.281 → 0.353 (+7pp) |
| febrl3 | 0.9942 → 0.9942 | +0.0000 | — | — |
| febrl4 | 0.9946 → 0.9946 | +0.0000 | — | — |

Same direction on B³ (historical_50k −0.0078, stable across 4 hash seeds).

**Why it fails end-to-end.** The blocking mechanism *works* — candidate recall
jumps +6pp, the corrupted-name pairs are generated. But (1) those hard
corrupted-name candidates score **below the FS threshold** (threshold_loss
+7pp) so they're generated then discarded, and (2) the **EM shift** from the
added passes changes `m`/`u` and degrades the overall operating point, so
precision *and* recall drop. Generating the pairs isn't enough when the scorer
can't correctly score them — the same wall historical_50k's residual recall
kept hitting on the scoring side. (Atomic-name STRIP is worse: B³ precision
−0.05.)

## Disposition — kept gated, default OFF (known-negative)

Per the maintainer's call, the lever stays in place **gated, default off**, with
the docs corrected to this honest measurement — a *documented known-negative*,
not a pending win. `auto`/`on` must NOT be enabled outside experimentation. A
possible future salvage is the threshold/EM interaction (e.g. accept the
atomic-pass candidates on a per-pass calibrated threshold, or exclude the added
passes from EM training) — but the evidence says the FS scorer fundamentally
can't separate the corrupted-name matches from non-matches at that blocking key,
so treat salvage as speculative.

**Methodology lesson (the real takeaway):** never validate an FS lever on a
single `dedupe_df` run — the pipeline's baseline drifts across processes/pipeline
versions by ~±0.01, which swamped this lever's effect and produced a phantom
win. Use the canonical `bench_er_headtohead` panel + repeated runs from the
start.

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

