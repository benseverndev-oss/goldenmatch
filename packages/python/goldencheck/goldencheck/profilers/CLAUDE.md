# Profilers

## BaseProfiler Interface

```python
class BaseProfiler(ABC):
    def profile(self, df: pl.DataFrame, column: str, *, context: dict | None = None) -> list[Finding]:
        ...
```

- `df` is always the **sampled** DataFrame (up to 100k rows), not the full file
- `context` is a shared mutable dict passed to every profiler per column — use it to share intermediate results (e.g., TypeInferenceProfiler writes inferred type; others read it)
- Return `[]` if nothing to report — never raise, log instead

## The 10 Column Profilers (in execution order)

| Class | Check name | What it flags |
|---|---|---|
| `TypeInferenceProfiler` | `type_inference` | Column stored as wrong dtype (e.g., zip as Int) |
| `NullabilityProfiler` | `nullability` | High null rate |
| `UniquenessProfiler` | `uniqueness` | Duplicate values in likely-unique columns |
| `FormatDetectionProfiler` | `format_detection` | Email/phone/URL format violations |
| `RangeDistributionProfiler` | `range_distribution` | Outliers, impossible values |
| `CardinalityProfiler` | `cardinality` | Enum violations, unexpected categories |
| `PatternConsistencyProfiler` | `pattern_consistency` | Inconsistent formats within column |
| `EncodingDetectionProfiler` | `encoding_detection` | Zero-width Unicode, smart quotes, Latin-1 in UTF-8 |
| `SequenceDetectionProfiler` | `sequence_detection` | Gaps in sequential numbering |
| `DriftDetectionProfiler` | `drift_detection` | Distribution drift, new categories over time |

Plus 2 relation profilers in `goldencheck/relations/`: `TemporalOrderProfiler` (`temporal_order`) and `NullCorrelationProfiler` — these call `profiler.profile(sample)` with no column arg.

## Adding a New Profiler

1. Create `goldencheck/profilers/my_check.py` subclassing `BaseProfiler`
2. Set a stable `check` name (matches the check names in `llm/prompts.py`)
3. Import and add instance to `COLUMN_PROFILERS` list in `engine/scanner.py`
4. Write tests in `tests/profilers/test_my_check.py`

## Confidence Values

- Start at `1.0` for deterministic checks (e.g., null count)
- Use `0.7-0.8` for heuristic checks (e.g., format guessing)
- Use `0.5-0.6` for weak signals (e.g., pattern inconsistency with small sample)
- `apply_corroboration_boost` in `engine/confidence.py` adds +0.1 (2 checks) or +0.2 (3+ checks) on same column

## Gotchas

- Profilers run on the **sample**, not the full df — don't report `affected_rows` as absolute counts without noting this
- `context` dict is shared across all profilers for a given column; write with namespaced keys (e.g., `context["type_inference.inferred"] = "email"`)
- Relation profilers receive the full sample DataFrame, not a single column
