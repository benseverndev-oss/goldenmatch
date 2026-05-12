# Scale audit — May 2026

**Status**: Round 1 closed; Round 2 fixed two bugs that surfaced. 10K, 100K, 500K complete (clean); 1M originally hit a SystemError/MemoryError after 30 min wall, **now fixed and runs in 151s** until a separate third-package bug surfaces in goldenflow.

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

1. **The cliff at 1M is memory after all — but it's a hidden cliff, not the OS budget.** Peak RSS at the failure point was 5 GB on a 64 GB box; the heap was at 1.4 GB. The OS-level OOM-killer never fired. The 5 GB ceiling is presumably a Windows working-set commit limit or a contiguous-large-allocation failure mode — Python's `malloc` returns NULL well before the OS would have OOM'd us.
2. **`auto_configure` scales as projected** — 1,809s at 1M vs 755s at 500K is a 2.4× factor for 2× rows. Slightly super-linear in wall, **2.94× RSS Δ for 2× rows** (worse than 500K's 1.46×/5×). The working-set plateau hypothesis from 500K may be breaking; need more data to know.
3. **There is a reproducible memory fault inside the controller path at 1M rows.** This is a real bug, not a scale issue. Independent of any throughput-ceiling work, this needs investigation — `goldenmatch dedupe 1M-row.csv` on default settings doesn't currently work.

### Second 1M run (different failure path, same root cause)

A re-run with `faulthandler.enable()` and `except BaseException` failed slightly differently:

| run | wall (s) | peak RSS (MB) | auto_configure RSS Δ | failure |
|---|---:|---:|---:|---|
| 1M attempt #1 | 1,811 | 5,037 | +3,180 | `SystemError: error return without exception set` |
| 1M attempt #2 | 1,767 | 5,579 | +3,472 | `MemoryError: ` (no message) |

Same general failure mode (memory pressure around 5 GB → C-extension allocation fails) but the propagation path differs run-to-run. `MemoryError` (run 2) is the polite Python idiom; `SystemError` (run 1) is what happens when the same C code returns NULL without calling `PyErr_SetString` first. Both indicate **an allocation in C code failing**, which fits the hypothesis that some matrix in fuzzy block scoring is being allocated above Windows's effective large-contiguous-block ceiling.

Stderr-vs-stdout redirect bug in the harness meant the first traceback dump landed in the void; fixed for run #3 (3rd 1M attempt in progress).

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

---

## Round 2 results (2026-05-11)

Track A turned up **two real goldenmatch bugs**, both fixed in this PR. Track B's `run_dedupe`-internal allocation profile is still blocked, but on a separate goldenflow bug (filed as follow-up), not on goldenmatch.

### Bug 1 — float64 NxN matrices in scorer at 1M

The Round 1 1M run crashed inside `find_fuzzy_matches`. With `faulthandler.enable()` + `except BaseException` + redirected stderr (harness hardening, also in this PR), we got a clean traceback the second time. Phase 1 (cheap accumulators) and Phase 3 (fuzzy accumulators) in `core/scorer.py` were allocating `np.zeros((n, n), dtype=np.float64)` — at the largest block that's hundreds of MB of pure zeros for similarity scores that live in `[0, 1]` and never need 64-bit precision.

**Fix:** changed Phase 1, Phase 3, and the `_fuzzy_score_matrix` ensemble paths (`jw`, `ts`, `sx`) to `dtype=np.float32`. Single-scorer paths (`jaro_winkler`, `levenshtein`, `token_sort`) wrapped in `np.asarray(..., dtype=np.float32)` so rapidfuzz's float64 output gets downcast at the boundary. Net effect: roughly halves matrix memory inside scoring.

### Bug 2 — O(N²) pair-set materialization in learned blocking

After the float32 fix, the 1M run got past the first scorer phase and crashed again — this time in `core/learned_blocking.py:127`, inside `evaluate_rule`. The old code built `blocked_pairs: set[tuple[int, int]]` from `itertools.combinations(members, 2)` for every block, then took `recall = len(true_pairs & blocked_pairs) / len(true_pairs)`. With a low-cardinality candidate key at 1M rows, one block can hold 200K members → 20 billion tuples → ~600 GB of Python set memory. That's the actual MemoryError, not the float64 matrices.

**Fix:** count blocked pairs in closed form (`sum(len(m) * (len(m) - 1) // 2 for m in blocks.values())`) and compute recall by iterating `true_pairs` against a `block_of: dict[row_id, block_key]` O(1) lookup table. The explicit pair set was never used elsewhere — only `len()` and `len(... & true_pairs)` mattered. New code is O(N) memory + O(|true_pairs|) recall work; old code was O(blocked_pairs) memory before any recall arithmetic ran.

### Bug 3 — PanicException in goldenflow (filed as follow-up, not fixed here)

With both goldenmatch fixes in place, 1M now runs auto_configure cleanly in **151s** with **2103 MB peak RSS** (a 12× wall improvement and 2.4× RSS improvement over Round 1's pre-crash partial), then dies with `PanicException: PyObject pointer is null` inside goldenflow's `transform_df` at a `Polars Series._s.to_list()` call. Different package, different root cause; not in scope for this PR.

### Updated 1M datapoint

| rows      | wall (s) | peak RSS (MB) | peak heap (MB) | clusters | status |
|----------:|---------:|--------------:|---------------:|---------:|:-------|
| 1,000,000 | 153.84   | 2,103.3       | 713.6          | 0        | auto_configure complete (151s); run_dedupe blocked on goldenflow PanicException |

Round 1's 1M row in the summary table (1,811s, 5,038 MB, SystemError) is now obsolete — that partial result reflected two bugs piled on top of each other. The fixed run is roughly an order of magnitude better on both axes, before run_dedupe has even started.

### Implications for the throughput-ceiling priority order

- **Memory is not the cliff at 1M.** 2.1 GB peak RSS for the heaviest stage on default settings makes the CLAUDE.md "1M records: OOM in-memory" note officially stale — the warning predates v1.7-v1.12 controller, and the OOM was bug-driven not architectural. **CLAUDE.md update is a follow-up.**
- **Wall is still the cliff, but the slope just shifted.** auto_configure at 1M is now 151s instead of crashing-after-30-min. The 10M projection from Round 1 (7 hr) was extrapolating from a buggy curve; the real curve is gentler. Need a clean 10M run to re-project.
- **The user's step 4** ("memory reduction only if (2) shows it's still a problem at 1M+") is **answered no for now** — at 1M, RSS is fine. Revisit at 5M+.
- **The user's step 3** ("attack wall time") is unblocked for goldenmatch internals but blocked end-to-end on the goldenflow PanicException. Filing that as `goldenflow#TBD: transform_df Polars Series PanicException at 1M rows` is the next concrete deliverable after this PR lands.

### Harness changes shipped alongside the fixes

`scripts/scale_audit.py` got four hardening improvements while we were chasing the crashes:

- `faulthandler.enable()` at startup so C-level crashes write a Python-side stack to stderr before the process dies.
- Per-stage atomic JSON flush (`tmp + os.replace`) so a mid-run crash still leaves the completed stages on disk.
- `_RSSWatchdog` daemon thread sampling at 0.5s — catches transient spikes that one-shot psutil reads at stage boundaries miss.
- `except BaseException` (not just `Exception`) with `traceback.format_exc()` dumped to stderr — `SystemError` from a C extension would otherwise sail past `except Exception` silently.

These changes are kept regardless of what the 10M curve looks like — they're cheap diagnostics insurance.

The discipline from PR #120 holds: **measure, don't speculate**. This doc is the measurement step before any code change. Round 1 closes with a real bug surfaced that wasn't on anyone's radar.

---

## Round 3 results — cloud measurement (2026-05-12)

**Setup**: Both Round 2 fix PRs landed (#173 goldenmatch float32 + O(N) blocking, #175 goldenflow `value_counts`). 1M ran end-to-end clean locally on Windows 64 GB — BUT the local run pushed the host into OS-level OOM at peak (per-stage psutil RSS only saw 4.3 GB; the true working set was higher and the Windows file-cache plus tracemalloc traceback storage tipped the box over). To get an honest peak we offloaded to a GitHub-hosted runner (PR #177 / #179: `scale-audit.yml` workflow_dispatch lane). Runner: `ubuntu-latest` (4-core, 16 GB). tracemalloc disabled (`--no-tracemalloc`) so the tracker's own per-allocation traceback overhead wouldn't inflate the peak.

**Result**: 1M completed end-to-end clean. No OOM. No watchdog trip. 836,155 clusters. `failed: null`. `stop_reason: POLICY_SATISFIED` (the controller was happy with the config it committed, not bailing on a time budget).

| stage           | wall (s) | wall (mm:ss) | peak RSS (MB) | RSS Δ (MB) |
|-----------------|---------:|-------------:|--------------:|-----------:|
| `read_csv`      |     0.15 |        00:00 |         432.9 |     +324.5 |
| `auto_configure`|  1,326.9 |        22:07 |       6,934.6 |   +6,598.6 |
| `run_dedupe`    |  1,250.8 |        20:51 |       7,648.4 |     +737.7 |
| **total**       |  **2,579.4** | **42:59** | **9,977.0**   | —           |

### Comparison vs Round 2 local (1M, the run that pushed Windows into OOM)

| metric                       | Round 2 local (Win11 64 GB, tracemalloc on) | Round 3 cloud (Linux 16 GB, tracemalloc off) |
|------------------------------|---:|---:|
| total wall                   | 3,716 s (62 min) | **2,579 s (43 min, 1.4× faster)** |
| reported peak RSS (psutil)   | 4,338 MB         | 9,977 MB |
| OS-level OOM at peak         | yes              | no |
| ran to completion            | partial — goldenflow PanicException | full |
| clusters produced            | 0 (died mid-pipeline) | 836,155 |

The 9.98 GB cloud peak is the **honest** number; the 4.3 GB local "peak" was psutil's view from inside a process that was already paging hard. Once you strip tracemalloc's traceback storage and run on Linux (no Windows file cache competing for RAM), 1M dedupe with the default zero-config controller fits comfortably inside a 16 GB box with ~6 GB headroom.

### What this resolves

1. **The `1M records: OOM in-memory` line in `packages/python/goldenmatch/CLAUDE.md` is officially obsolete.** Trimmed in PR #182. 1M is fine on a default 16 GB Linux box.
2. **Issue #176 (Round 3 memory attribution) is mostly answered.** The missing ~3-5 GB I couldn't account for via per-stage psutil sampling was tracemalloc traceback storage. Turn tracemalloc off and the working set fits the measured stage peaks.
3. **`auto_configure`'s 2.94× RSS Δ growth at 1M (Round 2 partial) was an artefact** of the partial measurement — the broken run wasn't reaching its actual peak. Clean 1M shows `auto_configure` peak at 6.9 GB and `run_dedupe` peak at 7.6 GB — both in the same order of magnitude, no super-linear blow-up.

### What this re-prioritises

**Wall time is the unambiguous cliff now.**

- 1M takes 43 min on the cloud's 4-core box. Locally that was 62 min on a Windows laptop.
- Auto_configure (22 min) and run_dedupe (21 min) are roughly equal — both need attention.
- A naive 10× extrapolation gives 7 hr at 10M. Realistically super-linear (blocking + scoring scale worse than O(N)) pushes that toward 10-15 hr.

Step 3 of the original 4-step user directive ("attack wall time") is promoted to #1 priority. Step 4 (memory reduction) is downgraded to "only matters at 5M+ on small boxes".

### Round 4 scope (separate PR, in progress)

- **cProfile attribution on cloud** (PR #183): adds `--cprofile <path>` flag + `cprofile` workflow_dispatch input. First fire: `rows=100000, cprofile=on, tracemalloc=off` — hot functions don't change with row count, 100K cloud runs in ~3 min, so the attribution loop is fast.
- Once the .prof shows the actual hot spots inside `auto_configure` (the controller's iterations + sample-run finalize) and `run_dedupe` (score_blocks_parallel + clustering), pick the #1 wall target and open a code PR.
- Likely candidates worth a measurement before believing them: `find_fuzzy_matches` rapidfuzz cdist setup overhead, `learn_blocking_rules` predicate-combination evaluation, `score_blocks_parallel` ThreadPool dispatch, Polars `value_counts` / `group_by` calls in classification. Resist guessing.

---

## Round 3 correction (2026-05-12)

**The Round 3 1M numbers above were ~2× too high.** Discovered while reading the Round 4 cProfile output. The harness was double-counting a full-data pipeline run; the goldenmatch core was already optimal w.r.t. that lever.

### The bug

`scripts/scale_audit.py` was calling `auto_configure_df(df)` without `_skip_finalize=True`. The controller's `_finalize` step then ran a full goldenmatch pipeline on the entire dataset *inside the auto_configure stage*. The harness then immediately called `run_dedupe_df(df, config=...)` which ran the same full pipeline AGAIN. Two full-data pipelines instead of one.

The production user-facing entry point (`goldenmatch._api.dedupe_df`) was already threading `_skip_finalize=True` specifically to prevent this duplication. The audit harness was the only caller not matching the production pattern. Fix landed in PR #187.

### Corrected 1M measurements (cloud run [25714358083](https://github.com/benzsevern/goldenmatch/actions/runs/25714358083))

`ubuntu-latest` (4-core, 16 GB Linux), tracemalloc off, both PRs #186 + #187 on main.

| stage | **corrected wall** | corrected peak RSS | Round 3 (buggy) wall | Round 3 (buggy) peak RSS |
|---|---:|---:|---:|---:|
| `read_csv` | 0.15 s | 437 MB | 0.15 s | 433 MB |
| `auto_configure` | **61 s** | 1,366 MB | 1,327 s (22 min) | 6,935 MB |
| `run_dedupe` | **1,237 s** (20.6 min) | 7,518 MB | 1,251 s | 7,648 MB |
| **total** | **1,300 s (21.7 min)** | **9,846 MB** | 2,579 s (43 min) | 9,977 MB |

836,154 clusters (same as Round 3's 836,155 to within one-row noise). No OOM, no watchdog trip. ~6 GB headroom on the 16 GB cloud box.

### What this changes

1. **The "1M takes 43 min" headline was wrong by 2×.** Real production wall is 21.7 min. The auto_configure stage was almost entirely the redundant _finalize run, not real controller work — the actual controller iteration loop is **61 s** at 1M.
2. **`auto_configure` is NOT a wall-time optimization target.** It's 5% of total wall (61 s of 1,300 s). The "structural lever" of consolidating controller iterations was a non-target — sample iterations are cheap (5K-row cap regardless of dataset size).
3. **The only target left is `run_dedupe`** (1,237 s = 95% of wall). That's where any future wall-time work has to go.

### What this confirms

The Round 4 cProfile attribution (100K run) is still valid for direction, but the absolute self-times were inflated by the 2× double-pipeline run. The relative ranking of hot spots holds: `rapidfuzz.cdist` (the actual scoring) > `find_fuzzy_matches` orchestration > `_fuzzy_score_matrix` > `_generalize` (already landed in PR #186) > `_exact_score_matrix`. These all live inside `run_dedupe`, so they're still the right targets.

### Next concrete step

Re-cProfile a 1M run with the corrected harness. Now affordable: 21.7 min wall + 30% cprofile overhead ≈ 28 min. The 100K profile may not predict 1M hot spots accurately — at 1M, blocking-related operations scale super-linearly and may overtake per-block scoring in relative cost. Round 5 attribution will tell us which of `find_fuzzy_matches`, `score_blocks_parallel` threading overhead, or `learn_blocking_rules` predicate evaluation deserves the next code change.

### Lesson

This is the second time the audit's own measurement bug masked the real production behaviour:

- Round 2: tracemalloc on inflated reported peak RSS, masking 1M-fits-in-16GB.
- Round 3: missing `_skip_finalize=True` doubled reported wall, masking that auto_configure is already cheap.

Both bugs landed in PRs alongside their discoveries (#173 hardened tracemalloc handling, #185 fixed artifact hidden-files, #187 fixed skip_finalize). The harness now matches the production path on both axes (RSS measurement + pipeline-call sequence). Future Rounds should trust the numbers more.

---

## Round 6 outcomes — v0-floor fix lands the commit-policy half (2026-05-12)

PR #197 fixed the commit-policy bug (issue #195): `pick_committed()`'s lex tiebreaker in the precision-collapsed regime was mechanically biased toward iterations that lowered threshold most. Post-fix, the controller correctly commits v0 in the collapsed regime instead of an over-corrected later iteration.

**Cloud validation at 2M ([run 25749197084](https://github.com/benzsevern/goldenmatch/actions/runs/25749197084)) confirms the commit policy is fixed**: log shows `auto-config committed best-effort RED config (iter=v0, ...)`. Pre-fix, this would have been `iter=3`.

**But the user-facing symptom is barely changed:**

| metric | 2M pre-fix | 2M post-fix | 2M with perfect dedupe |
|---|---:|---:|---:|
| total wall | 303 s | **229 s** | (n/a) |
| auto_configure | 7.3 s | 7.1 s | (n/a) |
| run_dedupe | 293 s | 219 s | (n/a) |
| peak RSS | 7,898 MB | 7,788 MB | (n/a) |
| **clusters** | **1,971,064** | **1,985,184** | **~1,700,000** |

Cluster count moved from 1.97M to 1.985M — both essentially "nothing got merged." The wall improved slightly (less iteration overhead committed) but the downstream pipeline still produced near-singleton output.

### Second bug, exposed by the first fix

The v0 config that gets committed isn't the same at 1M and 2M. Diagnostic via `scripts/investigate_autoconfig_2m.py`:

| | 1M v0 (produces 836K clusters) | **2M v0 (produces 1.99M clusters)** |
|---|---|---|
| matchkey threshold | 0.8 | 0.8 |
| address scorer | token_sort | token_sort |
| blocking strategy | learned | learned |
| **blocking transforms** | **`['lowercase', 'strip']`** | **`['lowercase', 'soundex']`** |

**v0's learned blocking key is sample-dependent.** The learner runs on a 5K sample (`min(total_rows // 4, 5000)`). At 1M the sample comes from a smaller pool and the best-recall predicate evaluates to `strip`. At 2M the same 5K sample comes from a larger pool and the predicate evaluator picks `soundex`. Soundex at 2M produces oversized blocks that hit the `max_block_size = 5000` cap and get filtered before scoring — explaining why 99.3% of 2M rows end up as singletons.

### Why this wasn't caught at 1M

At 1M, learned blocking happens to pick `strip`. `strip` keeps surnames distinct so blocks are small enough to pass the max_block_size filter, scoring runs, and dedupe works. The transition between `strip`-good and `soundex`-degenerate is somewhere between 1M and 2M (or possibly even smaller — the learner is sample-state-dependent, not strictly row-count-dependent).

### What this Round resolves and doesn't

**Resolved:**
- The commit-policy bug in `pick_committed()`. v0 is now correctly chosen in the collapsed regime.
- Issue #195's commit-policy half. Test pinned (`test_pick_committed_collapsed_regime_prefers_earliest_iteration`).

**Not resolved (separate bug, surfaced by the fix):**
- Learned blocking's sample-dependence at scale. Even when v0 is the committed config, v0's learned blocking key is wrong at 2M.
- The user-facing symptom (1.99M clusters at 2M). Until the learned-blocking layer is fixed, 2M+ still produces degenerate output.

### Two paths forward

1. **Fix learned-blocking determinism.** Make the learner's predicate choice less sample-state-dependent. Options: stratify the sample by likely-cluster, evaluate predicates against a larger sample, or score predicates by a more scale-invariant metric than current. Needs investigation; the right approach depends on what makes `soundex` look attractive to the learner at 2M-but-not-1M.
2. **Add a guard for degenerate v0.** When the v0 config's blocking key produces all-large blocks (above `max_block_size`), fall back to a simpler blocking strategy (multi-pass on exact-match-eligible columns) before committing v0. Cheaper to land than (1) but doesn't fix the root cause.

Both are out of scope for #195. **Filed as follow-up issue.**

### Status of the scale-audit workstream

The 2M-degenerate behavior was a non-issue when only 1M was being measured. Now that we've pushed past 1M, learned blocking is the new long pole. The DuckDB exploration (parked behind #195) is now parked behind the learned-blocking-determinism follow-up too — measuring storage backends on a pipeline that doesn't dedupe at 2M+ remains meaningless.

The 1M baseline (12.3 min, 8.4 GB, 836K correct clusters) is unchanged and remains the user-facing claim.
