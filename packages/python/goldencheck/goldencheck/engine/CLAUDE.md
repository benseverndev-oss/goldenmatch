# Engine

## Scanner Pipeline Order

```
read_file_arrow(path)                   # reader.py — CSV/Parquet/Excel → pyarrow.Table (Arrow-native, no Polars)
maybe_sample(frame, max_rows=100_000)   # sampler.py — owned deterministic stride sample (3.0.0)
run COLUMN_PROFILERS per column         # 10 profilers, shared context dict
run RELATION_PROFILERS on full sample   # temporal, null_correlation, numeric_cross, age_validation
classify_columns(sample)                # semantic/classifier.py
apply_suppression(findings, ...)        # semantic/suppression.py — BEFORE boost
_post_classification_checks(...)       # digits-in-name, code-like patterns, string length format
apply learned LLM rules if available   # goldencheck_rules.json
apply_corroboration_boost(findings)     # confidence.py — AFTER suppression
sort by severity descending             # ERROR first
```

## scan_file vs scan_file_with_llm

| | `scan_file` | `scan_file_with_llm` |
|---|---|---|
| Returns | `(findings, profile)` or `(findings, profile, sample)` | `(findings, profile)` |
| `return_sample=True` | Returns 3-tuple | Called internally |
| Confidence downgrade | Caller must call `apply_confidence_downgrade` | Done inside LLM path |
| Suppression | Yes (always) | Yes (inside `scan_file`) |

After `scan_file` without LLM, always call:
```python
findings = apply_confidence_downgrade(findings, llm_boost=False)
```
The CLI's `_do_scan` does this; the `review` command does it too. Don't skip it.

## confidence.py

**Corroboration boost** (`apply_corroboration_boost`):
- 2 distinct WARNING/ERROR checks on same column → +0.1 confidence
- 3+ distinct checks → +0.2 (exclusive tiers, not cumulative)
- Capped at 1.0; only applied to WARNING/ERROR findings
- Uses `dataclasses.replace()` — originals never mutated

**Confidence downgrade** (`apply_confidence_downgrade`):
- Only runs when `llm_boost=False`
- Any WARNING/ERROR with `confidence < 0.5` → downgraded to INFO
- Appends `(low confidence — use --llm-boost to verify)` to message

## reader.py

Supported formats: `.csv`, `.parquet`, `.xlsx`, `.xls`

Two readers live here:
- **`read_file_arrow(path)`** — the default, Arrow-native reader the scan path uses
  (`scan_file` → `read_file_arrow`). Returns a `pyarrow.Table`, no Polars involved.
- **`read_file(path)`** — the legacy Polars reader (`pl.DataFrame`), still used by the
  `scan_dataframe(pl.DataFrame)` overload / `[polars]` callers. Its CSV fallback chain
  (`pl.read_csv` UTF-8 → latin-1 → `ValueError` with a `--separator`/`--quote-char` hint)
  is unchanged, but it is NOT on the default scan path anymore.

Excel raises a user-friendly `ValueError` on password-protected files.

## sampler.py

```python
maybe_sample(frame, max_rows=100_000)  # returns frame unchanged if ≤ max_rows
# 3.0.0: OWNED deterministic stride/reservoir sample over the Arrow table —
# stable across runs AND --workers, Polars-free. Replaced the old Polars PRNG
# (df.sample(n=max_rows, seed=42)); the swap is a registered accepted divergence.
```

Default `sample_size` is `100_000`. Overridable via `scan_file(path, sample_size=N)`.

`scan_file(path, deep=True)` (CLI `--deep`) bypasses sampling entirely and
profiles the full population — `sample = df` instead of `maybe_sample(...)`. Use
for exact cardinality/uniqueness/composite-key results; the native kernels
(`goldencheck[native]`) carry the CPU-bound work. Threaded through
`scan_dataframe` / `scan_file` / `scan_file_with_llm`.

## validator.py

`validate_file(path, config)` checks columns against pinned rules in `goldencheck.yml`:
- **`existence`**: column defined in rules but absent from data → WARNING
- **`required`**: `rule.required=True` and null_count > 0 → ERROR
- **`unique`**: `rule.unique=True` and duplicates exist → ERROR
- **`enum`**: values not in `rule.enum` list → ERROR
- **`range`**: numeric values outside `[lo, hi]` → ERROR

Ignored findings (from `config.ignore` list) are filtered by `(column, check)` pair.

## Gotchas

- Profiler exceptions are **caught and logged** (not re-raised) — a broken profiler won't crash the scan
- `COLUMN_PROFILERS` and `RELATION_PROFILERS` in `scanner.py` are module-level singletons — profilers must be stateless
- `validate_file` reads the **full file** (not sampled) for accurate validation counts
- The `profile` object in the return tuple is built from the full `df`, not the sample — row/column counts are always accurate

## scan_file domain parameter

`scan_file(path, domain="healthcare")` passes domain to `load_type_defs()` and `classify_columns()`.
Type defs are loaded once and shared between classifier and suppression.

## fixer.py

`apply_fixes(df, findings, mode, *, force=False) -> (DataFrame, FixReport)`. Three modes: safe, moderate, aggressive.
Aggressive requires `force=True`. Fix functions are pure (Series → Series). FixReport tracks changes per column.

- **Per-cell fixes are vectorize-guarded (perf).** `remove_invisible_chars` / `normalize_unicode` / `fix_smart_quotes` each run a Python `map_elements` over the column, but only AFTER a cheap vectorized `Series.str.contains(<class>).any()` proves there's something to fix; on a clean column they return the SAME Series object and `apply_fixes` skips the full-frame change-comparison (`if fixed is col: continue`). Byte-identical (each op is an identity when its guard misses). This is the safe-fix hot path on big clean frames — it was the scaling term in GoldenMatch's `pipeline_prep_quality_scan` (the scan itself samples to 100K and is bounded; `apply_fixes` runs on the FULL frame). Guard char-classes are built from the actual chars, NOT Python `\uXXXX` escapes (Polars' Rust regex rejects those).

## differ.py

`diff_files(old_df, new_df, old_findings, new_findings) -> DiffReport`. Compares schema, findings, stats.
Finding matching key: `(column, check)` with severity/rows comparison for worsened/improved.

## watcher.py

`watch_directory(path, interval, pattern, exit_on) -> int`. Polls with mtime tracking. SIGINT/SIGTERM graceful shutdown.
