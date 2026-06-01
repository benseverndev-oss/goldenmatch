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

- **Same fixture, same machine, same scale** for both engines. `scored_pairs` is
  reported so any difference in blocking aggressiveness is *visible* rather than
  hidden — pair count, not row count, drives ER wall-clock.
- **GoldenMatch can't silently cheat downward**: `run_goldenmatch.py` sets
  `GOLDENMATCH_NATIVE=1` and asserts `native_enabled("block_scoring")`. If the
  native Arrow runtime isn't actually loaded, the datapoint fails loudly instead
  of quietly reporting a pure-Python number as "the optimized backend".
- **Splink isn't crippled**: it gets an idiomatic settings spec (compound
  blocking + standard comparisons) mirroring the blocking GoldenMatch's
  auto-config lands on. Splink has no zero-config mode — that asymmetry is real
  and noted, not engineered away.

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

- **One fixture shape** (person-like, 5 fields). Other shapes (bibliographic,
  product) would exercise different blocking/scoring behaviour.
- **Splink comparison spec is fixed**, not tuned per scale; a Splink expert could
  squeeze more. The spec is in `run_splink.py` and is meant to be reasonable, not
  optimal.
