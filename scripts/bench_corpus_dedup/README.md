# Corpus near-dup throughput bench (#1086)

Defends the #1083 **sketch-then-verify throughput tier** (`dedupe_df(df, throughput=…)`):
a head-to-head **docs/sec + MB/sec** measurement against [datatrove](https://github.com/huggingface/datatrove)
on a public corpus slice, plus a deterministic per-PR **perf gate** so the tier's cost can't
silently regress.

Built on the same patterns as `../bench_er_headtohead/` (subprocess-per-datapoint isolation,
one loud-failing runner per engine, an engine-agnostic evaluator) — purpose-built for whole-
document near-dup rather than person-record ER.

## What it measures

Per `(corpus, scale, engine)`:
- **Throughput**: dedupe wall, peak RSS, **docs/sec**, **MB/sec**, candidate pairs, clusters.
  GoldenMatch's is the throughput tier (LSH/MinHash blocking + sketch-distance verify);
  datatrove's is its MinHash dedup pipeline. Same corpus, same machine.
- **Recall** (engine-agnostic): pairwise precision/recall/F1 against the injected ground truth,
  via the **reused** `../bench_er_headtohead/evaluate.py` (a DuckDB contingency table —
  `record_id := doc_id`, identical contract). Throughput is only meaningful *at a stated recall*.

The headline is **docs/sec + MB/sec vs datatrove at the measured recall**.

## How it stays honest

- **Real text + injected ground truth.** Public corpora ship no near-dup labels, so
  `inject_dups.py` deterministically layers controlled near-dups (exact / partial-overlap /
  light-paraphrase) onto real text, each tagged to its source — that's what makes recall
  measurable. Both engines read the identical corpus parquet per scale.
- **Loud-fail.** `run_goldenmatch.py` refuses to report a number unless the throughput tier
  actually engaged (`throughput_posture` populated + `lsh`/`simhash` blocking) — a full-verify
  or fallback path can't masquerade as "the throughput tier".
- **Auditable comparability.** datatrove's MinHash LSH is configured (`num_buckets=10`,
  `hashes_per_bucket=10`) so its S-curve 50%-point `~(1/B)^(1/R) ≈ 0.79` sits near the tier's
  default Jaccard near-dup threshold (0.8) — both target the same near-dup similarity.
- **NeMo-Curator is cited, not run.** Its fuzzy dedup is GPU/RAPIDS (not CI-runnable); the
  summary carries a clearly-labelled reference row, never a measured number.
- **Subprocess isolation survives OOM.** `orchestrate.py` never loads a corpus; each datapoint
  is an isolated subprocess, an OOM-killed one is recorded `status: OOM`, and the aggregate is
  flushed after every datapoint.

## The per-PR perf gate

`throughput_perf_gate.py` runs the tier on the vendored `offline` corpus
(`data/offline_corpus.jsonl`, public-domain Gutenberg, network-free) at a **fixed size + seed +
config** and gates on **machine-independent cost** — so it never flakes on shared-runner
wall-clock noise:

- `candidate_pairs` — pairs the sketch blocking emits to verify (the dominant cost; from the
  throughput posture, *not* `bench_capture().scored_pair_count`, which is 0 on the sketch path).
- `reduction_ratio` — from the posture.
- `measured_recall` — pairwise recall on the injected dups (via the shared evaluator; this is
  the *empirical* recall, distinct from the posture's analytic `expected_recall`).

vs a committed `perf_gate_baseline.json` with tolerance (`candidate_pairs` ≤ baseline +15%;
recall / reduction_ratio ≥ baseline − 0.01). A blocking change that blows up the pair count or
quietly drops recall fails the gate. `--update-baseline` regenerates it (snapshot-test style);
intentional changes commit a new baseline. The first run with no committed baseline **seeds**
one and passes — commit the printed values to start enforcing.

Wired into `ci.yml` as the path-filtered `throughput-gate` job (`ubuntu-latest`, pure-Python,
seconds), part of the `ci-required` gate.

## Running it

Headline (dispatch — 64 GB recommended; installs datatrove + streams the corpus):

> GitHub → Actions → **bench-corpus-dedup** → Run workflow (pick `corpus`, `scales`, `engines`).

All test execution is via CI (the bench `pytest` + the gate run in the `throughput-gate` job).
The dispatch headline writes `summary.md` + `bench_results.json` (artifact `corpus-dedup-results`).

## Files

| File | Role |
|---|---|
| `corpora.py` | `load_corpus(name, n_docs, seed)` — `offline` (vendored) + `fineweb`/`c4`/`wikipedia` (HF streaming). |
| `data/offline_corpus.jsonl` | Vendored public-domain slice (gate fixture, network-free). |
| `inject_dups.py` | Seeded ground-truth near-dup injector → corpus + truth parquet. |
| `run_goldenmatch.py` | One datapoint, throughput tier, fails loud if the tier didn't engage. |
| `run_datatrove.py` | One datapoint, datatrove MinHash dedup (CI-installed competitor). |
| `orchestrate.py` | Subprocess-per-datapoint sweep, OOM-tolerant, reuses the head-to-head evaluator, `summary.md`. |
| `throughput_perf_gate.py` | Deterministic per-PR cost gate vs `perf_gate_baseline.json`. |
| `../../.github/workflows/bench-corpus-dedup.yml` | Dispatch headline lane. |

## Known limitations / follow-ups

- **datatrove config is reasonable, not squeezed.** `(B=10, R=10)` targets ~0.8 Jaccard; a
  datatrove expert could tune `n_grams`/precision further. Documented in `run_datatrove.py`.
- **One corpus family per run.** The injected-dup mix (exact/partial/paraphrase) is fixed; other
  corruption profiles would exercise the LSH bands differently.
- **Single-node.** Distributed billion-scale throughput is #1084; the corpus product surface is #1085.
