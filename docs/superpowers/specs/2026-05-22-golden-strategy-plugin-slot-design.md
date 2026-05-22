# Custom golden-strategy plugin slot

**Status:** spec → implemented in v1.18.1
**Date:** 2026-05-22

## Problem

`VALID_STRATEGIES` is a closed enum: 5 original + 3 added in v1.18.0
(`longest_value`, `unanimous_or_null`, `confidence_majority`). Users
with bespoke business rules (legal-priority hierarchies, regulatory
"newest non-superseded record wins", per-jurisdiction lookups) need
to write Python code, not extend YAML.

The plugin scaffolding already exists:
- Entry-point group `goldenmatch.plugins.golden_strategy`
- `PluginRegistry._golden_strategies` slot + `register_golden_strategy()`
  + `get_golden_strategy()`
- `GoldenStrategyPlugin` protocol in `plugins/base.py` (weak signature)

What's missing: dispatch + validator + a beefier protocol.

## Decision

### Protocol signature

Rich kwargs matching the internal `merge_field` API. Plugins ignore
what they don't need.

```python
@runtime_checkable
class GoldenStrategyPlugin(Protocol):
    name: str

    def merge(
        self,
        values: list,
        *,
        sources: list[str] | None = None,
        dates: list | None = None,
        quality_weights: list[float] | None = None,
        pair_scores: dict[tuple[int, int], float] | None = None,
        rule_kwargs: dict | None = None,
    ) -> tuple[object, float] | tuple[object, float, int | None]:
        """Merge cluster member values into one survivor.

        Returns either (value, confidence) -- the dispatcher fills idx=0 --
        or (value, confidence, idx) for plugins that want to expose
        provenance.

        `rule_kwargs` carries the per-field GoldenFieldRule.model_dump()
        so plugins can read configuration the user set in YAML
        (date_column, source_priority, or custom keys).
        """
        ...
```

### Strategy string syntax

`strategy="custom:<name>"`. The `custom:` prefix is reserved; built-in
strategies cannot start with it.

- `strategy="custom:legal_priority"` → looks up plugin `legal_priority` in registry.
- `strategy="custom:"` → invalid (empty name).
- `strategy="custom:my-rule"` → invalid (must match `^custom:[a-z_][a-z0-9_]*$`).

### Validator update

`GoldenFieldRule._validate_strategy` accepts:
1. `strategy in VALID_STRATEGIES`, OR
2. `strategy.startswith("custom:")` AND name matches `^custom:[a-z_][a-z0-9_]*$`.

At validate time we DO NOT verify the plugin exists — the rule may be
loaded before plugins are discovered. Existence is checked at dispatch
time in `merge_field`.

### Dispatch in `merge_field`

```python
if strategy.startswith("custom:"):
    plugin_name = strategy.removeprefix("custom:")
    return _dispatch_custom_strategy(
        plugin_name, non_null, values, rule,
        sources=sources, dates=dates,
        quality_weights=quality_weights, pair_scores=pair_scores,
    )
```

`_dispatch_custom_strategy`:
- Look up plugin via `PluginRegistry.instance().get_golden_strategy(name)`.
- If not found: emit a WARNING + fall back to `most_complete` (defensive default).
- If found: call `plugin.merge(values, **kwargs)`.
- Result handling: accept 2-tuple (synthesize `idx=0`) or 3-tuple.
- If plugin raises: emit a WARNING with the plugin name + exception, fall back to `most_complete`.

Strict mode opt-in: `GOLDENMATCH_GOLDEN_STRATEGY_STRICT=1` reraises plugin errors instead of falling back. Default off (one bad plugin shouldn't kill the whole golden build).

## Implementation

**Files changed:**
- `plugins/base.py` — beef up `GoldenStrategyPlugin` protocol with the rich signature above.
- `core/golden.py` — `_dispatch_custom_strategy` + `merge_field` dispatch.
- `config/schemas.py` — `GoldenFieldRule._validate_strategy` accepts `custom:<name>`.

**Files added:**
- `tests/test_custom_golden_strategy.py` — protocol acceptance + dispatch + fallback + strict mode + name validation.

## Tests

- `test_custom_strategy_name_passes_validation` — `custom:foo` rule is constructable
- `test_invalid_custom_name_rejected_by_validator` — `custom:`, `custom:has-hyphen`, `custom:HasCaps` all fail validation
- `test_dispatch_calls_plugin_with_kwargs` — verify plugin receives values + sources + dates + quality_weights + pair_scores
- `test_dispatch_accepts_two_tuple_result` — plugin returning `(value, conf)` → synthesized idx=0
- `test_dispatch_accepts_three_tuple_result` — plugin returning `(value, conf, idx)` → idx preserved
- `test_dispatch_falls_back_on_missing_plugin` — `custom:nonexistent` → WARNING + most_complete
- `test_dispatch_falls_back_on_plugin_exception` — plugin raises → WARNING + most_complete
- `test_strict_mode_reraises_plugin_exception` — `GOLDENMATCH_GOLDEN_STRATEGY_STRICT=1` → exception bubbles
- `test_rule_kwargs_pass_through` — plugin reads `date_column` / custom field from `rule_kwargs`

## Backward compat

- All existing strategies unchanged.
- The pre-v1.18.1 `GoldenStrategyPlugin` had `merge(values, sources=None) -> (val, conf)`. Any plugin authors out there (none known) will need to add `**kwargs` to their signature, or accept the new protocol. We bump the protocol's first stable release at v1.18.1; pre-v1.18.1 was not advertised as usable.

## Out of scope (v1.19+ candidates)

- **Refiner picks `custom:<name>`** from heuristics. Refiner currently sticks to built-ins; calling user code is opt-in via explicit `field_rules` only.
- **Multi-arg signatures** (cluster-level batch merge, not just per-field). The current per-field API is simpler; batch is a possible v2.
- **Async plugins** for remote-lookup strategies. Async dispatch would touch the whole golden pipeline; deferred until a user files for it.

## Kill criterion

- Built-in strategies pass existing benchmark suite
- Custom-plugin smoke test: a `LegalPriorityStrategy` test fixture returns the expected value on a synthetic 3-source fixture
