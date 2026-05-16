# Controller v3 — the auto-config controller becomes the execution planner

**Status:** COMPLETE — landed 2026-05-16 across PRs #259-#267. Acceptance criteria below.
**Status (original):** Design (drafted 2026-05-15)
**Author:** Claude + bsevern, brainstorm from the 50M readiness conversation
**Scope:** `packages/python/goldenmatch/goldenmatch/core/autoconfig.py`, `core/autoconfig_controller.py`, `core/autoconfig_policy.py`. Extension to the existing introspective controller's decision space.
**Related:**
- Existing controller spec: [`2026-05-06-autoconfig-introspective-controller-design.md`](2026-05-06-autoconfig-introspective-controller-design.md) — v1 picks blocking key, matchkey weights, threshold. This spec extends to also pick backend + chunk_size + workers + spill thresholds.
- Distributed Plan v1: [`2026-05-15-distributed-plan-v1-design.md`](2026-05-15-distributed-plan-v1-design.md) — the 50M architecture. Controller v3 is the planner that selects which execution plan to run.
- PR #239: introduced the prototype `autoconfig.py` scale-aware backend selection (`GOLDENMATCH_AUTOCONFIG_BACKEND_THRESHOLD` env var). This spec promotes that env-var hack to first-class controller state.

## Problem

GoldenMatch's zero-config promise is "user calls `gm.dedupe_df(df)` and we figure out everything else." Today, "everything else" means: blocking keys, matchkey weights, threshold, scorers — but **not** backend, chunk size, worker count, or memory budget. Those are left as kwargs the user has to pick.

The result: real callers either accept the default polars-direct backend (which OOMs at 5M on a 16GB box) or read CLAUDE.md and learn the spell "use `backend='chunked'` + `config_mode='explicit-personlike'`". That spell defeats zero-config.

PR #239's `autoconfig.py` already prototypes the right idea:

```python
# at n_rows >= 1M, force backend="duckdb"
if n_rows >= int(os.environ.get("GOLDENMATCH_AUTOCONFIG_BACKEND_THRESHOLD", "1_000_000")):
    config.backend = "duckdb"
```

That's planning. It's just hard-coded, single-signal, and env-var-gated. **Controller v3 makes execution planning a first-class concern of the auto-config loop.**

## Goals

1. **Auto-select execution plan** without user input: backend, chunk_size, max_workers, pair_spill_threshold, and clustering strategy (in-memory union-find vs spill-and-merge).
2. **Signal-driven, not row-count-thresholded.** Use the controller's existing `ComplexityProfile` (block size distribution, estimated pair count) + runtime introspection (available RAM via psutil) to decide.
3. **Backward compat.** Users who pass `backend="..."` or `config.backend=...` explicitly still get their choice; the planner only fills gaps.
4. **Observable.** The planner's decision + reasoning lands on `PostflightReport.controller_history` next to the existing matchkey/blocking decisions.

## Non-goals (v1)

- Implementing the chunked or distributed backends themselves. v3 is the planner; the backends it selects from must exist or be tagged as "planned" placeholders.
- LLM-driven planning. The `LLMRefitPolicy` hook from the v1 controller spec is the future home; not implemented here.
- Cross-machine cluster orchestration. The planner decides "use Ray with N workers"; Ray initialization stays in the existing `backends/ray_backend.py`.

## Decision space (what the planner picks)

Six knobs the user currently has to set manually (or accepts defaults that don't scale):

| Knob | Current default | Range | Picked when |
|---|---|---|---|
| `backend` | `"polars-direct"` | `polars-direct` / `chunked` / `duckdb` / `ray` | Always (default not size-aware) |
| `chunk_size` (chunked backend) | `100_000` | 10K–1M | Backend = `chunked` |
| `max_workers` (Ray + ThreadPoolExecutor) | 4 | 1 to `os.cpu_count()` | Always |
| `pair_spill_threshold` (when to write pairs to disk vs RAM) | None — all pairs in RAM | None / 1M / 10M / 100M | Backend ∈ {`chunked`, `duckdb`, `ray`} |
| `clustering_strategy` | In-memory union-find on all pairs | `in_memory` / `partitioned_union_find` / `streaming_cc` | Backend / spill threshold combination |
| `auto_fix_quality_scan_mode` | enabled, runs 3x via controller iteration | `enabled` / `cached` / `disabled` | Always — caching is the v3 add |

## Signals (what the planner reads)

Existing on `ComplexityProfile` (no new collection needed):

- `DataProfile.n_rows`, `DataProfile.column_types`, `DataProfile.cardinality_ratio`
- `BlockingProfile.n_blocks`, `BlockingProfile.block_sizes_p50/p95/p99/max`, `BlockingProfile.total_comparisons`
- `MatchkeyProfile.per_field.*`

New signals to add:

- `RuntimeProfile.available_ram_gb`: `psutil.virtual_memory().available / (1024**3)` at controller-start.
- `RuntimeProfile.cpu_count`: `os.cpu_count()`.
- `RuntimeProfile.disk_free_gb` (for spill threshold sanity).
- `EstimatedPairCount`: `sum(max(0, n) * max(0, n-1) // 2 for n in block_sizes)`. Computed from `BlockingProfile`. Already implicitly available; surface as a named field.

## Decision rules (the planner's policy table)

Each rule is a `(predicate, action)` pair. Rules evaluate in order; first match wins. The point is to make the policy table readable and overridable, not a hairy if-tree.

### Rule 1 — pathological inputs (mirror existing controller §Error handling 6)

| Predicate | Action |
|---|---|
| `n_rows == 0` | `ConfigValidationError` |
| `n_rows == 1` | Skip controller; return v0 |
| All columns null | `ConfigValidationError` |
| Single non-empty column | v0 + `health=yellow` |

### Rule 2 — small enough for the simple plan

| Predicate | Action |
|---|---|
| `n_rows < 100_000` AND `estimated_pair_count < 50_000_000` | `backend=polars-direct`, `chunk_size=None`, `max_workers=min(4, cpu_count)`, `clustering=in_memory`, `pair_spill=None` |

This is the "laptop demo" plan. Fits comfortably in 8 GB.

### Rule 3 — large rows, sparse pairs

| Predicate | Action |
|---|---|
| `n_rows >= 100_000` AND `estimated_pair_count < 50_000_000` AND `available_ram_gb >= 32` | `backend=polars-direct`, `max_workers=min(cpu_count, 16)`, `clustering=in_memory` |

The mid-tier "fast box" plan. Examples: 5M rows with very selective blocking keeping pair count under 50M.

### Rule 4 — large rows, lots of pairs, fits in memory with chunking

| Predicate | Action |
|---|---|
| `estimated_pair_count >= 50_000_000` AND `estimated_pair_count < 5_000_000_000` AND `available_ram_gb >= 16` | `backend=chunked`, `chunk_size=auto_chunk_size(n_rows, available_ram_gb)`, `pair_spill=ram`, `clustering=in_memory`, `max_workers=min(cpu_count, 16)` |

Where `auto_chunk_size = n_rows // ceil(estimated_memory_gb / available_ram_gb_target_use_fraction)`. Concretely: target using 60% of available RAM, divide rows accordingly.

### Rule 5 — DuckDB out-of-core regime

| Predicate | Action |
|---|---|
| `estimated_pair_count >= 5_000_000_000` OR `available_ram_gb < 16` | `backend=duckdb`, `pair_spill=duckdb`, `clustering=partitioned_union_find`, `max_workers=min(cpu_count, 8)` |

The "single-box scale" plan. Uses DuckDB as the data plane (spill to disk natively, parallel query exec). Clustering shifts to partitioned union-find because the full pair set won't fit in Python memory.

### Rule 6 — Ray escape hatch

| Predicate | Action |
|---|---|
| `n_rows >= 50_000_000` AND `ray_available` (`ray` import succeeds, cluster initializable) | `backend=ray`, `max_workers=cpu_count_total_cluster`, `pair_spill=disk_per_worker`, `clustering=streaming_cc` |

The 50M+ plan. Requires Distributed Plan v1's clustering strategy. Falls back to Rule 5 if Ray isn't available.

### Rule 7 — explicit override always wins

If the user passes `config.backend` or `gm.dedupe_df(..., backend=...)` explicitly, **all of the above rules are skipped for the backend slot**. The planner still fills `chunk_size`, `max_workers`, etc. for whichever backend the user picked.

## Auto-cache the auto-fix + quality scan

Per the post-#239 100K cProfile, `transform.run_transform` and the GoldenCheck quality scan each fire **5 times** per `dedupe_df()` call because the controller iterates 5 times (sample iterations + finalize). The transform output is deterministic for a given input; cache it.

Implementation:

- `ControllerCache` keyed on `(content_hash(df), config_version_hash)`.
- First iteration computes + caches; subsequent iterations read from cache.
- Cache lives on the controller instance, dies with the dedupe call (no cross-call persistence in v1).

Expected savings: ~10s per 100K dedupe (12.91s cumtime for `run_transform` reduced to ~3s for one application + ~0.1s × 4 for cache hits).

Out of scope: cross-call caching (would persist across `dedupe_df` invocations; risk of stale entries; needs the existing AutoConfigMemory mechanism). v1 caches within a single call only.

## Observability

Planner decisions land on `RunHistory.decisions` alongside the existing matchkey/blocking decisions. New `PolicyDecision.rule_name` values:

- `plan_selected_simple` (Rule 2)
- `plan_selected_fast_box` (Rule 3)
- `plan_selected_chunked` (Rule 4)
- `plan_selected_duckdb` (Rule 5)
- `plan_selected_ray` (Rule 6)
- `plan_user_override` (Rule 7)

Each carries `config_diff` with the actual `backend`, `chunk_size`, `max_workers`, etc. selected.

`PostflightReport` gets one new field surfaced via the existing controller-history hook: `execution_plan`, a frozen dataclass capturing all six knobs. CLI / MCP / web tab rendering updated to show this.

## Pipeline integration

The planner runs **as the last step of `AutoConfigController.run`**, after the existing matchkey/blocking/threshold loop converges. It reads the final `ComplexityProfile` (which has the block size distribution from the last iteration) plus `RuntimeProfile`, applies the rule table, and writes the plan onto `GoldenMatchConfig` before returning.

Order matters: the existing controller iterates on a 2K-row sample to pick blocking/matchkey/threshold. The plan that runs the final full-data dedupe is selected **based on the full-data row count + the sampled block size distribution + the actual runtime**. Sampling doesn't tell us the runtime is — that's a runtime introspection.

```
AutoConfigController.run(df):
    profile_v0 = profile(sample(df))
    config_v0 = initial_config(profile_v0)
    config_n, profile_n, history = iterate(sample(df), config_v0)
                                   # picks matchkey/blocking/threshold
    runtime = capture_runtime_profile()     # NEW: psutil snapshot
    plan = apply_planner_rules(profile_n.extrapolate(n_rows_full),
                               runtime, config_n)        # NEW
    config_n.apply_plan(plan)               # NEW
    history.append(PolicyDecision(rule_name="plan_selected_*", ...))
    return config_n, profile_n, history
```

`profile_n.extrapolate(n_rows_full)` projects the sample's block-size distribution to the full data. The simplest projection: `n_blocks_full = n_blocks_sample * (n_rows_full / n_rows_sample)`, `block_sizes_*_full = block_sizes_*_sample * (n_rows_full / n_rows_sample)`. Crude but defensible — pair count estimate is the load-bearing signal, and that scales linearly with the projection.

## Backward compatibility

- Users on `gm.dedupe_df(df)` zero-config: plan auto-selected. Behavior changes for callers near the rule boundaries (e.g. previously OOM'd at 5M, now switches to chunked transparently). This is the headline improvement.
- Users on `gm.dedupe_df(df, backend="...")`: explicit override wins per Rule 7. No behavior change.
- Users on `config.backend="..."` via YAML: same as above. The plan fills only the unset knobs.
- The `GOLDENMATCH_AUTOCONFIG_BACKEND_THRESHOLD` env var from PR #239 is retained as a Rule 5 threshold override for one release, with a `DeprecationWarning` when set. Drop in v2.

## Testing

### Tier 1 — unit tests on the rule table

Each rule gets its own test: construct a synthetic `ComplexityProfile` + `RuntimeProfile` that should hit the rule, call the planner, assert the right `config_diff`. ~10 tests.

Boundary tests for each rule's edge of applicability (n_rows just below / just above threshold). ~6 tests.

User-override test: user passes `backend="ray"` on a 1K dataset. Assert Rule 7 fires and the plan has `backend="ray"` regardless of size. ~2 tests.

### Tier 2 — integration tests

For each plan tier, run `gm.dedupe_df` end-to-end on a fixture sized to trigger that tier:

- Simple plan: Febrl3 (5K). Assert plan rule + clean run.
- Fast-box plan: synthetic 100K. Assert plan rule + clean run.
- Chunked plan: synthetic 500K with available RAM mocked to 8 GB. Assert chunk_size auto-picked + clean run.
- DuckDB plan: synthetic 500K with available RAM mocked to 4 GB. Assert DuckDB selected + clean run.
- Ray plan: synthetic 1M with `ray` available + sample of pair count ≥ 50M. Assert Ray selected (skipped if `ray` not installed).

### Tier 3 — bench gate

Re-run `bench-zero-config` at 100K (post-attack baseline) and confirm:
- Plan selected: `simple` (Rule 2).
- 100K wall ≤ 24s (carries over from map_elements attack).
- Cached transform: `run_transform` cumtime in cProfile < 4s (was 12.91s).

Re-run at 500K:
- Plan selected: probably `fast-box` on the 64 GB runner (Rule 3); on a 16 GB lane it should select `chunked` (Rule 4).
- Wall ≤ 110s (extrapolated from 100K × 5x linear).

### Tier 4 — scale audit

The existing scale-audit-5m workflow at 5M:
- Pre-v3: requires explicit `backend="chunked"` + `config_mode="explicit-personlike"` per CLAUDE.md (~50 min, 11.9 GB peak).
- Post-v3: `gm.dedupe_df(df)` zero-config should produce the same plan automatically. Same wall + RSS targets ±10%.

This is the load-bearing test for the spec. If 5M zero-config doesn't auto-select `chunked` with sensible chunk size on `ubuntu-latest-large`, the planner is wrong.

## Open questions

1. **Pair-count extrapolation accuracy.** The simple linear projection from sample to full-data block size distribution will be wrong for skewed data. Worst case: sample shows tight blocks (P99=200), full data has one giant block (P99=50K). The planner picks the wrong tier. Mitigation: bias toward larger plans when the sample's `block_sizes_p99 / block_sizes_p50 > 5` (likely-skewed signal).
2. **Runtime introspection on Ray.** `psutil.virtual_memory()` reports the head node's memory, not the worker pool's. The Ray rule needs cluster introspection (`ray.cluster_resources()`). Out of scope for v1; document the caveat.
3. **Cache invalidation correctness.** The within-call transform cache is keyed on `content_hash(df)` — needs to hash the polars Series content stably. Use Polars's built-in hash for the column subset that transforms touch; document the key derivation.
4. **Threshold values.** All concrete thresholds (50M pair count, 16 GB RAM, 100K row breakpoint) are starting points. Each will need calibration once we have measurements at each tier.

## Acceptance criteria

- v1 ships when:
  1. All six knobs in the decision space have a default rule that fires sensibly on test fixtures.
  2. `ExecutionPlan` lands on `PostflightReport` and is visible in CLI / MCP output.
  3. 100K bench shows the cached transform (cumtime < 4s) without regression.
  4. The 5M zero-config audit produces the same wall + F1 as the documented explicit-personlike + chunked combo, ±10%.
  5. All existing tests continue to pass.
  6. Spec [`2026-05-15-map-elements-attack-design.md`](2026-05-15-map-elements-attack-design.md) is merged before this; the planner cache assumes per-call transform is the expensive bit.
