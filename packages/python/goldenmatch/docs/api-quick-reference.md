# goldenmatch API Quick Reference

Practical examples for the most-used surface. Authoritative type signatures live in `goldenmatch/_api.py` and `goldenmatch/config/schemas.py`.

## `dedupe_df()` — DataFrame deduplication

```python
import goldenmatch

result = goldenmatch.dedupe_df(
    df,
    config=None,              # GoldenMatchConfig or None
    exact=["email"],          # exact match columns
    fuzzy={"name": 0.85},     # fuzzy match with thresholds
    blocking=["zip"],         # blocking keys
    threshold=0.85,           # overall fuzzy threshold
    llm_scorer=False,         # enable LLM for borderline pairs
)
```

## `DedupeResult` fields

```python
result.golden          # pl.DataFrame | None - canonical records with __cluster_id__
result.dupes           # pl.DataFrame | None - duplicate records with __row_id__
result.unique          # pl.DataFrame | None - non-duplicate records
result.clusters        # dict[int, dict] - {cluster_id: {"members": [row_ids], "pair_scores": {(a,b): score}}}
result.scored_pairs    # list[tuple[int, int, float]] - all matched pairs
result.stats           # dict - total_records, total_clusters, matched_records, match_rate
result.total_records   # int
result.total_clusters  # int
result.match_rate      # float
```

## `StandardizationConfig` - use rules dict, NOT keyword args

```python
# WRONG:
StandardizationConfig(email=["email"], phone=["phone"])

# RIGHT:
StandardizationConfig(rules={
    "email": ["email"],
    "phone": ["phone"],
    "first_name": ["strip", "name_proper"],
})
```

`StandardizationConfig` has a single `rules: dict[str, list[str]]` field with a model validator. Keyword args will raise a Pydantic validation error.

## `BlockingConfig` requires `keys` field

```python
# keys is required even with multi_pass
BlockingConfig(
    strategy="multi_pass",
    keys=[BlockingKeyConfig(fields=["email"], transforms=["lowercase"])],  # required!
    passes=[
        BlockingKeyConfig(fields=["email"], transforms=["lowercase"]),
        BlockingKeyConfig(fields=["last_name"], transforms=["soundex"]),
    ],
)
```

## `MatchkeyConfig` requires `name` field

```python
MatchkeyConfig(
    name="identity",       # required!
    type="weighted",
    threshold=0.75,
    fields=[...],
)
```

## Extracting pairs from clusters (correct way)

```python
pairs = []
for cluster in result.clusters.values():
    members = sorted(cluster["members"])
    for i in range(len(members)):
        for j in range(i + 1, len(members)):
            pairs.append((members[i], members[j]))
```

## Multi-pass blocking for catching different dupe types

```python
# Pass 1: exact email (identical-email dupes)
# Pass 2: soundex last_name (phonetic variants: Smith/Smyth)
# Pass 3: first 3 chars of last_name (typo dupes: Johm/John)
BlockingConfig(
    strategy="multi_pass",
    keys=[BlockingKeyConfig(fields=["email"], transforms=["lowercase", "strip"])],
    passes=[
        BlockingKeyConfig(fields=["email"], transforms=["lowercase", "strip"]),
        BlockingKeyConfig(fields=["last_name"], transforms=["soundex"]),
        BlockingKeyConfig(fields=["last_name"], transforms=["substring:0:3"]),
    ],
)
```

## Available scorers

- `exact`: 1.0 if equal, 0.0 otherwise
- `jaro_winkler`: best for short strings (names)
- `levenshtein`: normalized edit distance
- `token_sort`: handles word reordering
- `ensemble`: weighted combination of jaro_winkler + levenshtein + token_sort + dice (best for names)
- `dice`, `jaccard`: set-based similarity
- `soundex_match`: phonetic matching
- `embedding`: sentence-transformer cosine similarity

## Available transforms (applied at matchkey time)

`lowercase`, `uppercase`, `strip`, `strip_all`, `soundex`, `metaphone`, `digits_only`, `alpha_only`, `normalize_whitespace`, `token_sort`, `first_token`, `last_token`, `substring:start:end`, `qgram:n`

## LLM Scorer for borderline pairs

```python
from goldenmatch.config.schemas import LLMScorerConfig, BudgetConfig

config.llm_scorer = LLMScorerConfig(
    enabled=True,
    candidate_lo=0.60,    # send pairs scoring 0.60-0.90 to LLM
    candidate_hi=0.90,
    auto_threshold=0.90,  # auto-accept above 0.90
    budget=BudgetConfig(max_calls=500, max_cost_usd=1.0),
)
# Requires OPENAI_API_KEY or ANTHROPIC_API_KEY in environment
```

## Common Mistakes

- Using `exact=["email"]` as sole matchkey - creates oversized clusters with common emails
- Using `auto_configure()` on synthetic data - it may produce poor configs
- Not setting `name=` on MatchkeyConfig - it's required
- Not providing `keys=` on BlockingConfig - it's required even with multi_pass
- Extracting pairs from dupes DataFrame directly instead of using result.clusters
