# Future work

A long-lived index of deferred work — non-urgent items that surfaced during measurement or implementation and were intentionally not pursued. Items here should be **specific, measured, and have a clear hypothesis**. If something is just a vague idea, it doesn't belong here; it belongs in a brainstorm doc.

When an item gets picked up, move it (or its outcome) out of this file. When something gets killed for good, leave a one-line note explaining why.

---

## Scale-audit follow-ups (Round 5+, 2026-05)

### 1. Reduce fuzzy scoring wall via batched cdist / precomputed feature vectors

**Where**: `goldenmatch/core/scorer.py` — `find_fuzzy_matches`, `_fuzzy_score_matrix`, downstream `rapidfuzz.process_cpp.cdist`.

**What the data shows**: After PR #189 cleared the `DataFrame.filter` hotspot, these three are the new dominant cost in 1M dedupe wall:

- 100K Round 4 cProfile: `rapidfuzz.cdist` 19 s tottime, `find_fuzzy_matches` 11.8 s, `_fuzzy_score_matrix` 4.8 s.
- 1M Round 5 cProfile (pre-#189): `rapidfuzz.cdist` 89 s tottime, `find_fuzzy_matches` 45 s, `_fuzzy_score_matrix` 19 s.

Post-#189 these will be a larger fraction of the (now smaller) total. A Round 6 cProfile on the new code path is the first step.

**Hypothesis**: 30-50% wall reduction possible via two tactics:

1. **Batched cdist** — `cdist` is called once per (block × field) tuple. At 1M with ~50K blocks × 3 fuzzy fields, that's ~150K cdist invocations, each with overhead. Batching across fields (one cdist call producing a stacked similarity tensor) trades a constant Python-side overhead for an O(fields × N²) matrix that's then sliced field-by-field. Worth it when block size > some threshold.
2. **Precomputed feature vectors** — many string scorers (jaro-winkler, levenshtein) can be approximated by hashing into fixed-size feature vectors that allow vectorised dot-product similarity. rapidfuzz already exposes `process.cdist` with `score_cutoff` for early termination; we should make sure we're passing it (and a sane cutoff) from goldenmatch.

**How to validate**: Round 6 cProfile run, then prototype the batched cdist on a 100K fixture, measure delta. Microbench first; only ship if the gain on a realistic block-size distribution is materially better than the call overhead saved.

**Risk**: scorer correctness regressions. Lots of tests pin scorer behaviour at exact thresholds (e.g. autoconfig regression suite). Run the full goldenmatch test suite + DQbench adapter before merging.

---

### 2. `score_blocks_parallel` ThreadPool dispatch overhead

**Where**: `goldenmatch/core/scorer.py:727` `_score_one_block`, `score_blocks_parallel` orchestrator.

**What the data shows**: 100K Round 4 cProfile attributed **196 s cumtime** to `concurrent.futures._base.as_completed` and **164 s cumtime** to `threading.Event.wait` — about half the total wall flowed through synchronization primitives rather than actual scoring. At 1M Round 5: `threading.wait` cumtime 4,513 s (across both `_base.wait` and `Event.wait`).

The 1M cumtime number is inflated because each block's I/O wait gets attributed up the stack; the actual wall cost is bounded by the sequential portion. But the call counts — 1.7M `_thread.lock.acquire` calls at 1M — suggest the per-block dispatch overhead is non-trivial when blocks are small.

**Hypothesis**: 10-20% wall reduction by batching small blocks. Each block submission incurs a fixed ThreadPool overhead (~50-100 μs). At 50K blocks with average 10 members, the dispatch cost is ~3-5 s wall on its own; the actual scoring work per small block is comparable. Pre-coalesce small blocks into batches of (say) 1000 rows total before submission, score each batch in one cdist call, then post-split.

**Why this isn't first**: the win is smaller than item 1 and the code change is more invasive (touches the orchestrator). Pick it up after item 1's measurement.

**How to validate**: instrument `score_blocks_parallel` with explicit timing of (dispatch → wait → collect) per block; correlate with block size distribution from a real run. If small blocks (< 50 members) account for >30% of total dispatch time, the batching is worth it.

---

### 3. Land 2M dedupe under the 16 GB Linux runner ceiling

**Where**: combination of items 1 + 2 above, plus possibly explicit Polars-frame drops between pipeline stages.

**What the data shows**: Post PRs #197 and #200, the autoconfig + pipeline at 2M produces the correct config and does real scoring work — but peak RSS climbs past 13.5 GB and is still rising at the watchdog trip point. Tested watchdog bumps 14.5 → 15.0 GB; both tripped. Linear extrapolation says ~15-16 GB at full completion, right at OS-OOM on a 16 GB runner. Two cloud runs documented under Round 7 in `docs/scale-audit-2026-05.md`:

- watchdog=14500: trip at 13,050 MB, 28 min wall
- watchdog=15000: trip at 13,585 MB, 40 min wall

**Hypothesis**: Items 1 + 2 combined save ~2 GB → 2M peaks at ~11 GB, comfortable under any reasonable watchdog. Worth doing only if there's a real user ask for 2M+.

**How to validate**: re-fire 2M scale-audit with watchdog=14500 after the optimisations land. Cluster count should be ~1.7M (close to perfect 15% dedupe), peak RSS should be <12 GB.

**Risk**: each optimisation has its own behavioural / regression risk; expect 1-2 days of testing per item.

**Why this isn't urgent**: the 1M baseline (12.3 min, 8.4 GB, 836K correct clusters) is the user-facing claim. 2M is a "future user ask" not a current target.

---

## How to add to this file

- **Be specific**: include filepath:line for the code, a number from a measurement, and a hypothesis with an expected impact range.
- **Cite the source**: link the PR / cProfile run / issue that surfaced the item. If we can't point to evidence, the item isn't ready.
- **Pre-commit a validation plan**: how will you know it worked? "Microbench first" / "Re-run scale-audit" / "Compare cluster counts" — answer this before starting work.
