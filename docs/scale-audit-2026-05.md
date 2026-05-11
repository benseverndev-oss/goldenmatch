# Scale audit — May 2026

**Status**: Round 1 (10K + 100K done). 1M / 5M / 10M to follow; see _Pending_ at the bottom.

**Why**: Issue #171, the scale-gate workstream. PR #120's strategy doc ranks throughput-ceiling lift as the #1 highest-leverage next direction. The CLAUDE.md note that says "1M records: OOM in-memory" is ~6 months old and predates the v1.7-v1.12 controller. Before we change any code path or wire an autoselect threshold, we need a current profile.

**How**: `scripts/scale_audit.py` generates a synthetic person fixture at the requested row count (reusing `tests/generate_synthetic.py`), runs `goldenmatch dedupe`'s in-process equivalent (`auto_configure_df` + `run_dedupe_df`) under tracemalloc + psutil RSS sampling, and times each named stage.

**Hardware** (this round): Windows 11, Python 3.13.12 (uv-managed), 64 GB physical RAM. Each measurement is a single run — multi-run medians arrive when the longer row counts ship.

**Fixture shape**: Synthetic person records with 6 columns (first_name, last_name, email, phone, address, zip). 15% duplicate rate via the existing `tests/generate_synthetic.py` generator (also used by the autoconfig regression suite, so the shape is well-trodden).

---

## TL;DR (so far)

Three findings — the 100K data revised the 10K story:

1. **`auto_configure` is still the dominant stage**, but no longer by a 64% margin — it's 54% of wall at 100K, with `run_dedupe` catching up fast (33% → 45% of wall as rows go 10K → 100K). Suggests `run_dedupe` has higher-order scaling than auto-config does, so the dominant stage may flip at 1M.
2. **RSS scales sub-linearly so far** — 4.4× memory for 10× rows (10K → 100K). That's much better than the 10K-data linear projection suggested. 1M rows naively projects to ~5 GB peak RSS (well within a 64 GB box). 10M projects to ~25 GB — still an engineering problem but closer to "fixable" than "fundamental".
3. **Python heap scaling is the worse story** — 13.2× for 10× rows. Both stages peak at ~1.3 GB heap on 100K, where they peaked near 100 MB on 10K. Tracemalloc says they peak at the same moment, which lines up with the controller's finalize step re-running the pipeline.

**Wall time is the actual concern**: 564 s at 100K. If wall scales 7× per 10×, 1M ≈ 65 min, 10M ≈ 7.6 hr. The throughput ceiling per #171 isn't just RSS-bound — wall time at scale will lose users before RAM does.

The Round 1 conclusion stands but with revised priority: **fix `auto_configure`'s wall + heap profile**, then look at `run_dedupe`'s wall. RSS is the least-broken axis. The 1M run will confirm which of the two stages dominates above the OOM cliff.

---

## Summary table

| rows    | wall (s) | peak RSS (MB) | peak Python heap (MB) | clusters | status |
|--------:|---------:|--------------:|----------------------:|---------:|:-------|
|  10,000 |    81.32 |         262.3 |                 101.8 |    2,604 | ok     |
| 100,000 |   564.51 |       1,159.0 |               1,340.9 |   84,695 | ok     |

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
| peak Python heap      | 13.2× | **Super-linear** — the worrying axis |
| auto_configure wall   | 5.9× | The fixed-cost-heavy stage |
| run_dedupe wall       | 9.5× | The per-row-cost stage; catching up |
| run_dedupe RSS Δ      | 11.1× | Super-linear; expect this to hurt at 1M |

---

## Pending

The full audit grid:

- [x] 10K rows — done
- [x] 100K rows — done
- [ ] 500K rows — not yet (per CLAUDE.md, this is approximately where the historical OOM cliff sits)
- [ ] 1M rows — not yet; will confirm which stage dominates above the cliff
- [ ] 5M rows — stretch; ~25 GB peak RSS projected (within a 64 GB box if extrapolation holds)
- [ ] 10M rows — stretch; success criterion for issue #171

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

## Next moves (working hypothesis, revised after 100K)

1. **Run 500K and 1M to find the actual cliff.** The CLAUDE.md OOM note may be stale — if the 100K trend holds, 1M should be doable on a 64 GB box (peak RSS ~5 GB). Need data to know where the real cliff is and which stage triggers it.
2. **Profile `auto_configure_df` with `tracemalloc.snapshot()`** to identify dominant allocation sites. The 1.3 GB shared peak between `auto_configure` and `run_dedupe` at 100K suggests duplicated work — confirm by snapshotting at peak. Likely candidates:
   - `RunHistory.entries` retaining sample DataFrames across iterations
   - `IndicatorContext` memoization not cleared on commit
   - The "auto_configure_df re-entry inside run_dedupe_df" CLAUDE.md note about the Task 5.2 fix on the web path — the CLI / programmatic path may not have the same guard
3. **Profile `run_dedupe`'s super-linear RSS growth** (11× for 10× rows). Likely candidates:
   - `scored_pairs` accumulation as a Python list
   - Cluster `pair_scores` dict size
   - Matchkey-output column materialization
4. **Step 2 of issue #171** (autoselect backend by row count) is **lower priority** than initially estimated — extrapolation says default path may already fit 1M on 64 GB. Reconsider after 1M data lands.
5. **Small-win parking lot**: the noisy `auto-config: NE scorer 'ensemble' for field 'id' not registered or failed` warning fires every controller iteration. Either register the scorer, demote the warning to debug, or skip the field upfront. Quick PR; not blocking.

The discipline from PR #120 holds: **measure, don't speculate**. This doc is the measurement step before any code change.
