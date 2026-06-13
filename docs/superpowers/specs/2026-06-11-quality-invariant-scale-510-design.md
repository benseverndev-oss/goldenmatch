# Quality-Invariant Scale Validation (#510) — Design

**Date:** 2026-06-11 · **Issue:** #510 · **Status:** approved (brainstorm), pre-plan

## Thesis & goal

#510's thesis: *match quality and clustering behaviour are invariant as scale
grows.* Existing scale docs (`docs/scale-envelope.md`, `scale-audit-2026-05.md`)
are **throughput** claims (wall/RSS), not **quality** claims. This work fills the
quality side: a 1K→200M ladder measuring F1 vs a ground-truth oracle, plus
backend parity and determinism, published as `docs/quality-invariant-scale.md`.

**Pass targets (from the issue):** pairwise F1 Δ ≤ 0.005, B-cubed F1 Δ ≤ 0.005,
cluster F1 Δ ≤ 0.01 — each vs the 1K/10K oracle rung; golden-record equivalence
+ deterministic clustering pass; native vs pure-Python produce equal clusters.

## What already exists (the leverage — DO NOT rebuild)

`scripts/quality_invariant_scale.py` is ~70% of the harness:
- `generate_with_gt(n_rows, seed, shape)` — synthetic person dataset + `__cid__`
  ground truth. `shape="realistic"` (hash-syllable names + address/city/zip/
  birth_year, the *fair* fixture; F1 has measured ~0.9886 at 1M–10M) and
  `shape="phase5"` (throughput-shaped replica).
- `score_quality(predicted_members, gt_cids)` — **pairwise, B-cubed, AND cluster
  F1**, O(N) streaming (never materializes the GT pair set; ~3 GB at 200M). Its
  numbers match a set-based reference on the 1K/10K/100K rungs.
- `run_rung(n_rows, seed, shape, backend)` — generate → `dedupe_df` (zero-config
  or `--backend`) → `score_quality` → JSON with wall, peak RSS, predicted/multi
  cluster counts, committed-config telemetry, **native witness**, per-stage RSS.
- `scripts/railway_qis_job.py` + `Dockerfile.qis` — Railway one-shot runner
  (env `QIS_ROWS/SHAPE/SEED/BACKEND`); output lands in deploy logs, "appended to
  the published table by hand."
- `.github/workflows/bench-ray-cluster.yml` — `ray up`/`ray down` GCE cluster
  that `ray submit`s this harness (`--shape phase5`), with guaranteed teardown.
- `.github/workflows/bench-quality-invariant-scale.yml` — `workflow_dispatch`
  single-box lane (10M–50M, `large-new-64GB`) that already runs this harness and
  uploads the per-rung JSON as an artifact; the `--corruption` flag plumbs
  straight through it, so the mid-ladder rungs need no new workflow.

`#864` already merged the vectorized block-shuffle scoring; the distributed
recall-complete path is viable at 100M.

## What's missing (the work)

### 1. Corruption knob (`generate_with_gt`, realistic shape)
The realistic shape's only noise is one `a→@` swap on `first_name` → F1 ~0.9886,
too high to expose drift. Add configurable corruption (one new
`--corruption {light,moderate,hard}` CLI flag → a small dataclass of rates):
per-field, per-row apply some of: char transposition, char deletion,
token/word drop, occasional whole-field null, on `first_name`/`last_name`/
`address`/`email`. Target `moderate` ≈ **F1 0.90–0.95** — a drift-sensitive but
still-high-recall regime. Constraints:
- Deterministic given `(seed, n_rows, corruption)` — same statistical shape at
  every N (so F1 differences across rungs are scale effects, not data effects).
- Keep the `__cid__` oracle intact (corruption never moves a row's cluster).
- A row must stay recall-able: corruption is per-field, and the matching config
  is multi-pass (name + zip + email), so a row corrupted in one field still
  co-locates via another. (Validated by the 1K oracle landing in-range.)
- Default unchanged (`light` ≈ today's behaviour) so existing harness callers
  and the phase5 shape are untouched.

### 2. Ladder execution
Per-rung JSON via the harness (no harness change beyond the knob):
- **Single-box** (dev box for 1K–1M; the `bench-quality-invariant-scale.yml`
  `large-new-64GB` lane for 10M/25M — NOT the dev box, which OOMs under heavy
  local runs per `feedback_avoid_full_suite_oom`): 1K, 10K, 1M, 10M, 25M. 10M/25M
  use `--backend duckdb` (out-of-core, no OOM) or bucket on the 64 GB runner.
- **Cluster** (GCP, the #844 recipe; distributed recall-complete path): 50M,
  100M, 200M. `--backend ray` / `GOLDENMATCH_DISTRIBUTED_PIPELINE=2` +
  `GOLDENMATCH_DISTRIBUTED_BLOCK_SHUFFLE=1` +
  `GOLDENMATCH_DISTRIBUTED_WCC_SCRATCH=gs://…`. **Feasibility flag:** the
  realistic shape + real fuzzy scoring is HEAVIER than #844's exact-`last_name`
  cliques, so these rungs will run longer than 9.2 min — measure, don't assume;
  size the cluster (and possibly the 200M rung) from the 50M result.

### 3. Oracle-delta aggregator + verdict (new, small)
`scripts/qis_aggregate.py` (new): read the per-rung JSONs (a directory),
compute each rung's deltas vs the 1K/10K oracle for pairwise/B-cubed/cluster F1,
flag PASS/FAIL against the targets, and emit (a) a Markdown table and (b) a
one-line verdict. Pure-Python, no Ray; unit-tested on synthetic JSON. (Field
locations to read from the per-rung JSON: F1 metrics at the top level; the
candidate-pair count is nested at `bench.scored_pair_count`, not top-level —
the aggregator reads it from there for the report's pair-count column.)

### 4. Backend parity + determinism + golden-record equivalence
All three are #510 required outputs; all assert on real surfaces (`result.clusters`,
`result.golden`).
- **Backend parity:** at 1K/10K/1M, run `GOLDENMATCH_NATIVE=1` vs
  `GOLDENMATCH_NATIVE=0` and assert **identical predicted clusters, equal F1, AND
  byte-identical `result.golden`** (sorted, schema-stable compare). Test
  `test_qis_native_parity` via `run_rung` at 1K in the `python` lane
  (skip-if-native-unavailable, but assert the harness native witness either way).
  Parity is scale-independent, so 1K/10K/1M suffices.
- **Determinism:** re-run a rung (1K) twice with the same seed and assert
  **identical predicted clusters AND byte-identical `result.golden`**. Test
  `test_qis_deterministic`. (#510's "deterministic clustering check" +
  "golden-record equivalence" land here together.)
- **Harness return-shape change (load-bearing for the planner):** `run_rung`
  today returns a flat JSON-able dict and does NOT expose the `DedupeResult`
  (`result` is a function-local). The parity/determinism tests need
  `result.clusters` AND `result.golden`, so the work either (a) adds a
  `capture_result=True` / `return_result` path to `run_rung` that surfaces the
  `DedupeResult` (clusters + golden, sorted by a stable key) to the caller, or
  (b) has those two tests call `dedupe_df` directly and run `score_quality`
  themselves. Pick (a) — it keeps the auto-config + native-witness plumbing in
  one place. This is the "small harness addition," not a new pipeline; the plan
  must name it explicitly so it doesn't assume `result` is already reachable.

### 5. Published report `docs/quality-invariant-scale.md`
The deliverable. Sections: methodology (dataset shape, corruption, the oracle,
the per-rung-auto-config decision, metric definitions), the rung×metrics table
(rows = rungs; cols = pairwise/B-cubed/cluster F1 + Δ-vs-oracle + wall + peak RSS
+ candidate-pair count + predicted/multi cluster counts + auto-config timing +
native-parity), the PASS/FAIL verdict, and reproduction commands. Link it from
`docs/scale-envelope.md` and both READMEs (the scale-envelope section).

## Methodology decisions

- **Per-rung auto-config** (the harness default), not a frozen config: it is the
  real zero-config story #510 is about, and `committed_config` is captured per
  rung so any F1 drift is attributable to config drift vs engine drift. If
  auto-config picks materially different configs across rungs, the report says so.
- **Oracle = the 1K rung** (10K as a cross-check); deltas are measured against it.
  The corruption-knob "1K F1 ~0.90–0.95" gate is a one-time TUNING check on the
  oracle (pick a `moderate` level that lands the oracle in-band), NOT a per-rung
  pass/fail — the determinism guarantee (same `(seed, n_rows, corruption)` shape
  at every N) is what makes the higher rungs' F1 comparable to the oracle.
- **Shape = realistic** for the published ladder (phase5 stays available for
  throughput-only comparisons).
- **In-house embedder: DEFERRED from this ladder, explicitly.** #510's context
  mentions "with native kernels and the in-house embedder enabled." Native is
  in-scope (§4). The embedder is NOT, because: (a) the scale-INVARIANCE thesis is
  tested directly by the lexical/fuzzy config — whether F1 holds across N is the
  same question with or without embeddings; (b) the embedder adds a trained-model
  + ONNX-runtime-on-every-cluster-node dependency (ONNX doesn't link on the
  Windows dev box and isn't in the CI venv) that is orthogonal to scale and would
  dominate the effort. Recorded as a follow-up: an embedder-enabled ladder rerun
  once the lexical ladder is published. The report's methodology states this
  scoping explicitly.

## File structure
- **Modify** `scripts/quality_invariant_scale.py` — corruption knob on
  `generate_with_gt` / `_generate_realistic` + the `--corruption` CLI flag.
- **Create** `scripts/qis_aggregate.py` — oracle-delta table + verdict.
- **Create** `packages/python/goldenmatch/tests/test_qis_harness.py` — corruption
  determinism + F1-in-range on the 1K oracle + native parity + run-determinism +
  aggregator deltas. (No-Ray; runs in the `python` lane.)
- **Create** `docs/quality-invariant-scale.md` — the report.
- **Modify** `docs/scale-envelope.md` + `README.md` + package `README.md` — link
  the report.
- **Possibly modify** `scripts/railway_qis_job.py` — pass `--corruption` through
  if the large rungs run via Railway rather than the GCP cluster.

## Risks
- **Cluster-rung cost/feasibility** (50M/100M/200M heavier than #844). Mitigate:
  run 50M first, extrapolate, size the cluster; the 200M rung is the issue's
  "stretch" — drop it loudly in the report if it doesn't fit a sane window.
- **Corruption too strong** → F1 collapses / blocking misses → not a clean
  invariance signal. Mitigate: tune `moderate` against the 1K oracle locally
  before any cluster run; the 1K F1 must land ~0.90–0.95.
- **Auto-config instability at scale** could masquerade as quality drift. The
  committed-config capture surfaces it; report it rather than hide it.
- **Local 25M** may not fit a dev box → use duckdb backend or a large runner.

## Done criteria (maps to #510's checklist)
- [ ] Corruption knob lands the 1K oracle F1 ~0.90–0.95; deterministic.
- [ ] All rungs run; per-rung JSON captured (1K–25M local, 50M/100M [+200M if
      feasible] cluster).
- [ ] Pairwise/B-cubed F1 Δ ≤ 0.005, cluster F1 Δ ≤ 0.01 vs oracle (or the report
      explains any rung that misses, with root cause).
- [ ] Backend parity (native==pure-Python) + determinism tests green.
- [ ] `docs/quality-invariant-scale.md` published with the table + methodology +
      verdict; linked from scale-envelope + READMEs.
