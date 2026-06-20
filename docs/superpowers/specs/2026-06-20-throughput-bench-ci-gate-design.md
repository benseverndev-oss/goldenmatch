# Throughput benchmark + CI perf gate (#1086) — design

**Issue:** #1086 (part of epic #1080, Training-Data Dedup at Scale)
**Date:** 2026-06-20
**Status:** design

## Problem

The sketch-then-verify throughput tier (#1083, `dedupe_df(df, throughput=…)`) makes
a throughput claim — corpus/document near-dup dedup at high recall and low cost via
LSH/MinHash blocking + a cheap sketch-distance verify. That claim is currently
**undefended**: there is no measured throughput number against the field, and nothing
stops a future change from silently regressing the tier's cost.

This issue delivers two artifacts:

1. **A published throughput number** — docs/sec and MB/sec, head-to-head against a real
   external baseline (datatrove) on a public corpus slice, on one machine.
2. **A CI perf gate** — fails a PR when the tier's cost regresses, without flaking on
   shared-runner wall-clock noise.

## What already exists (dependencies)

- **Throughput tier (#1083, PR #1129).** `dedupe_df(df, *, throughput=<float|True|ThroughputConfig>)`.
  `ThroughputConfig(enabled, recall_target=0.95, similarity_threshold)` in
  `config/schemas.py`. Forces `lsh` (MinHash/Jaccard) or `simhash` (cosine) blocking on
  the longest text column, then confirms candidate pairs by sketch distance
  (`core/throughput_verify.py::score_sketch_pairs`). Plan overlay
  `verify_mode="sketch_distance"`; honest posture (`ThroughputPosture` —
  LSH-theoretic `expected_recall` + measured `reduction_ratio`, **not** a measured F1)
  on `DedupeResult.throughput_posture` and the controller telemetry `throughput` block.
  Raises `ThroughputNotApplicableError` when there is no text column.
  **NOT on main yet** — PR #1129 is open, auto-merge armed. See *Sequencing* below.
- **`bench_er_headtohead/` harness.** The proven structural template: streaming
  bounded-memory fixture generator, one loud-failing runner per engine, engine-agnostic
  DuckDB evaluator, subprocess-per-datapoint OOM-tolerant orchestrator, a
  `workflow_dispatch` 64 GB CI lane that installs a real competitor (Splink). This design
  mirrors those patterns for documents — it does **not** extend that harness (record-ER
  vs whole-doc-near-dup are different shapes; mixing them muddies both).
- **CI path-filter convention** (`ci.yml`, `dorny/paths-filter`). New jobs add a filter
  entry in the `changes` job + an `if:` gate. Doc-only PRs skip everything.

## Non-goals

- No GPU baseline run. NeMo-Curator is GPU/RAPIDS, not CI-runnable — its published
  throughput is **cited** as a reference row with a "GPU, not run here" caveat, not measured.
- No distributed/billion-scale bench (that is #1084) — single-node only.
- No new product surface (that is #1085) — this benchmarks the existing tier.
- The per-PR gate does **not** measure wall-clock throughput (too noisy on shared runners);
  it gates on deterministic algorithmic cost. Wall-clock is the dispatch headline's job.

## Architecture

A dedicated `scripts/bench_corpus_dedup/` harness plus a separate fast deterministic gate.
Six units, each independently testable:

```
scripts/bench_corpus_dedup/
  corpora.py            # pluggable corpus adapters -> streaming (doc_id, text)
  inject_dups.py        # seeded ground-truth near-dup injector + truth file
  run_goldenmatch.py    # one datapoint, throughput tier, fails loud if tier not engaged
  run_datatrove.py      # one datapoint, datatrove MinHash dedup pipeline
  evaluate.py           # engine-agnostic recall: pairwise + cluster P/R/F1 (DuckDB)
  orchestrate.py        # subprocess-per-(corpus,scale,engine), OOM-tolerant, summary.md
  throughput_perf_gate.py  # deterministic per-PR cost gate vs committed baseline
  perf_gate_baseline.json  # committed baseline (snapshot-test style)
  data/offline_corpus.jsonl  # vendored public-domain slice (gate fixture, network-free)
  README.md
.github/workflows/bench-corpus-dedup.yml   # dispatch headline bench (64 GB) [new]
.github/workflows/ci.yml                    # + throughput-gate job (ubuntu-latest) [modified]
```

### 1. Corpus adapters — `corpora.py`

`load_corpus(name, n_docs, seed) -> Iterator[tuple[str, str]]`, streaming, bounded memory.

| name | source | use |
|---|---|---|
| `fineweb` | HF `datasets` streaming (ODC-By). Built *by* datatrove → most on-narrative. | default headline |
| `c4` | HF `allenai/c4` `en` streaming. | alternate headline |
| `wikipedia` | HF `wikimedia/wikipedia` streaming (CC-BY-SA). | alternate headline |
| `offline` | vendored `data/offline_corpus.jsonl` (public-domain, a few MB). No network. | **per-PR gate fixture** |

The HF adapters are fetched in-job on the 64 GB runner (network there is acceptable, as
with other bench-* workflows). The `offline` adapter is the only one the per-PR gate
touches, so the gate never depends on network/HF availability.

### 2. Ground-truth dup injector — `inject_dups.py`

Real corpora ship no near-dup labels, so we inject them (same idea as the head-to-head's
typo-injection, lifted to documents). Given base docs and a seed, deterministically layer
a controlled fraction of near-dups, each tagged to its source doc:

- **exact** — byte-identical copy.
- **partial-overlap** — truncation / paragraph drop / insertion.
- **light-paraphrase** — casing, whitespace, token swaps below the near-dup threshold.

Emits the corpus (parquet) + a truth file (`doc_id -> cluster_id`). The injected fraction,
per-mode mix, and corruption strength are CLI knobs with fixed defaults. Determinism is
the contract — a fixed `(corpus, n_docs, seed, inject-params)` yields byte-identical output,
which is what makes the gate reproducible.

### 3. Engine runners (loud-fail, one datapoint each)

- **`run_goldenmatch.py`** — `dedupe_df(df, throughput=recall_target)`. **Asserts the tier
  actually engaged**: resolved plan `verify_mode == "sketch_distance"`, blocking strategy in
  `{lsh, simhash}`, native loaded (`GOLDENMATCH_NATIVE=1`, `native_enabled(...)`) — a slow or
  full-verify path can never masquerade as "the throughput tier". Reports the common schema
  (below) plus `throughput_posture` and `reduction_ratio`.
- **`run_datatrove.py`** — datatrove's MinHash dedup pipeline (signature → buckets →
  clustering) on the identical corpus parquet. Reports the same schema. datatrove is
  `uv pip install`'d in the workflow (not a repo dependency), exactly like Splink.

**Common per-datapoint schema:** `wall_s`, `rss_mb_peak`, `n_docs`, `bytes_in`, `docs_per_sec`,
`mb_per_sec`, `candidate_pairs`, `clusters`, `status` (`ok|OOM|error`), engine-specific extras.

NeMo-Curator is not run. `summary.md` carries a clearly-labeled reference row citing its
published GPU throughput, with the corpus/hardware it was measured on, so the comparison is
honest about what is measured vs cited.

### 4. Engine-agnostic evaluator — `evaluate.py`

Given an engine's emitted clusters/pairs + the truth file, compute **pairwise** P/R/F1 and
**cluster-level** P/R/F1 — identical code for both engines, memory-bounded via a DuckDB
contingency table (no pair materialization), reused conceptually from the head-to-head's
`evaluate.py`. Recall is what makes the throughput number honest: docs/sec at *what recall*.

### 5. Orchestrator — `orchestrate.py`

Subprocess-per-`(corpus, scale, engine)` datapoint; the OS reclaims each datapoint's memory
on exit; an OOM-killed datapoint is recorded `status: OOM` and the sweep continues; the
aggregate JSON is flushed after every datapoint. Produces `summary.md` (the headline
**docs/sec + MB/sec vs datatrove** table, recall alongside, NeMo cited) + `bench_results.json`.
Driven by `bench-corpus-dedup.yml` (`workflow_dispatch`, inputs: `corpus`, `scales`,
`engines`, `runner` default `large-new-64GB`).

### 6. Deterministic per-PR gate — `throughput_perf_gate.py`

The new artifact. Runs the throughput tier on the `offline` corpus at **fixed size + fixed
seed + fixed config**, and extracts **machine-independent** cost metrics — deterministic
functions of the input, not of runner speed:

- `candidate_pairs` — pairs the sketch blocking emits to verify (the dominant cost driver).
- `reduction_ratio` — from the blocking profile.
- `measured_recall` — pairwise recall on the injected ground-truth dups, computed by
  `evaluate.py` against the truth file. **This is not** `throughput_posture.expected_recall`:
  the posture is the tier's analytic LSH-theoretic recall, whereas the gate's `measured_recall`
  is the empirical pairwise recall against known injected dups. Different quantities, different
  code paths — the gate wires `evaluate.py`, not the posture.
- (optional) `sketch_ops` — MinHash/sketch computations.

`candidate_pairs` / `reduction_ratio` are intended to come from `DedupeResult` / the
blocking profile / the controller telemetry `throughput` block. **On the #1083 branch these
are not confirmed to be exposed as machine-readable counters** (`throughput_posture` surfaces
as a `dict`, and the telemetry `throughput` block was not positively confirmed), so the plan
should budget for the instrumentation path: the runner adds a thin, output-invariant,
env-gated hook (like `GOLDENMATCH_BUCKET_DEBUG`) that emits the counters — rather than assume
they are already readable.

Pass/fail vs a committed `perf_gate_baseline.json` with tolerance (defaults; the plan may tune):

- `candidate_pairs` ≤ baseline × (1 + 0.15) → a blocking change that blows up the pair count fails.
- `measured_recall` ≥ baseline − ε, ε = 0.01 → a change that quietly drops recall fails.
- `reduction_ratio` ≥ baseline − ε, ε = 0.01.
- A **generous** wall ceiling (e.g. 5× expected) as a coarse backstop only — never the primary signal.

`--update-baseline` regenerates the JSON; intentional changes commit a new baseline, exactly
like a snapshot test. Wired into `ci.yml` as a new path-filtered `throughput-gate` job
(filter area covers the throughput tier code + this harness), `ubuntu-latest`, runs in seconds.

## Data flow

```
corpora.load_corpus ─▶ inject_dups ─▶ corpus.parquet + truth.parquet
                                          │
              ┌───────────────────────────┴───────────────────────────┐
              ▼ (dispatch headline)                                    ▼ (per-PR gate)
   orchestrate ▶ run_{goldenmatch,datatrove} ▶ evaluate         throughput_perf_gate
              ▼                                                         ▼
       summary.md + bench_results.json                    compare vs perf_gate_baseline.json
       (docs/sec + MB/sec vs datatrove, NeMo cited)       → exit 0/1
```

## Error handling

- **OOM** — subprocess isolation; OOM-killed datapoint → `status: OOM`, sweep continues
  (a ceiling is itself a result, per the head-to-head).
- **`ThroughputNotApplicableError`** — guaranteed not to fire: every corpus adapter yields a
  text column. If it ever does, the runner records `status: error` with the message, not a crash.
- **datatrove unavailable locally** — the datatrove runner + its tests skip-if-not-installed
  (CI installs it); the gate never touches datatrove.
- **HF fetch failure** — only affects the dispatch headline on streamed corpora; the
  datapoint records `status: error`. The gate (offline corpus) is unaffected.
- **Tier-not-engaged** — `run_goldenmatch.py` asserts loud and exits non-zero rather than
  reporting a misleading number.

## Testing

- `corpora.py` — `offline` adapter is deterministic (same seed → same docs); HF adapters
  smoke-tested behind skip-if-offline.
- `inject_dups.py` — ground-truth correctness (every injected dup is in its source's truth
  class; determinism for fixed seed).
- `evaluate.py` — known toy corpus with hand-computed P/R/F1.
- `throughput_perf_gate.py` — tolerance logic (pass at baseline, fail just over the band),
  the `--update-baseline` round-trip, recall-floor enforcement.
- End-to-end smoke — tiny `offline` run through orchestrate → summary on every CI run that
  touches the harness.
- `run_datatrove.py` / datatrove install — guarded skip locally, exercised in the dispatch lane.

## Sequencing

This consumes the `throughput=` entry point from #1083 (PR #1129 — open, BLOCKED,
auto-merge armed; **not on main**). Plan: develop in a worktree branched off main; if #1129
has not merged by implementation time, stack/rebase onto `feat/1083-throughput-plan`.
**Do not merge #1086 until #1129 lands.**

## Published number (rollout)

At the end, sweep the headline docs/sec + MB/sec (vs datatrove, NeMo cited, at the measured
recall) into: README throughput table, context-network ADR, docs-site throughput/tuning page,
CHANGELOG — via the rollout-docs-sweep skill.

## Done bar (from the issue)

- [x] A published throughput number — docs/sec + MB/sec vs datatrove on a public corpus slice.
- [x] A gate that fails on regression — the deterministic per-PR `throughput-gate` job.
