# Auto-config: smarter & faster — assessment

- **Date:** 2026-06-22
- **Branch:** `claude/autoconfig-smarter-faster`
- **Context:** The auto-config native-core arc (A–F) just landed on `main` (#1166 +
  #1174/#1175/#1176/#1177). `"autoconfig"` is now in `_GATED_ON`, so the shared
  `goldenmatch-autoconfig-core` Rust kernel is the default decision core across
  Python / TS (wasm) / (future) SQL. **That gate-flip is the multiplier:** an
  improvement to the *decision logic* in the core is now earned once and inherited
  by every surface. This doc assesses where to spend that leverage.
- **Discipline:** every perf claim here is wall-clock measured or flagged
  `MEASURE FIRST`; every accuracy/threshold change is gated on the DQbench / F1
  suites, never reasoned from a proxy (`feedback_verify_perf_not_just_ship`,
  the Stage-D lesson). Sourced from three exploration passes (decision surface,
  perf surface, prior-art survey) — see "Prior art" below; do not re-derive those.

## Implementation status (this branch)

- **F1 — DONE.** `measure_blocking_profile` now computes the block-size
  distribution via a single vectorized `group_by` (`_fast_static_block_sizes`)
  for plain `static` blocking, falling back to the exact `build_blocks` loop for
  non-static / oversized-split configs. **Measured 329 ms → 6 ms median @ 1M
  (~55×); 49 ms @ 10M.** Byte-identical to the old path (10 parity tests). Gotcha
  found + fixed: a lazy post-`group_by` `.filter()` on the key is shoved back to
  n_rows by Polars **predicate pushdown** (74 ms); collecting the per-key agg
  first and filtering **eagerly** keeps it at ~6 ms.
- **F2 — DONE.** Full-frame measurement is now the default at the `normal`/`fast`
  tiers too (not just `thinking`/`einstein`), gated by `_should_measure_blocking`:
  lower tiers measure only when the cheap static fast path applies and the frame
  is under a 20M-row budget backstop; distributed still extrapolates. This kills
  the under-provisioning bug for the common `normal` tier. 10M measured at 49 ms,
  so the flip is affordable.
- **S1 — STAGED (next PR).** With F2, the biased linear `extrapolate_to` is now
  reached only on the residual fallbacks (distributed, >20M-row lower tiers,
  measurement failure). A Chao1-corrected pair-count estimate there changes
  numeric outputs and must be gated on the sample-quality bench + DQbench/F1 —
  deliberately not shipped unvalidated (measurement discipline).

## The North Star test (gating frame)

Every lever below is scored against the five commitments
(`context-network/foundation/project-definition.md`): does it **raise the
zero-config floor**, **preserve answer-parity across scale**, reach **every
surface**, **close the gap to an expert**, and stay **auditable** (never a black
box)? The 2026-06-15 roadmap diagnosis stands: we have largely *built* a
default-worthy tool but barely *made* it the default — so correctness-at-scale
and cross-surface reach weigh heaviest right now.

## The architectural split (where a fix pays off)

| Layer | Lives in | A change here benefits… | Constraint |
|---|---|---|---|
| **Decision logic** — planner rung boundaries, classifier heuristics, refit rules | the shared Rust core (`autoconfig-core`) | Python + TS + SQL at once | must regenerate golden vectors + re-prove cross-surface byte-parity, AND re-validate accuracy on DQbench |
| **Measurement** — blocking profile, sampling, candidate-pair counting | per-surface runtime (Python/Polars; TS profiler) | one surface per change | Polars-side; the TS profiler needs the same restructure separately |

This split is the spine of the plan: **smarter = mostly core work (multiplies);
faster = mostly per-surface measurement work (does not, but is cheap and high-impact).**

---

## FASTER — wall-clock levers (measured)

### F1. Polars restructure of `measure_blocking_profile` — **headline, 14× measured**
- **Where:** `core/blocker.py:117-121` — the per-block `collect()` loop:
  `for b in blocks: sizes.append(b.df.select(pl.len()).collect().item())`.
- **Fix:** one lazy `group_by(<blocking_key>).agg(pl.len())` collect; keep the
  `sum(s*(s-1)//2)` arithmetic in Python (it's already the vector op).
- **Measured (Stage-D bench, 1M rows / 317k blocks):** 272 ms → 19 ms (**~14×**).
- **Fix type:** Polars restructure. **NOT** a native kernel — the
  `candidate_pair_count` native path is a wash-to-loss at ≥1.5M blocks
  (list-marshaling > the Python sum). Leave `pairs.py` alone.
- **North Star:** scale-invariant correctness (it's the prerequisite for F2/S1).
- **Effort:** small, self-contained, already benched. **Do first.**

### F2. Make full-frame measurement cheap enough to default (depends on F1)
- Once F1 lands, full-frame `measure_blocking_profile` is affordable
  (~19 ms @ 1M) at planning-effort tiers *below* `thinking`/`einstein`, where
  today the controller linearly extrapolates from a ≤20k sample.
- **MEASURE FIRST:** re-bench the full-frame wall at 1M **and 10M** post-F1
  before flipping the default; the 10M point is unmeasured. Propose defaulting
  measured-blocking at `normal` if 10M stays affordable.

### F3. Trim multi-pass re-profiling (modest)
- **Where:** `core/autoconfig_controller.py:649-694` (eager full-df indicator
  scans pre-loop) + `autoconfig.py:3009-3024`/`3080-3086` (early-throughput and
  multi-source re-profiles re-scan the full frame).
- **Fix:** memoize column-level stats once and reuse across the iteration loop +
  the pre-loop gates (the `IndicatorContext` already does this for indicators;
  extend it to the throughput/source scans).
- **MEASURE FIRST:** profile the controller loop on a real 1M frame — the agent
  flagged the spots structurally; the win is unquantified. Low priority until measured.

---

## SMARTER — config-quality levers (gate on DQbench/F1)

### S1. Full-frame measurement replaces linear pair-count extrapolation — **highest quality leverage**
- **Problem (measured, `bench_autoconfig_sample_quality.py`):** within-block pairs
  grow quadratically but `BlockingProfile.extrapolate_to` (`complexity_profile.py:277-296`)
  scales linearly, so a 0.2% sample under-counts pairs by ~500× at 10M rows →
  the planner picks `simple/bucket` for a true chunked-rung dataset.
- **Fix:** route through F1/F2 measured blocking; for tiers that still sample,
  add a **Chao1-style correction** to the extrapolation (a Chao1 estimator already
  exists for matchkey cardinality — reuse the pattern for pair counts).
- **Surface:** Python measurement + the *planner rung input*. The rung **boundaries**
  are in the core; the **pair-count signal** feeding them is per-surface. Improving
  the signal needs no core change — good, it ships without a golden re-gen.
- **North Star:** scale-invariant correctness — this is the single biggest
  "wrong answer at scale" bug in auto-config today.

### S2. Adaptive, row-count-aware thresholds (core change → all surfaces)
The decision surface is full of **fixed magic numbers that ignore data shape**
(~40 catalogued). The high-value, low-risk conversions:
- **Identifier cardinality floor** `≥0.95` (`autoconfig.py:216`) → `≥(1 − 1/√n)`:
  a 10k-row 0.95-cardinality column is plausibly a high-entropy name, not an ID.
- **Sparse-match floor** `50` hits (`indicators.py:142`) → `min(50, 0.01·estimated_pairs)`:
  today it's row-count- and matchkey-independent.
- **`SIMPLE_PLAN_MAX_PAIRS = 50M`** (planner) — fixed regardless of row count;
  revisit as part of S1 once pair counts are trustworthy.
- **Fix type:** smarter heuristic, mostly in the **core** (planner + classifier),
  so all surfaces inherit it. **Constraint:** each change regenerates golden
  vectors and MUST be validated on DQbench/F1 — a threshold that helps one dataset
  can regress another. Land them one at a time, each behind its own benchmark delta.

### S3. Per-type exact-matchkey cardinality thresholds (closes a standing TODO, #715)
- **Where:** `autoconfig.py:877` — single `≥0.5` floor for *all* exact matchkeys
  (marked TODO). Low-cardinality shared attributes (e.g. `city`, `state`) at 0.5
  slip into exact keys and cause mega-matches.
- **Fix:** per-type floors (email ~0.7, phone ~0.3, generic name ~0.5), empirically
  justified. Core change; golden re-gen + DQbench gate.

### S4. Multi-signal rule firing (reduce single-signal misfires)
- Refit rules fire on one signal without cross-check — e.g. `rule_no_matches`
  lowers the threshold on `mass_above==0` without first checking blocking is
  healthy (broken blocking and too-strict scoring need opposite fixes).
- **Fix:** conjunctive predicates (e.g. lower threshold only if reduction-ratio is
  healthy AND mass==0). Core (refit rules). Gate on the controller behavior fixtures + F1.

### S5. (Watch, not now) LLM-escalation calibration
- LLM-reclassified columns always get confidence `0.85` regardless of model
  certainty; the prompt asks for type+ranking, not calibrated confidence. Real but
  lower-leverage than S1–S3 and opt-in only. Defer.

---

## What NOT to re-derive (settled by prior art)

- **Bigger samples don't fix the extrapolation bias** — only full-frame
  measurement does (S1). Settled by the 2026-06-21 finding.
- **A native kernel is not the profiler speed lever** — F1's Polars restructure is;
  `candidate_pair_count` native is a wash-to-loss at scale. Settled by Stage-D.
- **The native core's payoff is parity + correctness, not profiler wall-clock** —
  don't justify core work on "it's faster"; justify it on "one source of truth,
  every surface, auditable."
- **Iteration-budget exhaustion / collision-signal heuristics** — already
  sidestepped by Path Y eager promotion; don't reopen.

## Recommended sequencing

1. **F1** (Polars restructure) — small, measured, unblocks everything. Ship with the bench.
2. **S1** (measured/Chao1 pair counts) + **F2** (default-flip) — the biggest
   correctness win; F2's default flip gated on a fresh 1M/10M wall bench.
3. **S2/S3** — adaptive + per-type thresholds, **one PR per threshold**, each
   gated on a DQbench/F1 delta and (for core changes) a golden-vector re-gen.
4. **S4**, then revisit **F3/S5** only if measurement justifies.

## Open question for the user

Two viable scopes for this branch — flagging before I write the implementation plan:
- **(a) Speed-first slice:** land F1 (+ its bench) now as a tight, measured PR;
  spin S1/S2 into follow-ups. Fast, low-risk, immediate.
- **(b) Correctness-first slice:** F1 → F2 → S1 as one arc (measured blocking
  becomes the default), since F1 mainly *matters* because it unlocks S1.
Either way S2/S3 (threshold smartening) are separate, benchmark-gated PRs.
