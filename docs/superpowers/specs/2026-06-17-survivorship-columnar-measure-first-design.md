---
title: Columnar survivorship scale path (measure-first verdict)
date: 2026-06-17
status: design (approved in brainstorming; pre-spec-review)
owner: Ben Severn
related:
  - docs/superpowers/specs/2026-06-17-correlated-survivorship-and-conditional-golden-rules-design.md
  - docs/superpowers/specs/2026-06-17-groupprovenance-surfacing-design.md
  - docs/superpowers/specs/2026-06-17-allow-fill-anchor-strategy-design.md
---

# Columnar survivorship scale path (measure-first verdict)

> v2 workstream 2 of the correlated-survivorship program. A MEASURE-FIRST
> investigation: the deliverable is a bench + a measured verdict, NOT a
> rewrite. A Phase-2 vectorized rewrite is a separate, conditional spec
> that only happens if the verdict clears the bar.

## 1. Problem

Survivorship resolution does not use the fast columnar path. When
`_survivorship_active(rules)` is true, `_polars_native_eligible` returns
False and `build_golden_records_batch` takes the slow branch
(`core/golden.py` ~L884):

```python
s_sorted = multi_df.sort("__cluster_id__")
order = build_resolution_order(...)
for cdf in s_sorted.partition_by("__cluster_id__", maintain_order=True):
    rec, prov = resolve_cluster(cdf, rules, order, ...)   # per-cluster Python
```

`resolve_cluster` (`core/survivorship/resolve.py`) materializes one frame +
per-column Python lists PER CLUSTER (`col_arrays = {c: cluster_df[c].to_list()}`,
plus `source_array`/`row_id_array`), then resolves each unit (group via
`group_winner`, scalar via `merge_field`, conditional via the safe-`ast`
`eval_predicate`). At scale this is one Python iteration + materialization
per cluster: at ~1.67M clusters (5M rows, avg size 3) that is ~1.67M
iterations. The resolver's own docstring flags it: "materializes one frame +
per-column lists PER CLUSTER by design ... A columnar/array-slice
survivorship path that avoids per-cluster materialization is a tracked scale
follow-up."

**But the project's history says be skeptical.** Every prior columnar
rewrite in this codebase came in flat-or-negative: the scorer columnar A/B
was ~1-2% SLOWER with +13-16% RSS (`bench_scorer_columnar.py`); the Phase-2
Arrow columnar kernels stayed gated OFF (1.05-1.07x, +30% RSS). So a columnar
survivorship rewrite is a real risk of being a no-op. **This workstream
refuses to design the rewrite before measuring whether it would win.**

## 2. Goals / non-goals

**Goals**
- A bench (`scripts/bench_survivorship_columnar.py`) that measures the
  survivorship slow path's wall + RSS at scale on a realistic config, with a
  **per-phase breakdown** that attributes the cost.
- A measured **verdict** (committed report) with a go/no-go on a Phase-2
  vectorized rewrite, against an explicit kill criterion.
- The methodology is **floor + attribution, no prototype**: compare the slow
  path to the non-survivorship fast path (the floor / lower bound) and use
  the per-phase split to size the *recoverable* (vectorizable) portion of the
  tax. No vectorized prototype is built in this workstream (that would be
  half the rewrite, defeating measure-first).

**Non-goals**
- The vectorized rewrite itself (conditional Phase 2, separate spec).
- Any change to the production survivorship path. This workstream adds ONLY
  a bench script + a report. The slow path is untouched -> trivially
  byte-identical.
- Per-lever isolation benches. The brainstorm chose ONE realistic mixed
  config; the per-phase breakdown does the attribution.
- Distributed/Sail scale (survivorship already refuses those paths).

## 3. The bench

`scripts/bench_survivorship_columnar.py`, modeled on
`scripts/bench_scorer_columnar.py` (same shape: `argparse`, a
`make_workload(...)`, 5-run median wall, peak RSS).

### 3.1 Workload (realistic mixed config)

A person/address dataset generated with a dupe-rate knob driving cluster
count/size (reuse the `make_workload` pattern; synthetic surnames MUST
distribute across soundex codes per the project fixture lesson, else
blocking/clustering hangs -- but this bench operates on an
already-`__cluster_id__`-tagged multi-member frame, so it can synthesize
clusters directly and skip blocking entirely). Columns: `first_name`,
`last_name`, `street`, `city`, `state`, `zip`, `phone`, `updated_at`,
`source`. One realistic mixed `GoldenRulesConfig`:
- a `mailing_address` field_group over `{street, city, state, zip}`
  (`most_complete`),
- a conditional `phone` rule: `[{when: "state in ['CA','NY']", strategy:
  most_recent, date_column: updated_at, validate: nanp}, {strategy:
  source_priority, source_priority: [crm, billing]}]`,
- plain fields otherwise.

**Scales:** 1M and 5M rows. The per-cluster-loop cost tracks cluster COUNT,
so the bench reports cluster count and uses a dupe-rate giving many small
clusters (the worst case for the Python loop). A 50M extrapolation note is
derived, not run (cost ceiling).

### 3.2 What it measures (5-run median wall + per-phase peak RSS)

1. **Slow path** (`build_golden_records_batch` with the survivorship config),
   with a per-phase wall breakdown via lightweight timers around:
   `sort`, `partition_by`, the per-cluster loop, and -- inside a
   representative `resolve_cluster` sampling or an instrumented run --
   per-cluster **materialization** (`to_list`), **`group_winner`**, and the
   **conditional `eval_predicate`**. (Implementation note: prefer a coarse
   wall split via `GOLDENMATCH_BUCKET_DEBUG`-style env-gated timers added
   only to the bench's call path, NOT new production instrumentation, to keep
   the production path byte-identical. If per-`resolve_cluster` attribution
   needs finer detail, a sampling profiler (`py-spy`-style) over the bench
   process is acceptable and is captured in the report rather than as code.)
2. **Fast-path floor**: the SAME workload through the non-survivorship path
   (a plain `most_complete` `GoldenRulesConfig` with empty `field_rules`,
   which makes `_survivorship_active` False AND `_polars_native_eligible`
   True, routing to `_build_golden_records_polars_native` -- a genuinely
   vectorized `group_by`-per-column path, NOT the L904 col-arrays-once middle
   loop). This is the absolute lower bound on achievable wall -- a rewrite
   cannot beat it and realistically will not reach it (it still does
   group-winner work). **The bench MUST assert the floor lands on the
   vectorized path** (`_polars_native_eligible(floor_rules, None) is True`,
   mirroring `bench_scorer_columnar.py`'s eligibility guard) -- else a future
   eligibility-gate change could silently make the "floor" the slower middle
   loop and corrupt the tax.

Both report wall (5-run median, per the perf-audit "measure wall not cumtime"
lesson) and peak RSS markers (per the RSS-as-tracked-constraint discipline).

### 3.3 Where it runs

A `bench-survivorship-columnar.yml` `workflow_dispatch` workflow on
`large-new-64GB` (16c/64GB). The box is memory-starved; this bench does NOT
run on the laptop. Inputs: `rows` (1000000 / 5000000), `dupe_rate`. A 1k-row
smoke runs in the normal CI lane (Section 5).

## 4. Verdict methodology + kill criterion

`tax = slow_wall - floor_wall` is the TOTAL headroom. Split the slow wall by
the per-phase breakdown into:
- **Vectorizable phases** a rewrite could eliminate/vectorize: the per-cluster
  loop overhead (Python iteration), per-cluster materialization (`to_list`),
  and `group_winner` (expressible as a polars `group_by` + agg).
- **Inherently-Python phase**: the conditional `eval_predicate` -- a
  **per-conditional-field, per-cluster** `ast` walk (`select_conditional_strategy`
  calls it once per conditional field per cluster, against the cluster's
  already-resolved scalar dict; NOT per-row). It is far cheaper than a
  per-row walk would be, so a "dominated by the conditional eval -> NO-GO"
  outcome is correspondingly LESS likely; not vectorizable, stays on the slow
  path regardless.

**Recoverable = sum of the vectorizable-phase wall.** The floor bounds it
(recoverable cannot exceed `slow - floor`).

**Kill criterion (the distributed-plan-style bar):** pursue a Phase-2
vectorized rewrite ONLY if **recoverable >= ~25-30% of slow wall AND it
localizes** to the vectorizable phases (i.e. the cost is genuinely in the
loop/materialization/group_winner, not dominated by the conditional eval or
the unavoidable `merge_field` work). RSS must not regress (a vectorized path
that recovers wall but blows up RSS is also a no-go, matching the prior
columnar A/B failure mode). Otherwise the verdict is **"keep the slow path"**
with the numbers as proof (mirroring the columnar-pipeline-verdict and
quality-bridges-inert outcomes).

## 5. Deliverable + testing

**Deliverable:**
- `scripts/bench_survivorship_columnar.py` (the bench).
- `.github/workflows/bench-survivorship-columnar.yml` (`workflow_dispatch`,
  `large-new-64GB`).
- A committed verdict report (`docs/superpowers/reports/2026-06-XX-
  survivorship-columnar-verdict.md`) with the wall/RSS/per-phase table at
  1M + 5M, the recoverable computation, and the go/no-go. If GO, it names the
  follow-on Phase-2 rewrite spec; if NO-GO, it records "keep the slow path"
  with the evidence so this isn't re-litigated.

**No production code changes.** The slow path, `resolve_cluster`, and the
config schema are untouched. So there is no parity gate to write -- the
"parity" is trivially that production behavior is unchanged (the bench is a
read-only measurement harness).

**Testing:**
- A 1k-row smoke test (`tests/.../test_bench_survivorship_columnar_smoke.py`
  or a tiny CI step) that imports the bench, runs it at `rows=1000`, and
  asserts it completes and emits the expected table keys (slow wall, floor
  wall, per-phase split). This guards the harness from bit-rot; it is NOT a
  perf assertion (perf numbers are environment-specific and live in the
  report). The smoke must tolerate a `None` RSS (the model bench's
  `_peak_rss_mb()` uses `resource.getrusage`, Unix-only; on a non-Unix CI
  runner RSS may be `None` -- the smoke asserts the table keys, never an RSS
  value).
- **Plan-time check:** confirm `nanp` is a registered `validate_with`
  validator name reachable by `goldenflow_filter` before using it in the
  bench config; an unknown name silently no-ops the candidate drop (fail-open
  by design), which would understate the conditional path's work. If `nanp`
  is not wired in the bench's env, use a validator that is, or drop
  `validate:` from the bench config and note it.
- The bench's own correctness is bounded: it asserts the slow path and the
  floor path produce the same ROW COUNT (one golden record per cluster) so a
  silently-broken workload can't produce a misleading tax.

## 6. Open questions for spec review

- **Per-phase attribution mechanism:** env-gated bench-only timers vs a
  sampling profiler captured in the report. Leaning bench-only coarse timers
  for the phase split (deterministic, in the table) + an optional `py-spy`
  appendix for the intra-`resolve_cluster` detail. Confirm no production
  instrumentation is added.
- **Floor fidelity:** the non-survivorship fast path does strictly LESS work
  (no groups/conditionals), so the floor is a loose lower bound. Is "floor +
  per-phase recoverable" a defensible go/no-go basis, or does the verdict
  need a thin vectorized `group_winner` micro-prototype to tighten the
  recoverable estimate? Leaning no-prototype (measure-first); the per-phase
  vectorizable-wall is the primary signal and the floor is the bound.
- **Scale ceiling:** 1M + 5M measured, 50M extrapolated. Is a measured 50M
  run worth the runner cost, or is the extrapolation sufficient for a go/no-go?
  Leaning extrapolate (the cost is linear in cluster count; 5M is enough to
  establish the per-cluster constant).
