# Scale audit — May 2026

**Status**: Round 1 closed — 10K, 100K, 500K complete; 1M run hit a C-extension SystemError mid-`auto_configure` after 30 min wall (per-stage flush preserved the data). Round 2 will narrow allocation attribution and investigate the SystemError.

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

| rows      | wall (s) | peak RSS (MB) | peak Python heap (MB) | clusters | status |
|----------:|---------:|--------------:|----------------------:|---------:|:-------|
|    10,000 |    81.32 |         262.3 |                 101.8 |    2,604 | ok     |
|   100,000 |   564.51 |       1,159.0 |               1,340.9 |   84,695 | ok     |
|   500,000 | 1,424.49 |       3,669.0 |               1,470.3 |  432,159 | ok     |
| 1,000,000 | 1,811.51 |       5,037.6 |               1,355.9 |        0 | **SystemError mid-`auto_configure` (partial)** |

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

### 1,000,000 rows (dupe_rate = 0.15) — partial / FAILED

| stage           | wall (s) | RSS Δ (MB) | tracemalloc peak (MB) |
|-----------------|---------:|-----------:|----------------------:|
| `read_csv`      |     0.12 |      369.0 |                  27.4 |
| `auto_configure`| 1,809.35 |    3,180.3 |               1,355.9 |
| `run_dedupe`    |     _DNF_ |       _—_ |                   _—_ |

**Failed during `auto_configure`** after 30 min wall, peak RSS 5,037 MB (well under the 30 GB watchdog budget — this isn't an OOM):

```
SystemError: error return without exception set
```

This is the cpython idiom for "a C extension returned NULL without setting an exception state" — usually a bug in a native module (Polars, rapidfuzz, pyo3) or an undefined-behaviour path in C code. No traceback was captured by the original `except` clause (now fixed: subsequent runs will dump `traceback.format_exc()` to stderr).

**What this tells us:**

1. **The cliff at 1M is not memory.** Peak RSS at the failure point was 5 GB on a 64 GB box; the heap was at 1.4 GB. Both well below where any sane limit would trip.
2. **`auto_configure` scales as projected** — 1,809s at 1M vs 755s at 500K is a 2.4× factor for 2× rows. Slightly super-linear in wall, **2.94× RSS Δ for 2× rows** (worse than 500K's 1.46×/5×). The working-set plateau hypothesis from 500K may be breaking; need more data to know.
3. **There is a reproducible C-extension fault inside the controller path at 1M rows.** This is a real bug, not a scale issue. Independent of any throughput-ceiling work, this needs investigation — `goldenmatch dedupe 1M-row.csv` on default settings doesn't currently work.

### Scaling 500K → 1M (partial, auto_configure only)

| metric | factor (2×) | character |
|---|---:|---|
| auto_configure wall   | 2.4× | Slightly super-linear; was 2.5× per 5× at 500K |
| auto_configure RSS Δ  | **2.94×** | Super-linear; worse than 500K projection. Working-set plateau may be breaking |
| auto_configure heap   | 1.01× | Heap plateau holding |
| read_csv RSS Δ        | 2.33× | Slightly super-linear; CSV stays 3-4 KB/row |

---

## Pending

The full audit grid:

- [x] 10K rows — done
- [x] 100K rows — done
- [x] 500K rows — done (peak RSS 3.7 GB; no OOM near the historical "cliff" CLAUDE.md cites — that note is stale)
- [x] 1M rows — **partial**: `auto_configure` measured (30 min wall, 5 GB peak RSS); `run_dedupe` not reached. Failure is a C-extension SystemError, not memory.
- [ ] 5M rows — _skipped per OOM-risk discussion_; can't run cleanly until the 1M SystemError is fixed
- [ ] 10M rows — _skipped per OOM-risk discussion_; we need code changes (both the SystemError fix and probably memory reduction) before measuring this

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

## Next moves (Round 1 close — 1M data forces another revision)

The 1M failure changes the priority order again. **The leading concern is no longer wall time, RSS, or run_dedupe — it's the SystemError at 1M.**

1. **Investigate the SystemError at 1M `auto_configure`** (highest priority). Reproducing on a 1M synthetic fixture is the easiest debug path: re-run with the harness's now-fixed traceback dumping, identify which C extension call returns NULL without an exception state. Candidates from CLAUDE.md:
   - rapidfuzz cdist on a too-large NxN block (CLAUDE.md notes "blocking key choice dominates fuzzy performance — coarse keys create huge blocks")
   - Polars expression evaluation on a column the controller's matchkey introspection has invalidated
   - pyo3 boundary in the bridge or in `goldenmatch.core.probabilistic`'s EM training
   - Could also be a memory-fragmentation hit even at 5 GB RSS — Windows handles large allocations poorly when fragmented

2. **Once 1M completes cleanly, re-measure to land the full 1M datapoint.** Auto_configure's 2.94× RSS Δ for 2× rows is the next concerning signal — the 500K plateau hypothesis may be breaking. We need clean 1M numbers to know whether `auto_configure` or `run_dedupe` is the actual leading concern.

3. **`run_dedupe`'s super-linear RSS growth** (3× per 5× rows from 100K→500K) remains a parking-lot item for Round 2, but is downgraded until we have 1M data showing whether it actually happens.

4. **Throughput-ceiling work (issue #171) is functionally blocked on (1).** We cannot ship "10M on 64 GB by default" if 1M crashes on default settings.

5. **Step 2 of issue #171 (autoselect backend) deprioritized further** — even though the strategic answer hasn't changed (500K fits comfortably; autoselect threshold isn't urgent), the immediate path forward is fixing the SystemError.

6. **Small-win parking lot**: noisy `auto-config: NE scorer 'ensemble' for field 'id' not registered or failed` warning still fires ~14× per run regardless of row count. Quick PR; not blocking. May be related to the SystemError if the scorer name is leaking into a downstream C-extension call.

### Round 2 scope (separate PR)

Round 2 is now a two-track investigation:

**Track A: SystemError attribution**
- Reproduce 1M on the synthetic fixture with the harness's traceback dump (now committed).
- If traceback points at a specific Polars / rapidfuzz / pyo3 call, write a minimal repro.
- Filing it as a goldenmatch issue is enough deliverable — fix may belong upstream.

**Track B: Allocation attribution** (the original Round 2 scope, kept)
- `tracemalloc.snapshot()` at peak inside `run_dedupe_df` (need a clean 1M run first — Track A blocks this).
- `tracemalloc.snapshot()` at end of each controller iteration to confirm the working-set plateau holds at 1M or breaks (`auto_configure`'s 2.94× RSS Δ growth at 1M suggests it may be breaking).
- Profile `score_blocks_parallel` — at 500K it consumed roughly half the `run_dedupe` wall.

The discipline from PR #120 holds: **measure, don't speculate**. This doc is the measurement step before any code change. Round 1 closes with a real bug surfaced that wasn't on anyone's radar.
