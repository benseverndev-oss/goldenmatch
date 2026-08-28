# Ray production readiness — design

**Status:** proposed
**Date:** 2026-08-28
**Assessed at:** `main @ 6bc8ff99e`
**Supersedes:** the status section of `docs/distributed-ray-roadmap.md` (stamped 2026-05-19, stale)

---

## Problem

The Ray distributed lane is not production ready, but the reasons recorded in
the repo are wrong in both directions: the roadmap overstates the remaining
architecture work by months, while several in-repo comments understate what is
already gated and already measured.

Every claim below was verified against `main @ 6bc8ff99e` or against a CI run
log. Where a claim in the repo turned out to be stale, that is called out — the
staleness is itself one of the gaps.

### What is already true (verified, not assumed)

| Capability | Evidence |
|---|---|
| Clustering correctness is gated | `distributed` + `distributed_wcc`, both blocking, both in `ci-required` |
| Ray-free distributed tests are gated | They run in `python_goldenmatch`, which is in `ci-required` and is not `continue-on-error` |
| `test_distributed_fail_loud.py` can fail a merge | Imports no ray (`polars` + `pytest` only), so it runs in the sharded `python_goldenmatch` job, not only in the advisory ray lane |
| Bench provenance is captured | `.ray/cluster-gce.yaml` pins polars/pandas/pyarrow/numpy/goldenmatch-native exactly and installs goldenmatch by git SHA; the workflow captures per-instance `cpuPlatform` and rsyncs `pip freeze` from the head into the artifact |
| `distributed_broad` is green | 177 passed / 4 skipped on run `33176961803`; 167 passed on `33170819859`. The job comment claiming "known-fragile fixtures" is stale |
| 100M runs are byte-identical across four optimisation generations | pairwise F1 `0.9265507059539793` at 6736s, 3237s, 2906s and 3284s |

Three of those rows contradict claims made in an earlier draft of this
assessment. They are listed first deliberately: the failure mode was trusting an
in-repo comment about the code instead of checking the code, which is the same
failure mode this document exists to fix.

---

## Gaps

Ordered by whether they block the word "production", not by effort.

### G1 — `distributed_broad`'s exclusions do not exclude

`.github/workflows/ci.yml:3100` runs:

```
.venv/bin/python -m pytest packages/python/goldenmatch/tests/test_distributed_*.py \
  --ignore=packages/python/goldenmatch/tests/test_distributed_clustering.py \
  --ignore=packages/python/goldenmatch/tests/test_distributed_randomized_contraction_wcc.py
```

The shell expands the glob to explicit file paths. `--ignore` filters directory
traversal during collection; it does not filter paths named explicitly on the
command line. Both flags are no-ops.

Measured on run `33176961803`, job `98868039914`:

| file | tests collected | should be in this job |
|---|---:|---|
| `test_distributed_randomized_contraction_wcc.py` | 55 | no — `distributed_wcc` gates it |
| `test_distributed_clustering.py` | 51 | no — `distributed` gates it |
| everything else | 75 | yes |

So 106 of 181 tests (59%) are a second run of the two blocking gates. The job
takes 669–929s, and roughly 430s of the top-20 durations belong to the two files
it believes it excluded.

This is why G2 looks expensive. It is not.

### G2 — the ray-executing pipeline cannot fail a merge

Thirteen files require ray and are gated nowhere:

```
test_distributed_clustering_e2e.py   test_distributed_pipeline.py
test_distributed_controller.py       test_distributed_pipeline_branch.py
test_distributed_dataset.py          test_distributed_sample.py
test_distributed_fs_e2e.py           test_distributed_scoring.py
test_distributed_golden.py           test_distributed_scoring_tuning.py
test_distributed_indicators.py       test_distributed_utils.py
test_distributed_phase5_e2e.py
```

They skip in `python_goldenmatch` (no ray installed there, `pytest.importorskip`)
and run only in `distributed_broad`, which is `continue-on-error` at job level and
deliberately absent from `ci-required`. So the distributed *execution* path —
controller, scoring, pipeline, golden, end-to-end — is unprotected. #2797 is the
demonstration of what that costs: five worker-side handlers swallowed exceptions
and under-matched silently, and no gate could have caught it.

The job's own comment gives fragile fixtures as the reason it stays advisory.
That reason no longer holds — see the evidence table above.

### G3 — the default path does not distribute

`distributed/pipeline.py:35`: with `GOLDENMATCH_DISTRIBUTED_PIPELINE` unset,
`run_dedupe_pipeline_distributed` falls through to `_run_phase2_cheat_line`,
which materialises the input via `take_all`. The engine every benchmark measures
requires `PIPELINE=2`. A user who sets `backend="ray"` and nothing else gets a
driver-bound path wearing the distributed name.

### G4 — the planner will not select the lane

`core/autoconfig_planner_rules.py:335` `_ray_auto_select_enabled()` is the
soft-revert from the 2026-05-18 Distributed Plan v1 kill-criterion failure. The
v3 planner cannot pick ray unless `GOLDENMATCH_ENABLE_DISTRIBUTED_RAY=1`.

Keeping it shut is currently *correct*. It is listed as a gap because lifting it
is the definition of done, and because nothing currently states what would
justify lifting it (see G10).

### G5 — correlated survivorship is refused

`distributed/pipeline.py:167` fails fast on `field_groups`, conditional and
validate golden rules under `PIPELINE=2`, because the streaming pipeline cannot
replicate the driver's staged per-cluster pass and would otherwise emit plain
`most_complete` records silently.

The refusal is right. The gap is that it is undocumented in
`docs/scale-envelope.md`, so the limitation is discoverable only by hitting it.

### G6 — the driver is still the ceiling, and uncharacterised

Distributed clustering removed 81.2 GB of driver peak, leaving 50.9 GB at 100M
against a 256 GB head. What that 50.9 GB consists of has never been measured.
The clustering stage got a `GOLDENMATCH_CLUSTER_DEBUG` split and it immediately
overturned an estimate that was wrong by 4.5x; the non-clustering baseline has
had no equivalent.

Any plan to reduce it today would be a guess, which is the move that produced
three of the corrections already on record.

### G7 — #957: the cluster runs at roughly a quarter of itself

Issue #957 records `MapBatches(_score)` holding 3 tasks / 6 CPU and the shuffle
holding 16, against `Active & requested resources: ~19/80 CPU`, on the same 100M
shape the benchmarks use.

The lane's strategic case is built on being 3.0x slower than Spark (958s against
2906–3284s). The idle-capacity ratio is 4.2x. Those are the same number, which
makes the Spark gap a plausible artefact of scheduling rather than of engine
quality — testable, and untested.

The issue is filed **P2**. One of its three asks (project to scoring columns
before the shuffle) appears to have landed since — `SCORE_PROJECT` now defaults
to `1` — but the issue was never re-measured or closed, so present-day
utilisation is unknown rather than confirmed.

**Diagnosis constraint:** `ray status` reports *reserved* CPUs. Section 10 of
`docs/distributed/ray-optimal-setup.md` says it "can disagree with reality for
an hour". Any measurement here reads host CPU, not `ray status`.

### G8 — the repo's own record contradicts itself

Three instances, all live:

1. `docs/distributed-ray-roadmap.md` is stamped 2026-05-19, describes distributed
   clustering as a future 6–8 week phase, and puts the lane six months out. It is
   the first document a newcomer finds.
2. `docs/distributed/ray-optimal-setup.md` §6 calls runs `32992647463`,
   `33006980567` and `33074452121` "four runs of the same shape" and takes a
   3237s median. §8 of the same file attributes the 3237s→2906s step to the
   `+ single projected pass` code change — merged as #2781 on 2026-08-26. Both
   cannot be true.
3. The `distributed_broad` job comment describes fixtures that have since been
   stabilised.

`clustering.py` is correct where the docs are not: it compares only
`33074452121` against `33087786765`, same code, differing only in route, and
reports +12.2%. The shipped routing constant is sound; the prose around it is
not.

### G9 — provenance is captured but soft

The chain exists. What is missing is that nothing enforces or checks it:

- `if-no-files-found: warn` on the artifact upload — a run whose result JSON
  never came back is still green with no result.
- the pip-freeze rsync ends `|| echo "could not fetch pip freeze from the head
  (non-fatal)"` — the environment record is optional.
- nothing asserts that two runs being compared were built from the same SHA,
  which is exactly the check that would have caught G8.2.

### G10 — the kill criterion is the wrong shape, and there is no scale gate

The standing criterion is *100M dedupe under 30 minutes*
(`docs/distributed-ray-cluster-setup.md:183`). Both routes miss it by roughly
2x — 54 and 61 minutes. More importantly it measures wall on an engine chosen
for capacity: `bucket` is validated single-box through 200M and Spark is faster
through the whole overlap, so wall is the axis Ray will never win.

Separately, the only end-to-end accuracy evidence at 100M comes from manual,
paid `workflow_dispatch` runs. Nothing automated would catch an accuracy
regression at scale.

---

## Decisions

**D1 — Ray is capacity insurance, not a Spark competitor.**
`bucket` covers to 200M single-box; Spark is faster through the overlap and
`spark_connect` is already in `ci-required`. Ray's exclusive window opens where
`bucket` runs out of box. Scope, messaging and the kill criterion all follow from
this, and G10's replacement criterion encodes it.

**D2 — Settle G7 before funding anything architectural.**
If scoring saturates the cluster, the 3.0x Spark gap may substantially close, and
D1's framing — along with the value of G6 — changes. No design work downstream of
"Ray is 3x off" is trustworthy until this is measured.

**D3 — Gate coverage before behaviour changes.**
G1 and G2 land before G3. Promoting `PIPELINE=2` to default changes what every
ray user executes; doing that while the execution path cannot fail a merge
inverts the safety ordering.

**D4 — Do not reduce the driver baseline before measuring it.**
G6 gets an instrumentation pass first and a target second.

**D5 — The soft-revert gate stays shut until the new criterion passes.**
G4 is the last item, not the first. An opt-in lane that is honest about being
opt-in is a supportable product; an auto-selected lane that misses its criterion
is not.

---

## Gates

Each is a binary a reviewer can check.

| # | Gate |
|---|---|
| 1 | `distributed_broad` collects zero tests from the two files it excludes |
| 2 | Every `tests/test_distributed_*.py` file is assigned to exactly one distributed CI job, enforced by a check that fails when a new file is added to none |
| 3 | The ray-executing pipeline, controller, golden and e2e suites can fail a merge |
| 4 | A bench run that produces no result JSON, or no pip freeze, fails |
| 5 | `docs/distributed-ray-roadmap.md` and `ray-optimal-setup.md` §6 agree with `clustering.py` and with each other |
| 6 | Scoring holds >60 of 80 CPU at 100M, or the cap is characterised and named |
| 7 | A per-stage driver RSS split exists at 100M, and the 200M rung is measured rather than extrapolated |
| 8 | `backend="ray"` distributes with no environment variables set |
| 9 | `scale-envelope.md` states the correlated-survivorship limitation |
| 10 | A replacement kill criterion is written down, and `_ray_auto_select_enabled()` is lifted only when it passes |

---

## Work breakdown

Gates 1–5 and 8–9 are code and documentation, executable now, no cluster time and
no money. Gates 6–7 are measurement exercises whose design depends on their own
first result, so they are specified here as protocols rather than as
implementation plans.

| Plan | Gates | Status |
|---|---|---|
| `2026-08-28-ray-ci-gate-closure.md` | 1, 2, 3, 4, 5, 9 | written |
| `2026-08-28-ray-default-path.md` | 8 | to write, after G7 |
| Protocol P1 (below) | 6 | protocol only |
| Protocol P2 (below) | 7 | protocol only |
| Criterion revision | 10 | after P1 and P2 |

### Protocol P1 — re-measure cluster utilisation (Gate 6)

Not an implementation plan: the remedy depends on what the first measurement
says, and pre-specifying it would repeat the mistake this document catalogues.

1. Dispatch `bench-ray-cluster` at 100M, `us-east1-b`, current `main`, with the
   standard shape (1× `n2-highmem-32` + 3× `n2-standard-16`).
2. Sample **host** CPU on every node for the duration — `mpstat` or
   `/proc/stat` deltas via `ray exec`, at a fixed interval. Do not use
   `ray status`; it reports reservations.
3. Record, per pipeline stage: host CPU utilisation, in-flight `_score` task
   count, and object-store occupancy.
4. Compare against #957's `~19/80` and against the same run's `ray status`
   output, so the reservation-vs-reality gap is quantified rather than asserted.
5. If utilisation is still low, sweep `SCORE_NUM_CPUS`, `SCORE_CONCURRENCY` and
   `OP_RESERVATION` one at a time — one variable per run, per §3 of the
   operational guide.

**Exit:** scoring holds >60 of 80 CPU, or the binding constraint is named with
evidence. Either outcome updates #957 and its priority.

**Cost:** one paid run to measure; up to four more if a sweep is needed.

### Protocol P2 — characterise the driver baseline (Gate 7)

1. Add a per-stage driver RSS and wall split for the non-clustering stages,
   modelled on `GOLDENMATCH_CLUSTER_DEBUG`: output-invariant, off by default,
   near-free. Stages: load, standardize, blocking-key build, scoring submit and
   collect, golden build, write.
2. Run once at 100M with it on, and record the split into the bench artifact
   alongside the existing environment capture.
3. Run once at 200M to test the linear extrapolation. The claim that 50.9 GB
   scales linearly to ~102 GB at 200M is **an extrapolation, not a measurement** —
   and this lane has already been burned by scaling a per-unit cost across an
   unvalidated range.
4. Only then choose a target.

**Exit:** the 50.9 GB is attributed to named stages, and the 200M rung is a
measurement.

**Cost:** two paid runs.

---

## Out of scope

- Splink-Spark parity. D1 settles this: the Spark tier is the parity lane and is
  already gated.
- Distributed correlated survivorship implementation. G5's gate is a documented
  limitation; implementing it is a separate spec if a customer needs it.
- Retiring the `bucket` backend. It is the validated path to 200M and stays the
  recommendation there.

---

## Risks

| Risk | Handling |
|---|---|
| Promoting `distributed_broad` adds merge-queue wall | G1 removes 59% of its runtime first; measure the promoted job's wall before and after and record it in the PR |
| The ray suites are flaky in ways two green runs did not reveal | Require five consecutive greens on runs where the job actually ran (path filters skip it often) before promotion, and keep the revert to `continue-on-error` as a one-line rollback |
| P1 finds utilisation is already fixed | Good outcome — it retires the strategic argument against the lane. Close #957 with the measurement attached rather than silently |
| P1 finds the cap is architectural | D1 still holds; the lane remains capacity insurance and the criterion in G10 is still the right one |
| Doc reconciliation churns numbers again | Change prose only. `clustering.py`'s constant and its test are correct and must not move |
