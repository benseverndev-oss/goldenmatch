---
layout: default
title: Accuracy Benchmark
nav_order: 7
---

# Accuracy Benchmark

`infermap` ships with a cross-language accuracy benchmark used to gate every release. It measures the engine the same way Python and TypeScript users actually consume it, using a manifest-driven case corpus, hand-anchored metrics, and a self-test that fails CI on regression.

> TL;DR — current headline numbers (synthetic slice, both runners): **F1 ≈ 0.95**, parity within **0.3pp** between Python and TypeScript.

## Why a benchmark?

Schema-mapping engines are easy to overfit. A single hand-tuned alias can flatter the score on a small fixture suite without improving real behavior. The benchmark exists so that:

- **Every PR** is scored against the same corpus, in both languages, with the same metrics.
- **Cross-language drift** is caught immediately. A v0.1.x bug — Python silently re-inferred dtypes through polars while TypeScript did not — was caught by the benchmark and fixed by the new `MapEngine.map_schemas()` API.
- **Regressions are blocked** by CI. The `regression-ack` workflow requires an explicit `regression-ack` label on the PR if F1 drops more than the threshold.

## What it measures

| Metric | What it answers |
|--------|-----------------|
| **F1** | Of the mappings the engine emitted, how many are correct, and how many correct ones did it miss? |
| **Top-1 accuracy** | When the engine picks the highest-scoring target for a source field, is it right? |
| **MRR** | If the correct target isn't #1, how far down the candidate list is it? |
| **ECE** | Are the engine's confidence scores actually calibrated, or wishful thinking? |

Each metric has a hand-computed anchor so a refactor can't silently change the math. Anchors live under `benchmark/tests/parity/`.

## Running it locally

```bash
# Python
pip install -e ".[dev]"
pip install -e "benchmark/runners/python[dev]"
python -m infermap_bench run --output report-py.json
python -m infermap_bench report report-py.json

# TypeScript
cd benchmark/runners/ts && npm install --install-links && npm run build
node dist/cli.js run --output ../../../report-ts.json
node dist/cli.js report ../../../report-ts.json

# Compare two reports
python -m infermap_bench compare --baseline report-py.json --current report-ts.json
```

The CLI also supports filtering: `--only category:names`, `--only difficulty:hard`, `--only tag:abbrev`, or just a case ID prefix.

## Public API additions in v0.2

### `MapEngine.map_schemas()` (Python) / `mapSchemas()` (TS)

If you already have a `SchemaInfo` and don't need the engine to re-extract from a CSV/DataFrame, call `map_schemas` directly. This avoids round-tripping through the file/dtype inference layer.

```python
from infermap import MapEngine, FieldInfo, SchemaInfo

src = SchemaInfo(fields=[FieldInfo(name="cust_id", dtype="int64", sample_values=["1", "2"], value_count=2)])
tgt = SchemaInfo(fields=[FieldInfo(name="customer_id", dtype="int64", sample_values=["100", "200"], value_count=2)])

engine = MapEngine()
result = engine.map_schemas(src, tgt)
print(result.mappings[0])
```

### `return_score_matrix=True` (Python) / `returnScoreMatrix: true` (TS)

Opt-in flag that exposes the full M×N score matrix on the result. Used by the benchmark to compute MRR — handy in your own code if you want to inspect runners-up or build a UI that lets users override low-confidence picks.

```python
engine = MapEngine(return_score_matrix=True)
result = engine.map_schemas(src, tgt)
for source_field, candidates in result.score_matrix.items():
    top3 = sorted(candidates.items(), key=lambda kv: -kv[1])[:3]
    print(source_field, top3)
```

## Contributing cases

The corpus lives at `benchmark/cases/` and is indexed by `benchmark/manifest.json`. To add a case:

1. Create `benchmark/cases/<category>/<your-case-id>/{case.json, expected.json, source.csv, target.csv}`.
2. Run `python -m infermap_bench rebuild-manifest` to regenerate the manifest.
3. Commit and open a PR. CI will score it in both languages.

Edge cases that are particularly valued: ambiguous abbreviations, name collisions across categories, mixed case + delimiter conventions, and types where naming alone is misleading.

## Self-test

`benchmark/self-test/` is a tiny corpus with a frozen expected scorecard. It runs in CI on every PR — any change to the engine that moves the self-test scores by more than `1e-4` fails the build, even if the full benchmark looks fine. Treat it as a tripwire.

## See also

- [`benchmark/README.md`](https://github.com/benzsevern/infermap/tree/main/benchmark) — runner architecture
- [`docs/design/`](https://github.com/benzsevern/infermap/tree/main/docs/design) — design docs and trade-off notes
- [Examples](https://github.com/benzsevern/infermap/tree/main/examples) — including `08_benchmark_introspection.py`
