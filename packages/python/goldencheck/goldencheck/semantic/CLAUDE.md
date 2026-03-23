# Semantic

## What This Module Does

Classifies each column's semantic type (email, phone, date, etc.) and suppresses irrelevant findings based on that type.

## types.yaml Format

```yaml
types:
  email:
    name_hints: ["email", "e_mail", "_email"]   # substring/prefix_/suffix_ matches
    value_signals:
      format_match: "email"      # regex-based: ≥70% match "@.*\."
      min_match_pct: 0.70
    suppress: ["uniqueness", "cardinality"]
  phone:
    name_hints: ["phone", "mobile", "tel"]
    value_signals:
      format_match: "phone"      # regex: \d{3}.*\d{3}.*\d{4}, ≥70%
    suppress: ["pattern_consistency"]
```

Supported `value_signals` keys: `min_unique_pct`, `max_unique`, `format_match`, `min_match_pct`, `mixed_case`, `avg_length_min`, `numeric`, `short_strings`. All signals in a type must be satisfied (AND logic).

## Classifier Logic

`classify_columns(df)` runs per column:
1. **Name heuristic** (`_match_by_name`): substring match; hint ending with `_` = prefix match; starting with `_` = suffix match. First match wins.
2. **Value fallback** (`_match_by_value`): iterates type_defs, checks all `value_signals`. First match wins.
3. Returns `ColumnClassification(type_name, source)` where `source` is `"name"`, `"value"`, or `"none"`.

## Key Dataclasses

```python
@dataclass
class TypeDef:
    name_hints: list[str]
    value_signals: dict[str, Any]
    suppress: list[str]          # check names to downgrade to INFO

@dataclass
class ColumnClassification:
    type_name: str | None
    source: str                  # "name" | "value" | "llm" | "none"
```

## Suppression Rules

`apply_suppression(findings, column_types, type_defs)` in `suppression.py`:
- Only suppresses `WARNING`/`ERROR` (never `INFO`)
- Never suppresses findings with `source == "llm"`
- Never suppresses findings with `confidence >= 0.9`
- Matching check in `type_def.suppress` → downgrade to `INFO`, append `(suppressed: <type> column)` to message

## User-Defined Types

Pass `custom_types_path` to `load_type_defs()`. User types in `goldencheck.yml` replace same-name base types; new names are added. Loaded after base `types.yaml`, so user entries win on collision.

## Gotchas

- Suppression runs **before** corroboration boost in the scanner pipeline — a boosted finding was suppressed based on pre-boost confidence
- The `(suppressed: ...)` suffix is stripped by `llm/merger.py` before appending LLM annotations
- `format_match: "date"` only requires 50% match (lower than email/phone at 70%)
