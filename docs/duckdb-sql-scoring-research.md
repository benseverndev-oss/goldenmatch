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
