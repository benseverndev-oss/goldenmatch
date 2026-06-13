# GoldenAnalysis

**Measure and report across the Golden Suite.** A read-only, cross-cutting
analysis / metrics / reporting engine: it consumes any stage's typed artifacts
(or a raw DataFrame) and emits a unified, exportable `AnalysisReport`.

> GoldenAnalysis ships the generic frame path plus suite adapters (GoldenMatch /
> GoldenCheck / GoldenFlow / GoldenPipe), cross-run trend + regression detection,
> an edge-safe TypeScript port (optional WASM), and an optional Rust accelerator
> for the heavy aggregation primitives — all documented below. See
> `docs/superpowers/specs/2026-06-08-goldenanalysis-cross-cutting-analysis-engine-design.md`
> for the design rationale.

## Install

```bash
pip install goldenanalysis
```

Zero suite dependencies for the generic path — it works on any polars DataFrame
even with no other Golden package installed.

## Quickstart

```python
import polars as pl
import goldenanalysis as ga

df = pl.read_parquet("customers.parquet")

report = ga.analyze(df, analyzers=["frame.summary"])
print(report.to_markdown())

report.to_json("report.json")
report.to_parquet("report.parquet")   # long-form metric frame + table sidecars
```

CLI:

```bash
goldenanalysis report customers.parquet --analyzers frame.summary --format markdown
goldenanalysis report report.json --format markdown      # re-render a saved report
```

`trend` and `regressions` operate over a saved run history (see **Cross-run** below).

## Over the suite

With the relevant extra installed (`pip install goldenanalysis[match,check,flow,pipe]`):

```python
# A GoldenMatch dedupe result -> match.rates + cluster.distribution
report = ga.analyze_match(dedupe_result)

# A whole-pipeline manifest -> every analyzer whose artifacts are present
report = ga.analyze_pipeline(pipe_result)
```

`match.rates` emits `match.recall_estimate` when GoldenMatch ran
`dedupe_df(..., certify=True)` (it attaches an unsupervised `RecallEstimate`), and
`match.recall_safe_bound` when you pass an audit-calibrated certificate
(`analyze_match(result, certificate=...)`) — the safe bound needs a labelled
sample, so it can't be computed automatically. Both degrade silently when absent.

## Cross-run — trend + regression detection

Store reports over time, then trend a metric or detect regressions without ground
truth:

```python
hist = ga.ReportHistory(backend="jsonl", path=".golden/analysis.jsonl")  # or backend="sqlite"
hist.append(report)                                  # keyed by (dataset, run_id)

hist.trend("cluster.singleton_ratio", "customers")   # -> TrendSeries

policy = ga.RegressionPolicy(default_pct=10.0, per_metric={"match.recall_safe_bound": 2.0})
regs = hist.detect_regressions("customers", baseline="rolling_median", policy=policy)
print(report.to_markdown(regs))                      # callout + Δ-vs-baseline column
```

The `Baseline` is a strategy (`rolling_median` default — immune to one noisy night
— plus `previous` / `last_known_good`), and `RegressionPolicy` thresholds are
per-metric and respect each metric's `direction` (a `higher_better` metric only
flags on a drop). CLI:

```bash
goldenanalysis trend --metric cluster.singleton_ratio --dataset customers --history .golden/analysis.jsonl
goldenanalysis regressions --dataset customers --history .golden/analysis.jsonl \
  --policy "match.recall_safe_bound=2" --fail-on-regression   # exit 1 on a flagged regression (CI gate)
```

## GoldenCheck vs GoldenAnalysis

They are easy to confuse and are deliberately distinct:

| | GoldenCheck | GoldenAnalysis |
|---|---|---|
| **Scope** | Profiles a *single input dataset at ingest* | *Cross-cutting* over any stage's outputs |
| **Direction** | A **producer** of artifacts (scan findings) | A **consumer** of artifacts (incl. GoldenCheck's) |
| **Across runs?** | No — one dataset, one scan | Yes — trend / drift / regression over a run history |
| **Writes data?** | Suggests/applies fixes | **Never** — read-only by construction |

The hard line: **GoldenAnalysis depends on other packages' types; never the
reverse.** It sits *beside* the pipeline as a reporting step, consuming
GoldenCheck / GoldenFlow / GoldenMatch / GoldenPipe / InferMap outputs — it does
not replace GoldenCheck's ingest-time profiling, and GoldenCheck does not import
GoldenAnalysis.

## Native accelerator (optional, `goldenanalysis[native]`)

An optional Rust accelerator for the heavy aggregation primitives, gated exactly
like `goldenmatch[native]` / `goldencheck[native]`:

```bash
pip install goldenanalysis[native]   # pulls the separate goldenanalysis-native wheel
```

The pure-Python path stays the **default and the byte-identical reference**. The
compiled kernel (`analysis-core` pyo3-free + `analysis-native` abi3 wheel) mirrors
`core/aggregate.py`'s `histogram` / `quantile` value-for-value, reading input as a
Float64 Arrow array (zero-copy). The loader gate (`core/_native_loader.py`,
`GOLDENANALYSIS_NATIVE=auto|0|1`) uses a primitive only once it's in `_GATED_ON` —
which holds **`histogram` and `quantile`**: both proved byte-identical
(`tests/core/test_native_parity.py`) **and** measured **5.8–9.9x faster** than the
pure Python loop on Linux x86_64 at 1M–10M rows, *including* the list→Arrow
conversion the dispatch pays (`benchmarks/aggregate_benchmark.py` +
`bench-analysis-native.yml`). A new primitive joins only after the same two gates
clear — "it's Rust" is never enough (the goldencheck composite-key kernel was 2.5x
*slower* until the gate caught it). With `goldenanalysis[native]` installed, the
`auto` default uses the native path automatically; `GOLDENANALYSIS_NATIVE=0` forces
pure. In-tree dev build: `uv run python scripts/build_analysis_native.py`.

## License

MIT.
