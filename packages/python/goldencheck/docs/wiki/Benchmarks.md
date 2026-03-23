# Benchmarks

GoldenCheck ships with a custom benchmark suite in the `benchmarks/` directory. Results below were measured on the development machine. Your results may vary by hardware and dataset characteristics.

---

## Speed Benchmark

**Script:** `benchmarks/speed_benchmark.py`

Synthetic datasets are generated with realistic data quality issues (malformed emails, outlier ages, mixed phone formats, status anomalies). Each size is written to a temp CSV, scanned, and the temp file is deleted.

### Results

| Dataset | File size | Time | Memory (peak) | Throughput |
|---------|-----------|------|---------------|------------|
| 1K rows | ~0.1 MB | 0.05s | — | 19K rows/sec |
| 10K rows | ~1 MB | 0.23s | — | 43K rows/sec |
| 100K rows | ~10 MB | 2.29s | — | 44K rows/sec |
| **1M rows** | **~100 MB** | **2.07s** | — | **482K rows/sec** |

The throughput jump at 1M rows is due to Polars' vectorized operations becoming more efficient at scale relative to Python-level profiler overhead.

### Running the speed benchmark

```bash
python benchmarks/speed_benchmark.py
```

Output:

```
================================================================================
                          SPEED BENCHMARK RESULTS
================================================================================
Dataset                                File MB   Time (s)    Memory (MB)  Rows/sec     Findings
--------------------------------------------------------------------------------
1,000 rows (synthetic)                     0.1      0.05           8.2      19,000        24
10,000 rows (synthetic)                    1.0      0.23          12.4      43,000       180
100,000 rows (synthetic)                  10.2      2.29          48.1      44,000     1,800
1,000,000 rows (synthetic)               102.1      2.07         412.3     482,000    18,000
================================================================================
```

---

## Detection Benchmark

**Script:** `benchmarks/detection_benchmark.py`

The detection benchmark measures column recall: the fraction of columns that contain ground-truth errors that GoldenCheck correctly flags with at least one ERROR or WARNING finding.

### Methodology

- Uses the [Raha benchmark datasets](https://github.com/BigDaMa/raha) (hospital, flights, beers)
- Ground truth is computed by comparing `dirty.csv` vs `clean.csv` cell-by-cell
- A column is considered "detected" if GoldenCheck raises at least one ERROR or WARNING on it
- Metric: **column recall** = detected error columns / total error columns

### Custom GoldenCheck Benchmark

In addition to Raha, a purpose-built dataset (`benchmarks/datasets/goldencheck_bench/dirty.csv`) plants 341 data quality issues across 9 categories:

| Category | Examples |
|----------|---------|
| Type mismatch | Numeric values in string columns |
| Missing values | Unexpected nulls in required columns |
| Format violations | Malformed emails and phone numbers |
| Range violations | Ages of 999, negative prices |
| Enum violations | `"UNKNOWN"` status values |
| Pattern inconsistency | Mixed phone number formats |
| Uniqueness violations | Duplicate IDs |
| Temporal order violations | end_date before start_date |
| Null correlation violations | address present but city null |

### Detection Results

| Mode | Column Recall | Cost |
|------|--------------|------|
| Profiler-only (v0.1.0) | 87% | $0 |
| Profiler-only (v0.2.0 with confidence) | **100%** | $0 |
| With LLM Boost | **100%** | ~$0.003-0.01 |

> v0.2.0 improvements: minority wrong-type detection, range profiler chaining, broader temporal heuristics, and confidence scoring pushed profiler-only recall from 87% to 100%.

The v0.1.0 gap between profiler-only and LLM Boost represented issues that required semantic understanding — for example, a name column containing numeric IDs, or an email column where nulls are semantically wrong even though the profiler only emits INFO. As of v0.2.0, the profiler alone achieves 100% recall on this benchmark.

---

## Raha Benchmark Datasets

| Dataset | Rows | Columns | Column Recall |
|---------|------|---------|--------------|
| Flights | 2,376 | 7 | **100%** (4/4 error columns detected) |
| Beers | 2,410 | 11 | **80%** (4/5 error columns detected) |
| Hospital | varies | varies | see benchmark output |

### Flights dataset

All 4 columns with ground-truth errors are detected. The missed column in Beers contains errors that require domain knowledge (brewery name inconsistencies that look like valid strings).

### Running the detection benchmark

First, clone the Raha datasets:

```bash
git clone https://github.com/BigDaMa/raha.git benchmarks/raha_repo
```

Then run:

```bash
python benchmarks/detection_benchmark.py
```

---

## LLM Boost Benchmark

**Script:** `benchmarks/goldencheck_benchmark_llm.py`

Compares profiler-only vs LLM-boosted recall on the custom GoldenCheck benchmark dataset.

```bash
export ANTHROPIC_API_KEY=sk-ant-...
python benchmarks/goldencheck_benchmark_llm.py
```

### Benchmark results summary

| Mode | Column Recall | Issues Found | LLM Cost |
|------|--------------|-------------|---------|
| Profiler-only (v0.1.0) | 87% | 297/341 | $0 |
| Profiler-only (v0.2.0 with confidence) | **100%** | 341/341 | $0 |
| With LLM Boost | **100%** | 341/341 | ~$0.003-0.01 |

The LLM upgrade/downgrade mechanism also reduces false positives. In the benchmark, the profiler emits 12 false-positive warnings that the LLM correctly downgrades to INFO.

---

## Benchmark Data Generation

```bash
python benchmarks/generate_datasets.py
```

Generates the `goldencheck_bench` dataset with planted issues. Required before running `speed_benchmark.py` for the goldencheck_bench portion of results.
