# Engine

## Scanner Pipeline Order

```
read_file(path)                         # reader.py — CSV/Parquet/Excel → pl.DataFrame
maybe_sample(df, max_rows=100_000)      # sampler.py — deterministic seed=42
run COLUMN_PROFILERS per column         # 10 profilers, shared context dict
run RELATION_PROFILERS on full sample   # temporal, null_correlation
classify_columns(sample)                # semantic/classifier.py
apply_suppression(findings, ...)        # semantic/suppression.py — BEFORE boost
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

CSV fallback chain:
1. `pl.read_csv(path, infer_schema_length=10000)` — UTF-8
2. `pl.read_csv(path, infer_schema_length=10000, encoding="latin-1")` — Latin-1 fallback
3. Raises `ValueError` with hint about `--separator`/`--quote-char`

Excel raises a user-friendly `ValueError` on password-protected files.

## sampler.py

```python
maybe_sample(df, max_rows=100_000)  # returns df unchanged if ≤ max_rows
# uses df.sample(n=max_rows, seed=42) — deterministic, Polars native
```

Default `sample_size` is `100_000`. Overridable via `scan_file(path, sample_size=N)`.

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
