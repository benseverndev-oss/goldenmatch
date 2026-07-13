# Splink -> GoldenMatch config converter (+ N-level probabilistic fields)

**Date:** 2026-07-13
**Status:** Approved (design)
**Thesis phase:** Python POC (Rust/arrow-native port and TS surface are later phases, out of scope here)

## Problem

Splink users migrating to GoldenMatch must hand-translate their settings (comparisons, blocking rules, trained m/u probabilities) into a `GoldenMatchConfig`. There is no importer. Additionally, GoldenMatch's probabilistic fields support only 2/3 comparison levels, so real Splink comparisons (commonly 4-6 levels) and trained-model m/u probabilities cannot be represented faithfully today.

## Decisions (from brainstorming)

- **Input scope:** accept BOTH bare settings dicts/JSON and saved trained Splink model JSON (importing m/u into an `EMResult` so no retraining is needed).
- **Scope:** converter + extend GoldenMatch probabilistic fields from 2/3 to N levels. Other gaps (raw-SQL blocking, date/array/geo comparisons, per-level TF weights) stay best-effort.
- **Fidelity:** best-effort + structured `ConversionReport`; `strict=True` raises on any lossy mapping.
- **Surfaces (POC):** library API + CLI command + MCP tool.
- **Success bar:** bakeoff parity — converted config within F1 0.05 of native Splink on the `bench_er_headtohead` harness, plus unit tests.

## Known gap map (context)

Converts cleanly: `ExactMatch`, `JaroWinklerAtThresholds`, `LevenshteinAtThresholds` (distance->similarity lossy), `JaccardAtThresholds`, `block_on` column/substr rules, `em_convergence`/`max_iterations`, `unique_id_column_name`, trained m/u (once N-level lands).

Cannot convert (warn/drop): arbitrary-SQL blocking (arithmetic, ranges, asymmetric l/r), cross-column comparison levels (`ColumnsReversedLevel`, full_name-inside-name), date/array/geo comparisons (`DateOfBirthComparison`, `ArrayIntersect`, `DistanceInKM`), per-level `tf_adjustment_column`/`tf_adjustment_weight` (collapses to field-level `tf_adjustment: bool`), `link_and_dedupe` (recipe note).

## Architecture

Two stages, one feature arc, pure Python.

### Stage 1: N-level probabilistic fields (core extension)

- `MatchkeyField` (`config/schemas.py`) gains `level_thresholds: list[float] | None` — descending similarity cutoffs defining N-1 agree-bands plus the implicit disagree level. `levels: int` accepts any N >= 2.
- **Back-compat:** `levels=3, partial_threshold=0.8` is exactly the `level_thresholds=[1.0, 0.8]` special case; existing configs and tests are untouched.
- `core/probabilistic.py`: `comparison_vector` bands similarity into a level index via thresholds; `train_em`, `compute_thresholds`, `EMResult` already store per-level lists (`m_probs: dict[str, list[float]]`) — generalize loops that assume length 2/3, no data-model change.
- **Native-kernel guard:** if the native/fused scoring path assumes 2/3 levels, N>3 matchkeys route to the pure-Python probabilistic path (existing fallback pattern). Verify during implementation.

### Stage 2: converter

New module `goldenmatch/config/from_splink.py`:

```python
def from_splink(source: dict | str | Path, *, strict: bool = False) -> SplinkConversion

@dataclass
class SplinkConversion:
    config: GoldenMatchConfig
    report: ConversionReport
    em_model: EMResult | None   # present when input was a trained model
```

**Input detection:** dict or JSON path; levels carrying `m_probability`/`u_probability` => trained model, else bare settings.

**Comparisons -> MatchkeyFields.** Serialized Splink JSON carries no class names; each level is a raw `sql_condition` string. The converter is a table of anchored-regex level recognizers:

| Splink level SQL | Recognized as |
|---|---|
| `"col_l" IS NULL OR "col_r" IS NULL` (`is_null_level`) | null level (GoldenMatch handles nulls natively; skipped, no warning) |
| `"col_l" = "col_r"` | exact band (threshold 1.0) |
| `jaro_winkler_similarity(...) >= t` / `jaro_similarity(...)` | `jaro_winkler` band at `t` |
| `levenshtein(...) <= n` / `damerau_levenshtein(...)` | `levenshtein` band, distance->similarity converted, WARN (lossy) |
| `jaccard(...) >= t` | `jaccard` band at `t` |
| `ELSE` | disagree level |

Recognizers must handle DuckDB and Spark dialect spellings of the same function.

A comparison whose recognized bands share one scorer family (exact counts as any family at threshold 1.0) becomes one `MatchkeyField(scorer=family, levels=N, level_thresholds=[...])`. Mixed families, cross-column levels, unrecognized SQL: drop that LEVEL + warn; whole comparison unmappable: drop + warn. All fields join a single `MatchkeyConfig(type="probabilistic")`.

**Blocking -> BlockingConfig.** Recognize conjunctions of column-equality and `SUBSTR(col,a,b)`-equality (-> `substring` transform). One rule -> one `BlockingKeyConfig`; multiple rules -> `strategy: multi_pass` passes. Arithmetic/range/asymmetric rules: drop + warn. ALL rules dropped: ERROR (probabilistic matchkeys require blocking; config would be invalid).

**Trained-model import.** Per-level `m_probability`/`u_probability` copy 1:1 into `EMResult` (exact under N-level); `probability_two_random_records_match` -> `proportion_matched`; match weights recomputed `log2(m/u)`. Converter writes the model JSON and sets `model_path` on the matchkey so GoldenMatch skips EM training. TF: per-level `tf_adjustment_column` -> field `tf_adjustment=True`; warn on weight != 1.0 or column override.

**Settings scalars.** `em_convergence` -> `convergence_threshold`; `max_iterations` -> `em_iterations`; `unique_id_column_name` -> `id_column`; `link_type` -> report note (dedupe vs match entry; `link_and_dedupe` = warning + recipe note); `sql_dialect`/`retain_*`/prefixes -> ignored, info-level note.

## Surfaces

- **Library:** `from_splink` exported via `_api.py` + `__init__.py`, sibling of `load_config`.
- **CLI:** new `cli/import_splink.py` -> `goldenmatch import-splink settings.json -o goldenmatch.yaml [--model-out model.json] [--strict]`; prints report table; exit 1 on error-severity findings. Wire into `cli/main.py`.
- **MCP:** `convert_splink_config` tool (settings JSON inline -> config YAML + report), existing server tool pattern.

## Error handling / ConversionReport

`ConversionReport` = list of findings `{severity: info|warning|error, splink_path, message, mapped_to}` + `summary()`. Default mode always returns a Pydantic-validated `GoldenMatchConfig` unless an error-severity finding makes that impossible (zero surviving blocking rules or zero surviving comparisons). `strict=True` raises `SplinkConversionError` on first warning-or-worse.

## Testing

1. **Recognizer unit tests:** table-driven per SQL shape, DuckDB + Spark spellings.
2. **Golden-file tests:** settings JSON -> expected config dict + expected findings; trained-model fixture -> exact m/u copy (parity to 1e-9; it is a copy, not a fit).
3. **N-level EM tests:** existing 2/3-level tests untouched + green; new banding and EM-convergence tests at N=4/5.
4. **Bakeoff parity gate (success bar):** convert the settings from `scripts/bench_er_headtohead/run_splink.py`, run converted-GoldenMatch vs native Splink on a bakeoff dataset, require F1 delta <= 0.05. Script via existing harness, not CI.

## Out of scope

- Rust/arrow-native port of N-level scoring and the converter (thesis phase 2).
- TS/WASM surface (thesis phase 3).
- New scorers for dates/arrays/geo; richer blocking expressions.
- Reverse direction (GoldenMatch -> Splink).
