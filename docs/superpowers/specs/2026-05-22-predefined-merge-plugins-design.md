# Predefined golden-strategy plugins

**Status:** spec → implementing in v1.18.2
**Date:** 2026-05-22
**Predecessor:** v1.18.0 custom plugin slot (#golden-strategy-plugins)

## Problem

v1.18.0 shipped the custom-plugin slot (`strategy="custom:<name>"`) with
zero built-in plugins -- by design. Users wanting common patterns like
"pick max numeric value" or "lowercase + dedupe emails" had to write
their own. Two pain points:

1. **Numeric aggregates are absent from the built-in strategies.** All
   8 built-ins (most_complete, majority_vote, first_non_null,
   most_recent, source_priority, longest_value, unanimous_or_null,
   confidence_majority) treat values as opaque strings. A column like
   `lifetime_value` or `account_balance` has no built-in "pick the
   max" option.
2. **Format-canonical merges are operator favorites that get
   reimplemented constantly.** "Pick the lowercased / plus-stripped
   email" or "pick the most-digits phone" or "pick the value from
   our authoritative CRM source" come up in every customer
   integration. Shipping these as named plugins means the YAML
   config carries the intent (`strategy: "custom:email_normalize"`)
   instead of a custom Python module.

## Decision

Ship **10 predefined plugins** in three categories, auto-registered
when `PluginRegistry.discover()` runs:

### Numeric aggregates (3)

- **`numeric_max`** -- largest numeric value. Ignores non-numeric.
- **`numeric_min`** -- smallest numeric value.
- **`numeric_mean`** -- arithmetic mean of numeric values.

### Format-canonical (4)

- **`shortest_value`** -- shortest non-null string. Useful for codes
  / identifiers where shorter usually means more canonical.
- **`concat_unique`** -- comma-separated join of unique non-null
  values (sorted). Useful for tags / categories / multi-select fields.
- **`email_normalize`** -- lowercase + strip plus-addressing; pick
  the mode (most common normalized form).
- **`phone_digits_only`** -- strip formatting; pick the value with
  the most digits (favors full international format over local).

### Business-shaped (3)

- **`system_of_record`** -- pick value from authoritative source
  per `rule_kwargs.source_priority`. Same as built-in `source_priority`
  but with explicit "system of record" semantic naming for the YAML
  intent. Falls back to first non-null when no priority source has
  a value.
- **`lifecycle_stage`** -- pick the most-advanced lifecycle value.
  Default order (lowest -> highest): `subscriber`, `lead`, `mql`,
  `sql`, `opportunity`, `customer`, `evangelist`. Override via
  `rule_kwargs.lifecycle_order` (list of stage names).
- **`freshness_with_max_age`** -- like `most_recent` but emits NULL
  if no value is fresher than `rule_kwargs.max_age_days` (default 365).
  Compliance / data-freshness use case.

## Discovery

`PluginRegistry.discover()` auto-registers builtins BEFORE scanning
entry points. Order matters: a user's entry-point plugin with the
same name as a builtin wins (entry-point registration overwrites
the builtin in `_register()`).

`goldenmatch/plugins/builtin/__init__.py` exposes `_register_builtins(registry)`
which is called at the top of `discover()`. Discovery is idempotent
via the existing `_discovered` flag.

## API surface

All 10 plugins satisfy the `GoldenStrategyPlugin` protocol from
v1.18.0:

```python
class NumericMaxStrategy:
    name = "numeric_max"
    def merge(self, values, *, sources=None, dates=None,
              quality_weights=None, pair_scores=None,
              rule_kwargs=None):
        ...
```

User opts in via:
```yaml
golden_rules:
  field_rules:
    lifetime_value:
      strategy: "custom:numeric_max"
    email:
      strategy: "custom:email_normalize"
    crm_status:
      strategy: "custom:system_of_record"
      source_priority: ["salesforce", "hubspot"]
    last_seen_at:
      strategy: "custom:freshness_with_max_age"
      date_column: "last_seen_at"
```

`rule_kwargs` carries `source_priority` / `lifecycle_order` /
`max_age_days` -- the dispatcher in `core/golden.py::merge_field`
already passes the GoldenFieldRule's model_dump.

## Confidence semantics

- Single non-null candidate: 1.0
- Multiple candidates, clear winner (e.g. unique max): 1.0
- Tied or ambiguous: 0.7 (mirrors the built-in `most_complete`
  tie-break confidence)
- Format-canonical mode-pick: `count / total` (matches
  `majority_vote`)
- `freshness_with_max_age` emits NULL when all values stale:
  confidence 0.0
- `unanimous_or_null` semantics for compliance plugins: emit NULL
  with confidence 0.0 when no winner

## Tests

One test module per category:
- `tests/plugins/test_builtin_numeric.py`
- `tests/plugins/test_builtin_format.py`
- `tests/plugins/test_builtin_business.py`

Each plugin gets at minimum:
- Happy path (clear winner)
- Single non-null
- All-null returns (None, 0.0)
- Edge case specific to the plugin (e.g. `numeric_max` with strings
  that happen to parse as numbers; `email_normalize` with
  plus-addressing variants)

Plus a discovery test (`tests/plugins/test_builtin_discovery.py`)
verifying `PluginRegistry.discover()` finds all 10 by name.

## Out of scope (v1.19+)

- `numeric_median` / `numeric_weighted_average` (cheap to add later;
  starting with the 3 most-asked-for)
- `url_canonical` (URL canonicalization has edge cases that warrant
  their own spec)
- `verified_value_or_null` (KYC-shaped; needs a source-verification
  registry that's bigger than a single plugin)
- `dnc_safe_phone` (Do Not Call lookup; external service dependency)

## Kill criterion

- All 10 plugins auto-registered after `PluginRegistry.discover()`
- Each plugin has >= 3 unit tests; all pass
- Existing `test_custom_golden_strategy.py` tests still pass (plugin
  protocol unchanged)
- Documentation: README has a "Predefined plugins" section listing
  all 10 with one-line descriptions
