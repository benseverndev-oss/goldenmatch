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
- **Provenance caveat (a real decline condition, not just a passthrough).** `run_golden_fused_arrow`
  emits field-level `{value, confidence, source_row_id}` but does NOT reproduce the slow path's
  top-level `__survivorship_prov__` (`ClusterProvenance`: group `tie` flag + conditional
  fired-clause strategy). So "transparent" holds only when the run does NOT consume full
  `ClusterProvenance`. The golden wiring must therefore ALSO fall back to `build_golden_records_batch`
  when the run requests full-provenance lineage (a `__survivorship_prov__` consumer), not merely
  field-level `source_row_id`. Absent that, field-level provenance is byte-identical.
- **Default-on**; `GOLDENMATCH_GOLDEN_FUSED=0` kill-switch; native-required (declines if the
  `golden_fused` symbol is absent). Fires on every covered slow-path config — the broad win.

## 4. Match routing (controller post-step + plan field + pipeline short-circuit)

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

**Input-scale caveat (the plan must handle this).** `estimated_pair_count` is extrapolated to full
data (`BlockingProfile.extrapolate_to` scales `total_comparisons` by the row ratio), but
`block_sizes_max` is explicitly NOT scaled by `extrapolate_to` — it reflects the SAMPLE's max block,
so the `block_gb` term under-reads at scale while `pairs_gb` is full-scale. The plan must either
(a) feed a full-scale block-size signal (use `measure_blocking_profile`'s measured max when
`planning_effort in (thinking, einstein)`, else extrapolate the sample max by the row ratio), or
(b) fold the block term's scale bias into the calibration coefficient — and the calibration test must
assert the model at the full-data (n_rows, est_pairs, block_max) the bench actually ran, not at
sample scale. Pin this in the plan; a naive sample-scale `block_sizes_max` silently under-triggers.

**Why roughly-wrong is safe:** the estimator only decides WHETHER to try fused; `match_fused_ready`
+ the decline-to-`None` fallback mean an over-trigger costs a fallback and an under-trigger uses the
classic/out-of-core path. Directionally right + calibrated is enough.

### 4.2 A Python post-step, NOT a planner rule (the native short-circuit)

**Critical constraint discovered in review:** `apply_planner_rules` short-circuits to the native
kernel (`_nm.autoconfig_decide_plan(...)`) BEFORE the Python rule loop whenever `native_enabled(
"autoconfig")` AND the wheel carries the symbol AND `rules is _default_rules()` — and autoconfig is
native-default-on. So a new `PlannerRule` added to `DEFAULT_RULES` would be **dead code in the
default environment** (the Rust kernel decides the plan), silently no-op'ing match routing. Teaching
the Rust kernel about fused routing would be a cross-language JSON-round-trip change.

**Instead, decide match routing as a Python post-step in the controller, AFTER the backend plan is
chosen** (whether the native kernel or the Python rules chose it). Fused-match is a whole-stage
short-circuit, ORTHOGONAL to backend selection — the chosen backend (bucket/duckdb/…) becomes the
FALLBACK that runs if fused declines. A new `maybe_route_fused_match(committed_config, profile,
runtime, *, needs_artifacts, em_result) -> bool` runs at `autoconfig_controller.py` ~1246-1254
(right after `apply_planner_rules` + `plan.apply_to`), and sets `use_fused_match` on the plan/config
when ALL THREE hold:

1. **covered** — `match_fused_ready(config)` OR `match_fused_fs_ready(config)` (with `em_result`) OR
   `match_fused_multipass_ready(config)`.
2. **under pressure** — `estimate_classic_match_peak_rss_gb(...) > available_ram_gb * PRESSURE_FRACTION`.
3. **safe** — `not needs_artifacts` (see 4.3).

`ExecutionPlan` gains a `use_fused_match: bool = False` field; `apply_to` sets a flag the pipeline
reads (`apply_to` already writes `backend` AND `_throughput_plan`, so this follows the existing
attribute-write precedent — one more attribute). This sidesteps the native short-circuit entirely and
needs no Rust change. `rule_name` records `"fused_match_post_step"` for telemetry alongside the
backend rule's name.

### 4.3 The `needs_artifacts` / no-divergence gate (conservative by design)

`match_fused` returns bare connected components, so it drops MORE than artifacts — it also skips the
classic clustering's oversized-cluster handling. `needs_artifacts = True` (do NOT route) if ANY hold:

- **output artifacts requested/consumed:** output-lineage (incl. full `__survivorship_prov__`),
  review-queue, explain, returned `scored_pairs`, cluster confidence/bottleneck, OR a golden config
  using `confidence_majority` (needs pair scores).
- **clustering would diverge:** `golden_rules.auto_split` is on (default True) — classic MST-splits
  oversized clusters + weak-cluster downgrade, which bare CCs don't; `config.identity.enabled` (builds
  evidence edges from pairs); anomaly detection requested.

So match routing fires only when the run is genuinely artifact-free AND its clustering can't diverge
(`auto_split` off, identity/anomaly off) AND covered AND under pressure — a deliberately narrow
last-resort capacity mode. Under pressure WITH any of these, the existing `rule_duckdb` /
`rule_chunked` (which spill pairs to disk and PRESERVE artifacts + do full clustering) still fire; the
fused-match post-step just declines and the chosen backend runs. **The golden win is the broad one;
match is the narrow survival extra.** (Because the gate is this narrow, the est-RSS model + post-step
serve a rarely-firing path by design — the machinery is the price of a safe auto capacity-survival.)

### 4.4 The pipeline short-circuit

At the block/score/cluster seam in `_run_dedupe_pipeline` (~line 1657, before the block-build loop):
if the `use_fused_match` flag is set, short-circuit to the matching `run_match_fused_*_arrow`, feed
its `(row_id, cluster_id)` clusters into the golden seam, and skip the classic block->score->cluster.
On `None` (declined), fall back to the classic path. The `needs_artifacts` gate already ensured the
dropped artifacts aren't requested, so simply not producing them is correct.

- **Default-on-when-all-three-hold**; `GOLDENMATCH_MATCH_FUSED=0` kill-switch; native-required.

## 5. Telemetry

The plan carries `use_fused_match` + `rule_name="fused_match_post_step"`, which surface on
`PostflightReport` / `serialize_telemetry`, so the match-routing decision is observable. Add a
`golden_fused_used: bool` to the result / telemetry so the
golden routing (which is pipeline-local, not a planner rule) is also visible — essential for
answering "is it actually routing."

## 6. Testing

- `estimate_classic_match_peak_rss_gb` unit tests + the bench-calibration test (asserts est vs the
  memcap bench's measured classic peaks within tolerance at 1M/5M/10M).
- `maybe_route_fused_match` post-step tests: covered + pressure + safe -> `use_fused_match`; each of
  the three conditions falsified -> not (and each `needs_artifacts` / divergence sub-condition
  — auto_split/identity/anomaly/lineage/review/explain/pairs/confidence_majority — individually
  blocks). Assert the post-step runs even under native-autoconfig (mock native plan chosen, post-step
  still sets the flag) — the whole point of not being a `DEFAULT_RULES` rule.
- Golden-wiring parity test: a fused-routed `dedupe_df` on a covered slow-path config produces
  byte-identical golden output vs the classic path (reuse the golden_fused parity discipline); a
  full-`ClusterProvenance` run declines to classic.
- Match short-circuit test: covered + pressure + safe routes to fused and its clusters match the
  classic clustering on the same config; uncovered / needs-artifacts / diverging / no-pressure fall
  back.
- Kill-switch test: `GOLDENMATCH_GOLDEN_FUSED=0` / `GOLDENMATCH_MATCH_FUSED=0` restore the classic
  path byte-for-byte.

## 7. Non-goals

- Distributed / Sail fused routing.
- The est-RSS model feeding any non-fused decision (it is only for the fused-match post-step).
- Teaching the native `autoconfig_decide_plan` kernel about fused routing (the Python post-step
  sidesteps it; a native round-trip is a possible future optimization, not needed now).
- Changing the existing backend rules' thresholds.
- A dedicated user-facing "capacity mode" API (match routing is auto-gated; an explicit override can
  be a follow-up if needed).

## 8. Risks

- **Native planner short-circuit (resolved in 4.2).** A `DEFAULT_RULES` planner rule would be dead
  under native-default-on autoconfig. Resolved by deciding match routing as a Python post-step after
  `apply_planner_rules` — no Rust change, and the post-step runs regardless of which engine chose the
  backend.
- **est-peak-RSS model accuracy + the sample-scale `block_sizes_max` input (4.1).** Mitigation:
  calibrate against the memcap bench at FULL-data inputs; feed a full-scale block signal or fold the
  bias into the coefficient; the decline-to-None fallback bounds the cost of a wrong estimate; the
  model is directionally-right-and-tunable, not precise.
- **`needs_artifacts` / divergence completeness** — missing an artifact-consuming OR
  clustering-diverging path (auto_split / identity / anomaly / lineage / review / explain / pairs /
  confidence_majority / cluster-confidence) would silently drop or diverge it under pressure.
  Mitigation: enumerate conservatively (any doubt -> needs_artifacts True -> classic/duckdb); the
  short-circuit only triggers on the explicit flag; a test asserts each condition gates.
- **Golden provenance degradation (3)** — golden default-on with a full-`ClusterProvenance` consumer
  would silently lose richness. Mitigation: the golden wiring declines to `build_golden_records_batch`
  when full provenance is consumed.
- **#1604 dependency** — golden_fused not yet on main; execution branches off `feat/golden-fused-kernel`
  and rebases onto main once it lands.
