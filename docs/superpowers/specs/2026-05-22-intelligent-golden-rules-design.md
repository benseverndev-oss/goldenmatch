# Intelligent golden rules (post-cluster auto-config)

**Status:** spec → implemented in v1.18.0
**Date:** 2026-05-22

## Problem

Three holes in golden-field consolidation today:

1. **Strategy palette is thin.** `most_complete` / `majority_vote` / `first_non_null` / `most_recent` / `source_priority`. Missing:
   - **`longest_value`** — useful for free-text fields (address line, description) where length proxies completeness.
   - **`unanimous_or_null`** — compliance use case: if any cluster member disagrees, emit NULL so downstream consumers don't quietly accept a chosen-by-heuristic value.
   - **`confidence_majority`** — like `majority_vote` but weighted by pair-score confidence inside the cluster. The clustering already computes `pair_scores` and per-cluster `confidence`; consolidator currently ignores them.
2. **Auto-config picks `most_complete` for everything.** Real datasets have date columns where `most_recent` is clearly right, multi-source feeds where one source is consistently the highest-quality, free-text columns where `longest_value` beats `most_complete`. The signal exists in `ColumnProfile` (col_type, null_rate, avg_len) AND in the cluster output (within-cluster spread, per-source completeness) — neither is consulted.
3. **No cluster-informed refinement.** Auto-config picks rules BEFORE clustering runs. But the right per-field rule depends on cluster shape (within-cluster value spread tells you whether `most_complete` or `confidence_majority` is right; per-source completeness ranking tells you the right `source_priority` order).

The architectural insight (Ben, 2026-05-22): matchkey + blocking auto-config MUST happen pre-cluster (clustering depends on them). But **golden-rules auto-config should run AFTER clusters exist** — that's when the data informing per-field strategy is available.

## Decision

Two-phase auto-config:

- **Phase 1 (pre-cluster)**: matchkey + blocking + standardization. Unchanged from today.
- **Phase 2 (post-cluster, NEW)**: golden-rules refinement. Reads cluster output + per-column profiles + the prepared frame; emits a refined `GoldenRulesConfig` that overrides the v0 default.

Phase 2 is **opt-in via `GoldenRulesConfig.adaptive: bool = False`**. Default off in v1.18.0 to avoid silently changing behavior on existing benchmarks. Documented as the recommended setting for v1.18+ users; default flip to `True` is a v1.19 candidate after benchmark validation.

## New strategies (v1.18.0)

### `longest_value`

Pick the longest non-null string from cluster members. Quality-weighted tie-break.

**When to use**: free-text columns where length correlates with completeness (full address vs abbreviated, full company name vs ticker, description vs short label). Auto-config picks it when `col_type in ("string", "address", "description")` AND `avg_len > 20`.

### `unanimous_or_null`

If every non-null cluster member has the same value → emit that value. If any member disagrees → emit NULL (or NaN for numeric types). NULL members are ignored (don't count as disagreement; absence is not contradiction).

**When to use**: compliance-grade fields where a heuristic-chosen value is worse than a missing value (medical IDs, license numbers, SSN-shaped columns). Auto-config does NOT pick this — too aggressive for general use; opt-in per field only.

### `confidence_majority`

Majority vote weighted by per-pair scores within the cluster. For each candidate value, sum the pair_scores of edges where both endpoints have that value; pick the highest-sum value.

**Why it's better than `majority_vote`**: vanilla majority counts every member equally. A 5-member cluster where 3 members agree on "A" but the agreeing edges are all weak (score 0.55, 0.62, 0.51) loses to the 2 strong-edge members on "B" (scores 0.91, 0.88). Confidence-majority surfaces the consensus the clustering itself trusts.

**When to use**: high-cardinality identity fields (names, addresses) where some cluster members are weakly linked. Auto-config picks it when within-cluster `block_sizes_p99 / p50 > 5` (heterogeneous cluster sizes signal weak-edge presence).

## `GoldenRulesRefiner` (`core/golden_rules_refiner.py`)

```python
@dataclass
class RefinementSignals:
    """Per-field signals computed from clusters + column profiles."""
    within_cluster_spread: dict[str, float]  # field -> avg distinct/cluster
    per_source_completeness: dict[str, dict[str, float]]  # field -> source -> non-null rate
    date_column_coverage: dict[str, float]  # field -> fraction of clusters where every member has a date
    col_type: dict[str, str]
    avg_len: dict[str, float]
    null_rate: dict[str, float]


def refine_golden_rules(
    base_rules: GoldenRulesConfig,
    clusters: dict[int, dict],
    prepared_df: pl.DataFrame,
    column_profiles: list[ColumnProfile],
) -> GoldenRulesConfig:
    """Refine golden_rules based on cluster + column signals.

    Returns a NEW GoldenRulesConfig with field_rules populated. Does
    NOT mutate base_rules. When base_rules.adaptive is False, returns
    base_rules unchanged.
    """
```

### Rule table (applied per-field, first match wins)

| Condition | Picked strategy | Reason |
|---|---|---|
| `col_type == "date"` AND timestamp-shaped values | `most_recent` (`date_column=self`) | Direct date sort |
| `__source__` in df AND `per_source_completeness[field]` has clear ranking (top source > 1.5× median) | `source_priority` (ranked by completeness desc) | One source dominates |
| `col_type in ("string", "address", "description")` AND `avg_len > 20` AND `within_cluster_spread[field] > 1.5` | `longest_value` | Free-text, members disagree |
| `null_rate[field] > 0.5` | `first_non_null` | Mostly absent; fast path |
| `within_cluster_spread[field] > 2.0` (high disagreement) | `confidence_majority` | Trust the clustering's confidence |
| Else | `most_complete` | Default fallback |

## Implementation

**Files added:**
- `core/golden_rules_refiner.py` — `refine_golden_rules` + `compute_refinement_signals`.

**Files changed:**
- `core/golden.py` — `_longest_value`, `_unanimous_or_null`, `_confidence_majority` strategy functions; `consolidate_field` dispatch updated.
- `config/schemas.py` — `VALID_STRATEGIES` += `{"longest_value", "unanimous_or_null", "confidence_majority"}`; `GoldenRulesConfig.adaptive: bool = False`.
- `core/pipeline.py` — when `config.golden_rules.adaptive`, call refiner between `build_clusters` and `build_golden_records`.

## Pipeline integration

```python
# In _finalize / _run_dedupe_pipeline:
clusters = build_clusters(pairs, all_ids, max_cluster_size=..)
if config.golden_rules and config.golden_rules.adaptive:
    refined_rules = refine_golden_rules(
        base_rules=config.golden_rules,
        clusters=clusters,
        prepared_df=prepared_df,
        column_profiles=profiles,
    )
    effective_rules = refined_rules
else:
    effective_rules = config.golden_rules
golden_df = build_golden_records_batch(..., golden_rules=effective_rules)
```

## Tests

**New strategies (`tests/test_golden_strategies.py`):**
- `test_longest_value_picks_longest_non_null`
- `test_longest_value_ties_break_by_quality`
- `test_unanimous_or_null_emits_value_when_all_agree`
- `test_unanimous_or_null_emits_null_when_any_disagree`
- `test_unanimous_or_null_ignores_null_members`
- `test_confidence_majority_overrides_count_majority_on_weak_edges`
- `test_confidence_majority_falls_back_to_count_when_no_pair_scores`

**Refiner (`tests/test_golden_rules_refiner.py`):**
- `test_refiner_picks_most_recent_for_date_column`
- `test_refiner_picks_source_priority_when_one_source_dominates`
- `test_refiner_picks_longest_value_for_free_text`
- `test_refiner_picks_first_non_null_for_sparse_column`
- `test_refiner_picks_confidence_majority_on_high_spread`
- `test_refiner_returns_base_rules_when_adaptive_false`
- `test_refiner_is_pure_does_not_mutate_base`

## Kill criterion

- 3 new strategies + refiner integration pass `uv run ruff check` + new unit tests
- Existing benchmark suite (DBLP-ACM / Febrl3 / NCVR / DQbench T1-T3) does not regress F1 when `adaptive=False` (the default — refiner is a no-op)
- On a multi-source synthetic fixture (3 sources, one consistently higher completeness), `adaptive=True` picks `source_priority` ranked correctly

## Out of scope (v1.19+ candidates)

- **Custom plugin slot** (`strategy="custom:my_rule"`). Architecturally load-bearing — deserves its own spec + PR. The `GoldenStrategy` protocol shape, the lookup mechanism (entry points vs explicit register), and the failure mode (what happens when the plugin raises) all need decisions before code.
- **Default flip**: `adaptive=True` as the default for new configs once benchmarks confirm no F1 regression.
- **Per-cluster strategy override**: rules that vary per-cluster based on cluster health (`weak` clusters use `unanimous_or_null` defensively). Possible but adds substantial complexity.
- **LLM-assisted refinement** for ambiguous fields. Out of scope; v1.18 is heuristics-only.
