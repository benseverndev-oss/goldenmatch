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
1. **Name heuristic** (`_match_by_name`): hints ending with `_` = prefix-only match (NOT substring); hints starting with `_` = suffix-only match; all others = substring match. First match wins. Dict iteration order determines priority.
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

Pass `custom_types_path` to `load_type_defs()`. User types replace same-name base types; new names are added.

## Domain Packs

`goldencheck/semantic/domains/{healthcare,finance,ecommerce}.yaml` — YAML files with `description` + `types` keys.
`load_type_defs(domain="healthcare")` loads base → domain → user with priority ordering.
`classify_columns(df, type_defs=preloaded)` accepts preloaded type_defs to avoid double-loading in scanner.
`list_available_domains()` scans the `domains/` directory for `.yaml` files.

## Gotchas

- Suppression runs **before** corroboration boost in the scanner pipeline — a boosted finding was suppressed based on pre-boost confidence
- The `(suppressed: ...)` suffix is stripped by `llm/merger.py` before appending LLM annotations
- `format_match: "date"` only requires 50% match (lower than email/phone at 70%)
- `pattern_consistency` suppression checks `Finding.metadata` for pattern lengths — if patterns differ in length by >1, suppression is skipped (catches zip code format issues)
