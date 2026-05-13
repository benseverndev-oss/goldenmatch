# DuckDB SQL-side scoring: feasibility research (2026-05-12)

**Verdict: don't do it.** DuckDB SQL string-distance functions are slower per-pair than rapidfuzz cdist by ~21×. The architectural rewrite would be a regression.

## What I was investigating

After the Round 5 scale-audit win (PR #189, 21.7 min → 12.3 min at 1M), the next-biggest items in `docs/future-work.md` are `find_fuzzy_matches` + `_fuzzy_score_matrix` + `rapidfuzz.cdist`. A tempting alternative: push scoring into DuckDB SQL, letting the database's vectorised execution engine handle the per-pair work.

The `goldenmatch_dedupe_table` UDF in `goldenmatch-duckdb` already reads from DuckDB. Currently it pulls the whole table into Polars and runs the in-memory Python pipeline. The "obvious" optimisation: do the scoring entirely in SQL via DuckDB's native string-distance functions.

## What DuckDB provides

DuckDB 1.5.2 ships with the full string-distance toolkit native:

| function | what it does |
|---|---|
| `levenshtein(a, b)` | edit distance (returns BIGINT) |
| `damerau_levenshtein(a, b)` | edit distance with transpositions |
| `jaro_similarity(a, b)` | jaro similarity ∈ [0, 1] |
| `jaro_winkler_similarity(a, b)` | jaro-winkler similarity ∈ [0, 1] |
| `jaccard(a, b)` | jaccard set similarity |
| `hamming(a, b)` | equal-length hamming distance |
| `array_cosine_similarity(a, b)` | for embedding-vector pairs |

`jaro_winkler_similarity` is the one goldenmatch's `find_fuzzy_matches` uses heaviest. **Parity check: 100,000/100,000 match within 1e-6 against rapidfuzz.** No correctness blocker.

## The microbench that killed the idea

100,000 random name pairs ("John Smith" / "Jane Johnson" style). Three paths, same algorithm, same answers:

| path | wall | per-pair |
|---|---:|---:|
| `rapidfuzz.process.cdist` (what goldenmatch uses) | extrapolated from 5K×5K matrix | **25.8 ns** |
| `rapidfuzz.distance.JaroWinkler.normalized_similarity` per-pair Python loop | 31.2 ms | 312 ns |
| **DuckDB SQL `jaro_winkler_similarity(a, b)`** | **55.5 ms** | **555 ns** |

**rapidfuzz cdist is 21× faster per-pair than DuckDB SQL.** Even rapidfuzz called per-pair from a Python loop beats DuckDB SQL by 1.8×.

## Why DuckDB loses here

Counter to the intuition that "vectorised SQL = faster":

- **rapidfuzz `cdist` runs SIMD-friendly C loops** over the (N×M) cartesian product. The C inner loop does the entire matrix in one go with no per-row dispatch overhead.
- **DuckDB `jaro_winkler_similarity` is a scalar function** invoked per-row in the SQL execution plan. Each invocation pays a function-call overhead. DuckDB's general vectorised execution model gives you parallelism + batching at the *expression* level, but the scoring function itself is the bottleneck and it's slower than rapidfuzz's specialised implementation.
- **Splink uses DuckDB and is genuinely fast**, but Splink's win is *blocking* (reducing total candidate pair count) — not per-pair scoring speed. Once they've narrowed to a small candidate set, the per-pair cost matters less.

## Where DuckDB SQL scoring could still be the right move

This research kills the "do it for performance" case but leaves three legitimate use cases:

1. **Spill-to-disk for >>RAM datasets.** DuckDB transparently spills intermediate results to disk; the current Polars + rapidfuzz path doesn't. At 10M+ rows on a 16 GB box, the Python path OOMs and the SQL path doesn't. Per-pair cost is irrelevant if the alternative is "won't complete".
2. **SQL-first user workflows.** A user whose data is already in DuckDB and who wants the dedupe step inside their existing SQL pipeline. Some perf loss is acceptable to avoid a Python round-trip.
3. **Cross-database joins.** When the matching is between two DuckDB tables, doing it in SQL avoids serialising both into Polars memory.

**None of these are wall-time wins** for the 1M-on-16-GB-box case the scale audit measured. They're convenience / scalability ceiling wins.

## What to do instead

The `find_fuzzy_matches` / `_fuzzy_score_matrix` hot spots from `docs/future-work.md` item 1 stand. The right attack vectors are inside the rapidfuzz call site, not on the storage side:

- **Batched cdist across fields** — call rapidfuzz `cdist` once per block with all fields stacked, instead of once per (block × field) tuple. Reduces Python-side overhead without changing the scoring algorithm.
- **Early termination via `score_cutoff`** — rapidfuzz `cdist` supports a cutoff that short-circuits computation when a score can't reach the threshold. Pass goldenmatch's threshold through. The 1M cprofile (PR #183) shows ~3M rapidfuzz calls; even modest cutoff savings compound.
- **Cluster the calls** — many blocks are tiny (< 10 members). Coalesce small blocks before submitting to the ThreadPool so dispatch overhead amortises (future-work item 2).

None of these need a DuckDB rewrite.

## When to revisit

This research is valid for "in-memory dedupe of N records that fit in RAM". Revisit DuckDB SQL scoring if:

- The scale target moves to 10M+ on a small box (RAM-bound path matters more than wall).
- DuckDB ships a vectorised batch-scoring API (`SELECT jaro_winkler_batch(col_a, col_b) FROM t` that returns an array, with C-level loop over the column). The team has talked about something like this; not in 1.5.2.
- A user request specifically wants "stay in SQL, accept perf loss". Document the trade-off and ship.

## Reproduction

```python
import duckdb, time, random
import polars as pl
from rapidfuzz.distance.JaroWinkler import normalized_similarity as jw_rf
from rapidfuzz import process

random.seed(0)
N = 100_000
FIRST = ['John','Jane','Robert','Mary','James','Patricia','Michael','Jennifer']
LAST = ['Smith','Johnson','Williams','Brown','Jones','Garcia','Miller','Davis']
names_a = [random.choice(FIRST) + ' ' + random.choice(LAST) for _ in range(N)]
names_b = [random.choice(FIRST) + ' ' + random.choice(LAST) for _ in range(N)]

con = duckdb.connect()
df = pl.DataFrame({'a': names_a, 'b': names_b})
con.register('pairs', df.to_arrow())

t = time.perf_counter()
sql_scores = con.sql('SELECT jaro_winkler_similarity(a, b) FROM pairs').fetchall()
duckdb_wall = time.perf_counter() - t

t = time.perf_counter()
rf_scores = [jw_rf(a, b) for a, b in zip(names_a, names_b)]
rf_wall = time.perf_counter() - t

t = time.perf_counter()
_ = process.cdist(names_a[:5000], names_b[:5000], scorer=jw_rf)
cdist_sub_wall = time.perf_counter() - t

print(f'DuckDB SQL:           {duckdb_wall*1000:6.1f} ms ({duckdb_wall/N*1e9:.0f} ns/pair)')
print(f'rapidfuzz per-pair:   {rf_wall*1000:6.1f} ms ({rf_wall/N*1e9:.0f} ns/pair)')
print(f'rapidfuzz cdist:      {cdist_sub_wall*1000:6.1f} ms ({cdist_sub_wall/(5000*5000)*1e9:.1f} ns/pair)')
parity = sum(1 for x, y in zip(sql_scores, rf_scores) if abs(x[0] - y) < 1e-6)
print(f'parity: {parity}/{N}')
```

---

## 1M follow-up measurement: the existing UDF surface (2026-05-12)

Separate question from "should we rewrite scoring as SQL?": **how does the existing `goldenmatch-duckdb` UDF surface actually perform at 1M?** That UDF doesn't push scoring into SQL — it loads the DuckDB table into Polars via `con.cursor().sql(...).pl()` and dispatches to `dedupe_df` in-process. So the wall should be ~Python-baseline plus the DuckDB load overhead.

Measured via `scripts/scale_audit.py --backend duckdb-udf --rows 1000000` ([run 25742044451](https://github.com/benzsevern/goldenmatch/actions/runs/25742044451), `ubuntu-latest` 4-core 16 GB, tracemalloc off).

| stage | polars-direct (Round 5) | duckdb-udf | Δ |
|---|---:|---:|---|
| read_csv / `duckdb_load` | 0.15 s | 0.61 s | +0.46 s |
| auto_configure / `register_udfs` | 58 s | 0.005 s | (different work) |
| run_dedupe / `dedupe_via_udf` | 676 s | 702 s | +26 s |
| **total wall** | **737 s (12.3 min)** | **704 s (11.7 min)** | **−33 s** |
| peak RSS | 8,439 MB | 7,110 MB | **−1,329 MB (−16%)** |
| clusters returned | 836,154 (full clusters dict) | 145,352 (golden records — different semantic; same data underneath) |

### Reading this honestly

The duckdb-udf path is **wall-equivalent to polars-direct at 1M** — within ~5%, on the same side of noise. It's not slower despite the extra DuckDB load step, and it's modestly *cheaper* in RSS.

Three things explain the rough wall parity (and the small RSS win):

1. **The UDF doesn't push scoring into SQL.** It loads the DuckDB table back into Polars via the connection cursor and calls `dedupe_df` in-process. So the bulk of the wall is the same Python+rapidfuzz pipeline either path takes. The microbench result above (DuckDB SQL scoring is 21× slower per-pair) is irrelevant to this surface — that surface doesn't use SQL scoring.
2. **DuckDB columnar → Polars is faster than CSV → Polars.** `pl.read_csv` has parsing + schema-inference overhead the columnar fetch sidesteps. The `auto_configure` line is misleading in the side-by-side: polars-direct's 58 s is the controller's sample iterations, while the UDF's 0.005 s `register_udfs` is just function registration. Both paths then pay the auto-config cost inside `dedupe_df` — accounted under `dedupe_via_udf` in the UDF column. So the apples-to-apples comparison is `(read_csv + auto_configure + run_dedupe) = 734 s` vs `(duckdb_load + register_udfs + dedupe_via_udf) = 703 s`, with the UDF path winning by ~30 s on the load path.
3. **Lower RSS, also from the load path.** `pl.read_csv` materialises the full CSV into a Polars DataFrame; the DuckDB columnar table loads into Polars more compactly (or, the controller's working set sees a smaller upstream footprint). 1.3 GB headroom is real, even if the wall delta is in the noise.

### What this means

- **Using DuckDB AS storage is fine.** The `goldenmatch-duckdb` UDF surface adds no meaningful wall cost vs the Python API at 1M scale. A SQL-first user can `goldenmatch_dedupe_table('customers', '{}')` and pay ~11.7 min, same as `goldenmatch dedupe customers.csv`.
- **The microbench finding still stands.** *Pushing scoring INTO SQL* would be slower because rapidfuzz cdist beats DuckDB's scalar `jaro_winkler_similarity` by 21×. The UDF surface is wall-equivalent precisely because it *doesn't* try to do this — it stays in the Python path for the heavy work.
- **The architectural pivot point is `dedupe_df` calls Polars-in-memory.** Anything that wants to actually leverage DuckDB's storage engine (spill-to-disk, larger-than-RAM, vectorised aggregation) would have to bypass `dedupe_df` and do the scoring + clustering inside SQL. The microbench says that's a wall regression for the in-RAM case. Whether it's worth it for >>RAM cases is a separate question — measure it when there's a real >10M ask.

### Open questions left for future measurement

- **5M and 10M on duckdb-udf**: does the UDF surface degrade gracefully, or does the `con.cursor().sql(...).pl()` materialization blow up at scale? If yes, that's the natural moment to bypass `dedupe_df` and try SQL-side scoring with spill-to-disk — the per-pair cost regression may be worth the OOM avoidance.
- **The cluster-count semantic mismatch** (836K vs 145K above) — `goldenmatch_dedupe_table` returns golden records (one per multi-member cluster), `dedupe_df` returns the full clusters dict (multi-member + singletons). Not a bug; just a documentation gap. Worth a one-line note in the UDF's docstring.

---

## 2M scale-ceiling test — autoconfig degenerates, OOM-ceiling question unanswered (2026-05-12)

User's question: does the duckdb-udf surface enable larger row counts than polars-direct, as the existing positioning implies? Hypothesis going in: it does NOT, because the UDF's `con.cursor().sql(...).pl()` materializes the whole DuckDB table into Polars memory — same OOM ceiling as polars-direct.

Cloud measurement (`ubuntu-latest` 4-core 16 GB, tracemalloc off, both backends fired in parallel):

| metric | 1M polars-direct | **2M polars-direct** | **2M duckdb-udf** |
|---|---:|---:|---:|
| total wall | 737 s (12.3 min) | **303 s (5.0 min)** | **293 s (4.9 min)** |
| peak RSS | 8,439 MB | **7,898 MB** | **5,374 MB** |
| `auto_configure` wall | 58 s | **7.3 s** | (inside `dedupe_via_udf`) |
| `run_dedupe` / `dedupe_via_udf` wall | 676 s | 293 s | 291 s |
| clusters output | 836,154 (full dict) | 1,971,064 (full dict) | **2,570 (golden only)** |

### 2M is FASTER than 1M. That can't be honest scaling.

The cluster counts tell the real story:

- **1M polars-direct**: 836,154 clusters from 1M rows. With a 15% dupe rate, ~850K base records are expected after perfect dedupe. 836K is very close to perfect — the controller picked a config that actually merges duplicates.
- **2M polars-direct**: **1,971,064 clusters from 2M rows.** Expected after perfect dedupe: ~1,700K. **97% of rows are still singletons** — the controller picked a config that barely merges anything.
- **2M duckdb-udf**: 2,570 golden records (multi-member clusters only) at 2M, vs **145,352 at 1M**. **57× fewer multi-member clusters** at double the row count. Same finding from a different output semantic.

The "fast" 2M wall is "fast" because the pipeline isn't doing the work. `auto_configure` dropped from 58 s at 1M to 7.3 s at 2M — an 8× speedup in the iteration loop strongly suggests the controller is bailing on the first iteration with a no-op or near-no-op config.

### What this means for the OOM-ceiling question

**Unanswered.** Both 2M runs completed because they did nearly no work, not because the pipeline scaled. A fair scaling test needs an *explicit* config that does the same scoring work at 2M as at 1M — bypassing the auto-config controller that's currently producing a degenerate config at this row count.

The original hypothesis (UDF and polars-direct share the same OOM ceiling because both materialise the full table into Polars) is unfalsified. The 2M data doesn't prove or disprove it; it proves something else.

### What this means for goldenmatch

**Possibly the most important finding from the whole DuckDB exploration.** Independent of any DuckDB question, the auto-config controller has a scale-dependent failure mode that produces drastically worse output at 2M than at 1M. The CLAUDE.md note about controller gating ("Auto-config learned blocking: gated at `total_rows >= 50_000` in `autoconfig.py`. Sample size capped at `min(total_rows // 4, 5000)`") doesn't mention any 1M+ gate, but something is clearly changing.

Possible mechanisms (all need verification):

1. The sample size is capped at 5,000 — so at 2M the sample is 0.25% of the data, vs 0.5% at 1M. The learned-blocking predicate evaluator may be tripping a threshold that picks a less-aggressive blocking key at the smaller relative sample.
2. The controller's `pick_committed()` policy may be selecting a "RED config that runs fast" at 2M because all candidate configs look bad on the sample.
3. The synthetic fixture's seed produces a different distribution at 2M that fools the auto-config heuristics.
4. There's an explicit row-count guard somewhere we haven't found.

This belongs as a goldenmatch issue, not a DuckDB doc footnote. **Filed as follow-up.** DuckDB measurement is on hold until this is understood — measuring storage backends on a pipeline that isn't doing the work isn't a useful comparison.

### Next concrete steps (not in this PR)

1. **Reproduce with explicit config**: re-fire 2M polars-direct with an explicit `MatchkeyConfig` that mirrors what the controller committed at 1M. If wall + cluster_count look like proper-scaling extrapolations from 1M, the bug is in the controller's scale-dependent behaviour. If they don't, something else is going on.
2. **Inspect the committed config**: `auto_configure_df` returns the config; dump it for both 1M and 2M runs and diff. The diff is the bug.
3. **Decide whether to chase this in the DuckDB workstream or kick it out**: arguably this is a goldenmatch core controller issue and the DuckDB exploration should pause until it's resolved.
