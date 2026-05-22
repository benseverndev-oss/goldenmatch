# Golden rules intelligence layer 2 (v1.18.1)

**Status:** spec → implementing in v1.18.1
**Date:** 2026-05-22
**Predecessor:** v1.18.0 (intelligent rules + custom plugin slot)

## Problem

The v1.18.0 refiner has 8 heuristic rules + compliance/identity/sibling-timestamp pre-rules. It picks reasonable strategies but is still shallow on four fronts:

1. **Source priority uses completeness as the only quality signal.** A source that's "complete but wrong" (auto-filled with junk) outranks a sparse-but-accurate source. Completeness ≠ quality.
2. **No learning from past corrections.** MemoryStore tracks user overrides, but the refiner ignores them. Same picks each run regardless of feedback.
3. **One rule per field across all clusters.** A weak cluster gets the same strategy as a strong cluster, when defensive picks would be better for weak / oversized clusters.
4. **Ambiguous fields get the fallback default.** When no rule fires (no compliance match, moderate spread, average everything), the refiner just defaults to `most_complete` — leaving real intelligence on the table for unusual datasets that don't fit any heuristic.

## Four lifts

### 1. Per-source consensus agreement (replaces completeness-only ranking)

Today's rule 2 ranks sources by `non_null_rate` and picks the top as priority. Replace with **agreement-with-consensus**:

```
for each multi-member cluster:
    for each field:
        consensus_value = mode of non-null cluster values (mode beats first_seen)
        for each source in cluster:
            agreement[source][field] += 1 if source_value == consensus_value else 0
            attempts[source][field] += 1 if source_value is not None else 0

agreement_rate[source][field] = agreement[source][field] / max(attempts[source][field], 1)
```

Use `agreement_rate` instead of `completeness` for `source_priority` ranking. Sources with `attempts < 10` fall back to completeness (insufficient signal).

### 2. MemoryStore-learned strategy picks

New module `core/autoconfig_golden_strategy_tuner.py`. Mirrors `core/autoconfig_ne_tuner.py` shape:

```python
@dataclass(frozen=True)
class StrategyTuning:
    field: str
    strategy: str        # learned best
    n_corrections: int   # corrections that informed it
    train_hit_rate: float  # how often the picked strategy matched user choices
    heldout_hit_rate: float
    reason: str          # "learned" | "below_minimum" | "overfit_guard" | "no_memory"


def tune_field_strategy(
    store: MemoryStore | None,
    dataset: str,
    field: str,
    candidates: list[str],
) -> StrategyTuning:
    ...
```

For each correction, classify which of the candidate strategies WOULD have produced the user's chosen value. Strategy with highest hit rate wins. 90/10 train/heldout split; 5pp drop → overfit guard reverts to defaults.

Gated on `>= 50` corrections per dataset (same as #129 NE tuner). Env-overridable via `GOLDENMATCH_GOLDEN_TUNER_MIN_CORRECTIONS`.

Refiner consults the tuner FIRST (before any heuristic rule). Falls back to heuristics when the tuner returns `reason in {"below_minimum", "no_memory", "overfit_guard"}`.

### 3. Per-cluster strategy overrides

Today: `refine_golden_rules` returns a single `GoldenRulesConfig`. The same field-rule applies to every cluster.

Extended: refiner also returns a `cluster_overrides: dict[int, dict[str, GoldenFieldRule]]` — per-cluster, per-field rule overrides. Applied at `build_golden_record` time:

```python
def build_golden_record(
    cluster_df: pl.DataFrame,
    rules: GoldenRulesConfig,
    cluster_id: int | None = None,
    cluster_overrides: dict[int, dict[str, GoldenFieldRule]] | None = None,
    ...
):
    effective_rules = rules
    if cluster_overrides and cluster_id in cluster_overrides:
        # Apply per-cluster overrides on top of base rules.
        merged_field_rules = {**rules.field_rules, **cluster_overrides[cluster_id]}
        effective_rules = rules.model_copy(update={"field_rules": merged_field_rules})
    ...
```

Refiner sets per-cluster overrides for:
- `cluster_quality == "weak"` → `unanimous_or_null` for every field (defensive)
- `oversized` clusters → `confidence_majority` for every field (heterogeneous; trust pair scores)
- `size == 2` clusters → `unanimous_or_null` (only two members; either they agree or they don't)

### 4. LLM-assisted picks for ambiguous fields

New module `core/golden_strategy_llm.py`. Called when:
- `golden_rules.use_llm_for_ambiguous = True` (new opt-in flag, default False)
- Heuristic rules produced no winner (all rules returned None for this field)
- `LLMBudget` allows another call

LLM prompt template:
```
You are picking a golden-record consolidation strategy for a database
field. Given the column name, a sample of values, and the available
strategies, choose the best fit.

Field: {field_name}
Column type: {col_type}
Sample values (5 from random clusters):
- Cluster X: {value_1, value_2, value_3}
- Cluster Y: {value_4, value_5}
...

Available strategies: most_complete, majority_vote, first_non_null,
most_recent, source_priority, longest_value, unanimous_or_null,
confidence_majority.

Respond with ONE strategy name and a one-line rationale.
```

Parse the response; validate against `VALID_STRATEGIES`. Cache by `(dataset_signature, column_name)` so re-runs don't re-call.

Budget integration: `LLMBudget.estimate_calls(n_fields_ambiguous)` checks budget before dispatch. Soft-fail (warn + skip) on budget exhaustion.

## API additions

- `GoldenRulesConfig.use_llm_for_ambiguous: bool = False`
- `RefinementSignals.per_source_agreement: dict[str, dict[str, float]]` (replaces `per_source_completeness` for source_priority ranking; completeness still computed for the fallback path).
- `refine_golden_rules` return: tuple of (`GoldenRulesConfig`, `cluster_overrides: dict[int, dict[str, GoldenFieldRule]]`)
- `core/golden.py::build_golden_record` and `build_golden_records_batch` accept the new `cluster_overrides` kwarg.

## Tests

- **#1 consensus**: synthetic 3-source / 4-cluster fixture where source A is most complete but disagrees with consensus on 80% of fields. Refiner should pick source B for priority.
- **#2 tuner**: 50 stub corrections; tuner picks the strategy that matched 90% of corrections.
- **#3 overrides**: weak cluster + non-weak cluster in same run; weak gets `unanimous_or_null`, non-weak gets the field-level rule.
- **#4 LLM**: monkeypatch the LLM call to return "longest_value"; verify dispatch + caching + budget check.

## Shipping in v1.18.1

- #1 Per-source consensus agreement
- #2 MemoryStore-learned strategy picks

## Deferred to v1.18.2 (own PRs)

- **#3 Per-cluster strategy overrides.** Affects `merge_field` +
  `build_golden_record` + `build_golden_records_batch` signatures (~10
  call sites). Worth its own PR for review clarity.
- **#4 LLM-assisted picks for ambiguous fields.** Needs prompt design
  + measurement against benchmarks. Bundled as separate PR with own
  spec + budget integration.

Issues filed: see linked GitHub issues for the v1.18.2 follow-ups.

## Out of scope (v1.19+)

- Cluster-quality-aware ENSEMBLE picks (run two strategies, pick by confidence). Bigger refactor; current per-cluster override is enough for v1.18.1.
- LLM-assisted compliance-pattern expansion (ask LLM "is this a compliance field?" and cache new patterns).
- Cross-field correlation rules (address1+city+state as a "record" unit).

## Kill criterion

- Each of the 4 features has > 2 unit tests passing
- Existing benchmarks pass with `adaptive=False` (refiner is no-op when disabled)
- `adaptive=True` on a synthetic 3-source fixture: source-priority picks the highest-agreement source (not highest-completeness), tuner picks a learned strategy when 50 corrections fed in, weak cluster gets `unanimous_or_null` overrides, LLM call dispatched + cached for an ambiguous field.
