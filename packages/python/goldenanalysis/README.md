# GoldenAnalysis

**Measure and report across the Golden Suite.** A read-only, cross-cutting
analysis / metrics / reporting engine: it consumes any stage's typed artifacts
(or a raw DataFrame) and emits a unified, exportable `AnalysisReport`.

> **Phase 1 (`0.1.0`)** ships the generic frame path. Suite adapters, the other
> analyzers, cross-run regression detection, the TypeScript port, and the Rust
> accelerator land in later phases. See
> `docs/superpowers/specs/2026-06-08-goldenanalysis-cross-cutting-analysis-engine-design.md`.

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

`trend` and `regressions` are visible in `--help` but are stubs until `0.2.0`
(they need cross-run `ReportHistory`).

## Over the suite (`0.2.0`)

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

## License

MIT.
