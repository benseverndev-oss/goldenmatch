# ER head-to-head scaling bench

Splink (DuckDB backend) vs GoldenMatch (bucket + native + Arrow) across
**100k / 1M / 5M / 25M / 100M** rows, on one machine, with an identical fixture
per scale. Built because no published apples-to-apples wall+RSS comparison exists
between these engines (Splink publishes 7M/~2min/1B-pairs; GoldenMatch publishes
25M/6.5min/57.7GB — different pair counts, different operations, not comparable).

## What it measures

Per `(scale, engine)`:
- **Speed/memory**: end-to-end dedupe wall, peak RSS, scored-pair count, resolved
  cluster count, pairs/sec. Splink's wall includes train+predict+cluster;
  GoldenMatch's includes auto_configure+dedupe — each engine's full required
  workflow, so neither is flattered.
- **Accuracy vs ground truth** (one engine-agnostic evaluator, identical code for
  both): **pairwise** precision/recall/F1 + confusion matrix (TP/FP/FN/TN), and
  **B-cubed** (B³) precision/recall/F1. Computed from a DuckDB contingency table
  (`evaluate.py`) — no pair materialization, so it stays memory-bounded at 25M/100M.

## How it stays honest

- **Realistic fixture**: census-weighted surnames (Zipfian — real "Smith/Johnson"
  frequencies, so realistic hot blocks) and real given names, with single-char
  typos + nulls on duplicates. Both engines read the identical parquet per scale.
- **Same machine, same scale.** `scored_pairs` is reported so blocking-aggressiveness
  differences are *visible* — pair count, not row count, drives ER wall-clock.
- **Both engines get an explicit, idiomatic, tuned config** (neither auto-config
  nor a crippled spec):
  - *GoldenMatch* uses an explicit **bucket + native + Arrow** config (its actual
    optimized path). `run_goldenmatch.py` sets `GOLDENMATCH_NATIVE=1` and asserts
    `native_enabled("block_scoring")`, so a pure-Python fallback can never
    masquerade as "the optimized backend".
  - *Splink* gets an idiomatic settings spec (compound blocking + standard
    comparisons + EM training on selective keys).
- **A real engine asymmetry is surfaced, not hidden**: the bucket backend does
  **single-key blocking** (one eager bucket pass — that's how it stays fast),
  whereas Splink **unions multiple blocking rules**. So GoldenMatch is given its
  best single key (`postcode`, ~0.94 pair coverage here) and Splink its rule union
  (~0.99). The resulting recall gap is a genuine property of each engine's fast
  path, reported plainly via the accuracy columns.

## How it survives OOM on GitHub

`orchestrate.py` never loads a fixture. Each datapoint runs as an isolated
subprocess; the OS frees its memory on exit. An OOM-killed datapoint (SIGKILL,
no result file) is recorded as `status: OOM` and the sweep continues. The
aggregate JSON is flushed after every datapoint, so a late OOM never loses
earlier results.

**The 100M bucket datapoint is expected to OOM on a single 64 GB box** —
GoldenMatch's bucket backend is in-memory and 25M already peaks at ~58 GB. That
ceiling is a legitimate result: it's exactly where the single-node in-memory path
ends and the Ray/distributed path (or Splink's DuckDB spill) takes over.

## Running it

CI (recommended — the 25M tier needs 64 GB):

> GitHub → Actions → **bench-er-headtohead** → Run workflow.

Locally (small scales):

```bash
# generator needs only numpy + pyarrow
python scripts/bench_er_headtohead/generate_fixture.py --rows 100000 \
    --out /tmp/b/bench_100000.parquet --ground-truth /tmp/b/bench_100000.truth.parquet

# full sweep (build native first: python scripts/build_native.py)
python scripts/bench_er_headtohead/orchestrate.py \
    --scales 100000 1000000 --engines goldenmatch splink --workdir /tmp/bench_er
```

## Files

| File | Role |
|---|---|
| `generate_fixture.py` | Streaming, bounded-memory person-shaped parquet generator (+ ground truth). |
| `run_goldenmatch.py` | One datapoint, GoldenMatch bucket+native+arrow, fails loud if native absent. |
| `run_splink.py` | One datapoint, Splink 4.x DuckDB dedupe, counts via DuckDB relations (no pandas materialization). |
| `evaluate.py` | Engine-agnostic accuracy: pairwise P/R/F1 + confusion + B³, via DuckDB contingency table. |
| `orchestrate.py` | Subprocess-per-datapoint sweep, OOM-tolerant, runs eval, aggregates to `summary.md` + `bench_results.json`. |
| `../../.github/workflows/bench-er-headtohead.yml` | CI lane (64 GB runner, builds native, installs Splink). |

## Known limitations / follow-ups

- **Blocking parity is inherently imperfect**: GoldenMatch's fast bucket path is
  single-key; Splink unions rules. We give each its best idiomatic config, but
  the recall ceilings differ by design. A multi-pass (non-bucket) GoldenMatch lane
  would close recall at a speed cost — a possible follow-up lane.
- **One fixture shape** (person-like, 5 fields). Other shapes (bibliographic,
  product) would exercise different blocking/scoring behaviour.
- **Splink comparison spec is fixed**, not tuned per scale; a Splink expert could
  squeeze more. The spec is in `run_splink.py` and is meant to be reasonable, not
  optimal.
