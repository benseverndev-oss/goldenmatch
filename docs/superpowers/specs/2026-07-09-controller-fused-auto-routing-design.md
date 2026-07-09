# Controller auto-routing to the fused path under memory pressure — design

**Date:** 2026-07-09
**Status:** Design (approved), pre-implementation
**Package:** `goldenmatch` (Python controller + pipeline)
**Depends on:** golden_fused (PR #1604, not yet on main) + match_fused (on main, #1600).
Execution branches off `feat/golden-fused-kernel`; rebase onto main once #1604 lands.
**Related:** [[project_fused_match_kernel]], [[project_fused_golden_kernel]], Controller v3 planner (#259-#267).

## 1. Problem

The fused Arrow-native match (`match_fused`) and golden (`golden_fused`) kernels are byte-identical,
wall-neutral, and ~2x lower peak RSS = 2x single-box capacity. But **neither is wired into
`pipeline.py`** and **the controller doesn't know they exist** — so the proven capacity win reaches
no real `dedupe_df` call. A win that is opt-in-by-hand ships no value.

**Goal:** have the auto-config controller + pipeline route to the fused path automatically wherever
it is a safe win, so a covered workload keeps its ~2x capacity headroom by default — with the
byte-identical guarantee preserved by construction.

## 2. The asymmetry that drives the design

The two fused paths are NOT symmetric:

- **`golden_fused` is a transparent drop-in.** Byte-identical golden output; its own gate already
  declines fast-path-eligible configs (so it only bites on the RSS-heavy slow path). Routing to it
  loses nothing. -> route whenever covered, no memory gate needed.
- **`match_fused` is clusters-only.** It fuses block->score->cluster into one call and returns
  `(__row_id__, __cluster_id__)`, so it DROPS the artifacts the classic path produces:
  `scored_pairs`, `pair_scores`, cluster confidence/bottleneck, lineage, review-queue candidates,
  explain data. It is a capacity-survival path, not a transparent one. -> route only when covered
  AND under real memory pressure AND the run needs none of those artifacts.

Also: the **controller is the only place with a `RuntimeProfile`** (available RAM); the pipeline
seams have none. So the match routing DECISION lives in the controller planner; golden routing is
pipeline-local (no RAM signal needed).

**Safety by construction:** every fused entry declines to `None`/falls back to the classic path, so
a mis-route can only cost a fallback, never a wrong result. Byte-identity holds regardless of the
routing logic's precision.

## 3. Golden routing (pipeline-local, default-on)

At the golden seam in `core/pipeline.py::_run_dedupe_pipeline` (the `cluster_frames is not None`
branch, ~line 2386, and the dict slow path ~2536): build the multi-member clustered frame the
classic path already assembles (`multi_df` / `_multi_df_from_frames`), then try
`run_golden_fused_arrow(that_frame, golden_rules, quality_scores=, cluster_pair_scores=,
provenance=needed)`. Use its result if non-`None`; else fall back to
`build_golden_records_from_frames` / `build_golden_records_batch`.

- The fused win is in the PROCESSING (it avoids the per-cluster Python dicts + the golden-record
  list-of-dicts), NOT the input frame — both paths need the clustered-rows frame.
- `run_golden_fused_arrow` itself declines when `_polars_native_eligible` is True, so fused golden
  only bites on the slow path (exactly where the RSS win lives) — the fast columnar path is
  untouched.
- **Default-on**; `GOLDENMATCH_GOLDEN_FUSED=0` kill-switch; native-required (declines if the
  `golden_fused` symbol is absent). Fires on every covered slow-path config — the broad win.

## 4. Match routing (controller rule + plan field + pipeline short-circuit)

### 4.1 The est-peak-RSS model

A small pure function `estimate_classic_match_peak_rss_gb(...)` next to the planner rules, estimating
the CLASSIC match path's peak RSS from signals the controller already has:

```
frame_gb  = n_rows * n_score_cols * BYTES_PER_CELL      # materialized matchkey columns
pairs_gb  = estimated_pair_count * BYTES_PER_PAIR       # the scored-pairs list/store
block_gb  = block_sizes_max^2 * 8 * BLOCK_CONCURRENCY   # peak concurrent cdist matrices (float64)
est_classic_rss_gb = (frame_gb + pairs_gb + block_gb) / 1e9
```

Inputs: `n_rows` (n_rows_full), `estimated_pair_count` + `block_sizes_max` (from
`ComplexityProfile.blocking`), `n_score_cols` (count of matchkey comparison fields in the config).

**Calibration is the honesty mechanism.** Coefficients start from physical sizes (`BYTES_PER_PAIR
~64`, `BYTES_PER_CELL ~40`, float64 matrices); a **calibration test** pins
`estimate_classic_match_peak_rss_gb(bench_inputs)` against the match memcap bench's MEASURED classic
peaks (~5.19 GB classic at 10M per the `fused_match` docstring; the bench records 1M/5M/10M) within a
tolerance band (+/-30%). A single scale coefficient absorbs the residual. The test guards the model
against drift; the model is tuned to measured reality, not asserted from first principles.

`PRESSURE_FRACTION ~0.6-0.7` (route BEFORE the ceiling, leaving headroom the model doesn't capture).
All four constants (`BYTES_PER_PAIR`, `BYTES_PER_CELL`, `BLOCK_CONCURRENCY`, `PRESSURE_FRACTION`) are
`GOLDENMATCH_*` env-overridable so the trigger tunes without a code change.

**Why roughly-wrong is safe:** the estimator only decides WHETHER to try fused; `match_fused_ready`
+ the decline-to-`None` fallback mean an over-trigger costs a fallback and an under-trigger uses the
classic/out-of-core path. Directionally right + calibrated is enough.

### 4.2 The planner rule

New `PlannerRule` `plan_fused_match`, inserted into `DEFAULT_RULES` (`core/autoconfig_planner_rules.py`)
**before** `rule_duckdb` and after the simple/fast_box/bucket rules. The rule needs the config +
output flags, so extend the planner `context` (today `{"user_backend": None}`) to carry
`{config, needs_artifacts, em_result}`. Fires when ALL THREE hold:

1. **covered** — `match_fused_ready(config)` OR `match_fused_fs_ready(config)` (with `em_result`) OR
   `match_fused_multipass_ready(config)`.
2. **under pressure** — `estimate_classic_match_peak_rss_gb(...) > available_ram_gb * PRESSURE_FRACTION`.
3. **artifacts not needed** — `not needs_artifacts` (see 4.3).

Action -> `ExecutionPlan(use_fused_match=True, rule_name="plan_fused_match")`. `ExecutionPlan` gains a
new `use_fused_match: bool = False` field; `apply_to` sets a flag the pipeline reads (a config field
or a dedicated attribute).

### 4.3 The `needs_artifacts` gate (conservative by design)

`needs_artifacts = True` if the run requests / consumes anything `match_fused` cannot produce:
output-lineage, review-queue, explain, returned `scored_pairs`, cluster confidence/bottleneck, OR a
golden config using `confidence_majority` (needs pair scores). This makes match routing **rare and
correct** — it fires only on artifact-free runs under real pressure (the last-resort capacity mode).
Under pressure WITH artifacts, the existing `rule_duckdb` / `rule_chunked` (which spill pairs to disk
and PRESERVE artifacts) still fire; `plan_fused_match` sits in front of them only for the artifact-free
case. The golden win is the broad one; match is the narrow survival extra.

### 4.4 The pipeline short-circuit

At the block/score/cluster seam in `_run_dedupe_pipeline` (~line 1657, before the block-build loop):
if the `use_fused_match` flag is set, short-circuit to the matching `run_match_fused_*_arrow`, feed
its `(row_id, cluster_id)` clusters into the golden seam, and skip the classic block->score->cluster.
On `None` (declined), fall back to the classic path. The `needs_artifacts` gate already ensured the
dropped artifacts aren't requested, so simply not producing them is correct.

- **Default-on-when-all-three-hold**; `GOLDENMATCH_MATCH_FUSED=0` kill-switch; native-required.

## 5. Telemetry

`ExecutionPlan.rule_name` already surfaces on `PostflightReport` / `serialize_telemetry`, so
`plan_fused_match` is observable. Add a `golden_fused_used: bool` to the result / telemetry so the
golden routing (which is pipeline-local, not a planner rule) is also visible — essential for
answering "is it actually routing."

## 6. Testing

- `estimate_classic_match_peak_rss_gb` unit tests + the bench-calibration test (asserts est vs the
  memcap bench's measured classic peaks within tolerance at 1M/5M/10M).
- Planner-rule tests: covered + pressure + artifact-free -> `use_fused_match`; each of the three
  conditions falsified -> not (and `needs_artifacts` -> falls to duckdb/chunked under pressure).
- Golden-wiring parity test: a fused-routed `dedupe_df` on a covered slow-path config produces
  byte-identical golden output vs the classic path (reuse the golden_fused parity discipline).
- Match short-circuit test: covered + pressure + artifact-free routes to fused and its clusters match
  the classic clustering on the same config; uncovered / artifacts-needed / no-pressure fall back.
- Kill-switch test: `GOLDENMATCH_GOLDEN_FUSED=0` / `GOLDENMATCH_MATCH_FUSED=0` restore the classic
  path byte-for-byte.

## 7. Non-goals

- Distributed / Sail fused routing.
- The est-RSS model feeding any non-fused rule (it is only for `plan_fused_match`).
- Changing the existing backend rules' thresholds.
- A dedicated user-facing "capacity mode" API (match routing is auto-gated; an explicit override can
  be a follow-up if needed).

## 8. Risks

- **est-peak-RSS model accuracy.** Mitigation: calibrate against the memcap bench; the decline-to-None
  fallback bounds the cost of a wrong estimate; the model is directionally-right-and-tunable, not
  precise.
- **`needs_artifacts` completeness** — missing an artifact-consuming path would silently drop it under
  pressure. Mitigation: enumerate conservatively (any doubt -> needs_artifacts True -> classic/duckdb);
  the pipeline short-circuit only triggers on the explicit flag.
- **Planner-rule ordering** — `plan_fused_match` must sit before `rule_duckdb` but after the
  in-memory-cheap rules so it only fires under genuine pressure. Pin the order in a test.
- **#1604 dependency** — golden_fused not yet on main; execution branches off `feat/golden-fused-kernel`
  and rebases onto main once it lands.
