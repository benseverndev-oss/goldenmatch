# Scale audit — May 2026

**Status**: Round 1 (10K + 100K runs). 1M / 5M / 10M to follow; see _Pending_ at the bottom.

**Why**: Issue #171, the scale-gate workstream. PR #120's strategy doc ranks throughput-ceiling lift as the #1 highest-leverage next direction. The CLAUDE.md note that says "1M records: OOM in-memory" is ~6 months old and predates the v1.7-v1.12 controller. Before we change any code path or wire an autoselect threshold, we need a current profile.

**How**: `scripts/scale_audit.py` generates a synthetic person fixture at the requested row count (reusing `tests/generate_synthetic.py`), runs `goldenmatch dedupe`'s in-process equivalent (`auto_configure_df` + `run_dedupe_df`) under tracemalloc + psutil RSS sampling, and times each named stage.

**Hardware** (this round): Windows 11, Python 3.13.12 (uv-managed), 64 GB physical RAM. Each measurement is a single run — multi-run medians arrive when the longer row counts ship.

**Fixture shape**: Synthetic person records with 6 columns (first_name, last_name, email, phone, address, zip). 15% duplicate rate via the existing `tests/generate_synthetic.py` generator (also used by the autoconfig regression suite, so the shape is well-trodden).

---

## TL;DR (so far)

Two findings, one of which was unexpected:

1. **`auto_configure` is the dominant time cost — by a wide margin.** At 10K rows, it's **52 s of an 81 s total wall (64%)**, and 134 MB of a 262 MB RSS peak. The `run_dedupe` stage proper is half the time and a quarter of the memory.
2. **The controller's measurement loop runs the pipeline multiple times on samples before committing.** This explains the time but not the RSS — auto-config holds onto sample profiles, run history, and indicator-context objects across iterations.

The "1M records OOM" note in CLAUDE.md likely understates the problem: if 10K rows of zero-config dedupe touches 262 MB peak RSS, the RSS-per-row ratio is ≈26 KB. Naively scaled (which it won't be — there are super-linear stages) that puts 1M rows at ~26 GB, 10M rows at ~260 GB. The autoconfig path is going to OOM well before the matching path becomes the bottleneck.

That reframes the workstream: **fix auto_configure's memory profile first**, then look at the dedupe path. The 100K + 1M runs will confirm or refute this.

---

## Summary table

| rows  | wall (s) | peak RSS (MB) | peak Python heap (MB) | clusters | status |
|------:|---------:|--------------:|----------------------:|---------:|:-------|
| 10,000 |    81.32 |         262.3 |                 101.8 |    2,604 | ok     |
| 100,000 | _pending_ | _pending_ | _pending_ | _pending_ | _pending_ |

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

### 100,000 rows

_Run in progress as of doc draft. Result + per-stage will land in a follow-up commit on this branch._

---

## Pending

The full audit grid:

- [x] 10K rows — done
- [ ] 100K rows — running
- [ ] 500K rows — not yet
- [ ] 1M rows — not yet (this is the historical OOM cliff)
- [ ] 5M rows — stretch; expected to OOM in auto_configure based on extrapolation
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

## Next moves (working hypothesis, will revise after 100K + 1M)

1. **Profile `auto_configure_df` specifically** with `tracemalloc.snapshot()` to find the dominant allocation site. Likely candidates: `RunHistory.entries` retaining sample DataFrames; `IndicatorContext` memoization across iterations; redundant `auto_configure_df` re-entry inside `run_dedupe_df` (CLAUDE.md mentions this as a "Task 5.2 fix" for the web path — confirm CLI / programmatic paths got the same fix).
2. **Stage the 100K run repeatedly** with different fixture shapes (no dupes, all dupes, sparse fixture) to see whether the controller's cost is proportional to record count, duplicate-detection work, or fixture column count.
3. **Then** decide whether step 2 of #171's plan ("autoselect backend by row count") needs to happen before, or as part of, step 3 ("targeted memory reduction"). Current evidence suggests fixing auto_configure first is the higher-leverage move.

The discipline from PR #120 holds: **measure, don't speculate**. This doc is the measurement step before any code change.
