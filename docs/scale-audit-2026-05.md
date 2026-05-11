# Scale audit — May 2026

**Status**: Round 1 (10K + 100K + 500K done; 1M running). See _Pending_ for the rest.

**Why**: Issue #171, the scale-gate workstream. PR #120's strategy doc ranks throughput-ceiling lift as the #1 highest-leverage next direction. The CLAUDE.md note that says "1M records: OOM in-memory" is ~6 months old and predates the v1.7-v1.12 controller. Before we change any code path or wire an autoselect threshold, we need a current profile.

**How**: `scripts/scale_audit.py` generates a synthetic person fixture at the requested row count (reusing `tests/generate_synthetic.py`), runs `goldenmatch dedupe`'s in-process equivalent (`auto_configure_df` + `run_dedupe_df`) under tracemalloc + psutil RSS sampling, and times each named stage.

**Hardware** (this round): Windows 11, Python 3.13.12 (uv-managed), 64 GB physical RAM. Each measurement is a single run — multi-run medians arrive when the longer row counts ship.

**Fixture shape**: Synthetic person records with 6 columns (first_name, last_name, email, phone, address, zip). 15% duplicate rate via the existing `tests/generate_synthetic.py` generator (also used by the autoconfig regression suite, so the shape is well-trodden).

---

## TL;DR (so far)

The 500K data significantly reshapes the story relative to the 100K projection. Three findings:

1. **The 13.2× heap explosion from 10K→100K was an artifact**, not a fundamental memory problem. At 500K, peak Python heap is essentially **flat** (1.1× growth for 5× rows). Once the dataset is large enough that per-row cost dominates over the controller's fixed-iteration overhead, heap stops growing. The 10K→100K "super-linear heap" hypothesis is now retracted.
2. **`auto_configure` and `run_dedupe` are converging on equal cost.** At 10K they were 2:1; at 100K 1.2:1; at 500K **1.13:1**. By 1M they likely swap dominance and `run_dedupe` becomes the bottleneck. The "fix auto_configure first" Round 1 conclusion is partially superseded — both stages need attention, and `run_dedupe`'s super-linear RSS growth (3× for 5× rows) is the worse trend.
3. **10M-rows-on-64GB now looks tractable for memory.** Revised projections from 500K data:

   | at | wall | peak RSS | peak heap |
   |---:|---:|---:|---:|
   | 1M | ~50 min | ~7 GB | ~1.5 GB |
   | 10M | ~7 hr | ~30 GB | ~2 GB |

   The 10M target in issue #171 is no longer obviously RAM-bound. **Wall time is the cliff** — 7 hr at 10M will lose users before RAM does. The throughput-ceiling workstream's priority order shifts: wall first (probably blocking and scoring), then `run_dedupe`'s super-linear RSS growth, then `auto_configure`. The CLAUDE.md "1M records: OOM in-memory" note appears to be **stale and overstated** based on this data.

The 1M run (in progress) will confirm whether the convergence flips or holds. After that, Round 1 closes and Round 2 narrows to `tracemalloc.snapshot()` attribution.

---

## Summary table

| rows    | wall (s) | peak RSS (MB) | peak Python heap (MB) | clusters | status |
|--------:|---------:|--------------:|----------------------:|---------:|:-------|
|  10,000 |    81.32 |         262.3 |                 101.8 |    2,604 | ok     |
| 100,000 |   564.51 |       1,159.0 |               1,340.9 |   84,695 | ok     |
| 500,000 | 1,424.49 |       3,669.0 |               1,470.3 |  432,159 | ok     |

---

## Per-stage breakdown

### 10,000 rows (dupe_rate = 0.15)

| stage           | wall (s) | RSS Δ (MB) | tracemalloc peak (MB) |
|-----------------|---------:|-----------:|----------------------:|
| `read_csv`      |     0.05 |       10.5 |                  27.4 |
| `auto_configure`|    52.25 |      134.9 |                  99.3 |
| `run_dedupe`    |    26.89 |       24.4 |                 101.8 |

**Observations:**

- `auto_configure` consumes 64% of total wall and 51% of the RSS growth.
- `run_dedupe`'s tracemalloc peak (101.8 MB) is barely higher than `auto_configure`'s (99.3 MB) despite running a full match. The controller's sample-and-iterate loop is allocating roughly the same heap as the final full-data run.
- A noisy `auto-config: NE scorer 'ensemble' for field 'id' not registered or failed` warning fires ~14 times during auto_configure on this fixture — every controller iteration retries the same field/scorer combination that's known to fail. Cleaning that up is a candidate small-win.

### 100,000 rows (dupe_rate = 0.15)

| stage           | wall (s) | RSS Δ (MB) | tracemalloc peak (MB) |
|-----------------|---------:|-----------:|----------------------:|
| `read_csv`      |     0.01 |       42.6 |                  27.4 |
| `auto_configure`|   306.88 |      741.2 |               1,340.9 |
| `run_dedupe`    |   255.91 |      270.3 |               1,340.7 |

**Observations:**

- `auto_configure` still leads on both wall (54%) and RSS Δ (67%) but `run_dedupe` is the faster-growing stage — its wall grew 9.5× from 10K to 100K, vs auto_configure's 5.9×. By 1M the two may swap.
- Python heap peaks at ~1.3 GB on **both** stages. Same value to the megabyte — strongly suggests the peak moment lives in shared infrastructure (`run_dedupe_df` is invoked inside the controller's finalize step and also by the final `run_dedupe` call). Confirms the CLAUDE.md note about "auto_configure_df re-entry inside run_dedupe_df" being a possible site of redundant work.
- Per-row peak RSS: ~12 KB (was ~26 KB at 10K). Auto-config has higher fixed cost than per-row cost, which is good news — the relative cost amortises with row count.
- The `auto-config: NE scorer 'ensemble'` warning still fires (now ~14× on 100K, same as 10K — so it's a per-iteration cost, not per-row, which means the controller is still doing the same number of iterations regardless of input size).

### Scaling so far (10K → 100K)

| metric | factor | notes |
|---|---:|---|
| wall                  | 6.9× | Sublinear; encouraging |
| peak RSS              | 4.4× | Sublinear; even better |
| peak Python heap      | 13.2× | **Super-linear** — _the 500K data overturns this_ |
| auto_configure wall   | 5.9× | The fixed-cost-heavy stage |
| run_dedupe wall       | 9.5× | The per-row-cost stage; catching up |
| run_dedupe RSS Δ      | 11.1× | Super-linear; _500K shows this slowing too_ |

### 500,000 rows (dupe_rate = 0.15)

| stage           | wall (s) | RSS Δ (MB) | tracemalloc peak (MB) |
|-----------------|---------:|-----------:|----------------------:|
| `read_csv`      |     0.00 |      158.5 |                  27.4 |
| `auto_configure`|   754.85 |    1,081.3 |               1,340.9 |
| `run_dedupe`    |   667.86 |      817.2 |               1,470.3 |

**Observations:**

- **Two stages converging.** Wall ratio `auto_configure : run_dedupe` was 2:1 at 10K, 1.2:1 at 100K, now **1.13:1** at 500K. By 1M they likely swap; by 5M `run_dedupe` should dominate.
- **Heap peaks essentially flat** between 100K (1,340.9 MB) and 500K (1,470.3 MB). Once the dataset is large enough, the controller's working set is bounded. 13× heap growth in the 10K→100K range was the iteration-cost-dominates phase; we're past that now.
- **`run_dedupe`'s tracemalloc peak finally exceeds `auto_configure`'s** (1,470 MB vs 1,341 MB) — first row count where they differ. Confirms separate allocation peaks; the 100K coincidence (both at 1,340.9 MB) was the controller's finalize step re-running matching, allocating exactly the same heap as the controller's own iterations had at peak. At 500K the final run allocates more.

### Scaling 100K → 500K

| metric | factor (5×) | character |
|---|---:|---|
| wall                  | 2.5× | Sublinear |
| peak RSS              | 3.2× | Sublinear |
| peak Python heap      | **1.1×** | **Essentially flat** — plateau confirmed |
| auto_configure wall   | 2.5× | Now per-row-dominated |
| run_dedupe wall       | 2.6× | Slightly faster growth than auto_configure |
| auto_configure RSS Δ  | 1.46× | Working set bounded |
| run_dedupe RSS Δ      | 3.0× | Super-linear; the next thing to attack |

---

## Pending

The full audit grid:

- [x] 10K rows — done
- [x] 100K rows — done
- [x] 500K rows — done (peak RSS 3.7 GB; no OOM near the historical "cliff" CLAUDE.md cites — that note is stale)
- [ ] 1M rows — running (projected ~50 min wall, ~7 GB peak RSS)
- [ ] 5M rows — _skipped per OOM-risk discussion_; would project ~30 GB but would tie up the box for ~3 hrs and we'd learn nothing the 1M didn't already say
- [ ] 10M rows — _skipped per OOM-risk discussion_; we need a code change before measuring this

Each row count also wants a `tracemalloc.snapshot()` at the moment of peak allocation, broken out by code location, so we can identify the actual dominant allocation source line. This first round establishes "where in the pipeline" (stage); the next round narrows to "which expression in that stage."

---

## Reproducing

```bash
# Generate the fixture + run one audit pass:
python scripts/scale_audit.py --rows 10000 --out .profile_tmp/scale_10k.json

# Aggregate completed runs:
python scripts/scale_audit.py --summarize .profile_tmp/scale_*.json > audit.md
```

Fixtures live under `.profile_tmp/scale_fixtures/` (gitignored). JSON outputs under `.profile_tmp/` (also gitignored). Only this doc gets committed.

---

## Next moves (revised after 500K)

The Round 1 priority order changes substantially based on 500K data:

1. **`run_dedupe`'s wall + super-linear RSS** is now the leading concern. Its RSS Δ scales 3× per 5× rows; its wall is the fastest-growing stage. At 5M+ rows it will be the dominant bottleneck, not `auto_configure`. Round 2 should `tracemalloc.snapshot()` inside `run_dedupe_df` at peak to identify the allocation site.
2. **`auto_configure`'s working set is bounded** — heap plateau at 500K confirms it. Don't prioritize this anymore. The "controller's finalize step re-runs the pipeline" hypothesis from 100K analysis is right but it's not actually the problem — the cost is the per-iteration sample matching, not memory retention across iterations.
3. **Wall time is the cliff, not RAM.** Issue #171's success criterion (10M on 64 GB) is likely already memory-feasible. The throughput-ceiling problem is reframed: hit wall time first. Candidate attacks:
   - Cap controller iterations more aggressively when the data shape is "easy" (the controller currently runs the same iteration count regardless of input size — that's why `auto_configure` wall grows at all)
   - Parallelize fuzzy block scoring more aggressively (current `score_blocks_parallel` already does this; check if the per-block fan-out is row-count-aware)
   - Polars-native fast paths for matchkey transforms that currently round-trip through Python
4. **Step 2 of issue #171 (autoselect backend) deprioritized further.** 500K-on-default-settings consumes 3.7 GB peak RSS in 24 minutes wall. We don't need an autoselect threshold — we need to make the default faster.
5. **Small-win parking lot**: noisy `auto-config: NE scorer 'ensemble' for field 'id' not registered or failed` warning still fires ~14× per run regardless of row count. Quick PR; not blocking.

Round 2 scope (to land as a separate PR after 1M):

- Add `tracemalloc.snapshot()` at peak inside `run_dedupe_df`, attribute the 1.5 GB heap allocation to specific code locations
- Add `tracemalloc.snapshot()` at end of each controller iteration to confirm the working set really doesn't accumulate
- Profile `score_blocks_parallel` specifically — at 500K it's ~half the `run_dedupe` wall

The discipline from PR #120 holds: **measure, don't speculate**. This doc is the measurement step before any code change.
