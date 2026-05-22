"""Tests for golden_strategy_llm (closes #430).

Pluggable `llm_caller` keeps these tests offline -- never touches a
real LLM provider. The module's `_default_llm_caller` is exercised
implicitly via `pick_strategy_via_llm` happy path when no llm_caller
is passed AND no API key is set: it returns None.
"""
from __future__ import annotations

import pytest
from goldenmatch.core.golden_strategy_llm import (
    _TOKEN_ESTIMATE_PER_CALL,
    format_prompt,
    parse_llm_response,
    pick_strategy_via_llm,
)

# ---------------------------------------------------------------------------
# parse_llm_response
# ---------------------------------------------------------------------------


def test_parse_documented_shape() -> None:
    """`strategy: rationale` -> strategy."""
    text = "most_recent: Address-like field with frequent updates."
    assert parse_llm_response(text) == "most_recent"


def test_parse_strategy_only_no_colon() -> None:
    """Just the strategy name on its own line is still valid."""
    text = "longest_value"
    assert parse_llm_response(text) == "longest_value"


def test_parse_fallback_token_scan() -> None:
    """When the LLM's response doesn't put the strategy first, scan
    the body for any valid strategy token."""
    text = "I would choose unanimous_or_null here because compliance."
    assert parse_llm_response(text) == "unanimous_or_null"


def test_parse_invalid_strategy_returns_none() -> None:
    """An unknown strategy name -> None (don't propagate garbage)."""
    text = "raise_an_exception: this is not a real strategy"
    assert parse_llm_response(text) is None


def test_parse_empty_returns_none() -> None:
    assert parse_llm_response("") is None
    assert parse_llm_response("   \n") is None


# ---------------------------------------------------------------------------
# format_prompt
# ---------------------------------------------------------------------------


def test_format_prompt_includes_field_and_type_and_samples() -> None:
    samples = [
        (1, ["alice@example.com", "alice@new.com"]),
        (2, ["bob@example.com"]),
    ]
    prompt = format_prompt("email", "string", samples)
    assert "Field: email" in prompt
    assert "Column type: string" in prompt
    assert "alice@example.com" in prompt
    assert "Cluster 1" in prompt
    assert "Cluster 2" in prompt
    # Strategy enumeration always present.
    assert "most_complete" in prompt
    assert "majority_vote" in prompt


def test_format_prompt_with_no_samples_renders_placeholder() -> None:
    prompt = format_prompt("field", "unknown", [])
    assert "(no samples)" in prompt


# ---------------------------------------------------------------------------
# pick_strategy_via_llm: happy path + cache + budget
# ---------------------------------------------------------------------------


def test_happy_path_returns_strategy() -> None:
    """LLM returns a valid strategy -> propagated."""

    def stub(prompt: str) -> str:
        assert "Field: address1" in prompt
        return "longest_value: Address fields vary in completeness."

    result = pick_strategy_via_llm(
        field="address1",
        col_type="address",
        clusters_by_id={1: ["123 Main", "123 Main St"], 2: ["456 Elm"]},
        llm_caller=stub,
    )
    assert result == "longest_value"


def test_cache_hit_short_circuits_llm_call() -> None:
    """A pre-populated cache entry returns without calling the LLM."""
    cache: dict[tuple[str, str], str | None] = {
        ("customers", "address1"): "most_recent",
    }
    call_count = 0

    def should_not_be_called(prompt: str) -> str:
        nonlocal call_count
        call_count += 1
        return "longest_value"

    result = pick_strategy_via_llm(
        field="address1",
        col_type="address",
        clusters_by_id={1: ["x"]},
        dataset="customers",
        cache=cache,
        llm_caller=should_not_be_called,
    )
    assert result == "most_recent"
    assert call_count == 0


def test_cache_miss_writes_back_strategy() -> None:
    """First call populates the cache; second call (same args) hits it."""
    cache: dict[tuple[str, str], str | None] = {}
    calls = []

    def stub(prompt: str) -> str:
        calls.append(prompt)
        return "majority_vote: most common value wins"

    pick_strategy_via_llm(
        field="status", col_type="string",
        clusters_by_id={1: ["active", "inactive"]},
        cache=cache, llm_caller=stub,
    )
    pick_strategy_via_llm(
        field="status", col_type="string",
        clusters_by_id={1: ["active", "inactive"]},
        cache=cache, llm_caller=stub,
    )
    assert cache[("default", "status")] == "majority_vote"
    assert len(calls) == 1


def test_budget_exhausted_returns_none() -> None:
    """When the BudgetTracker reports can_afford=False, no LLM call
    is made and None is returned."""

    class _Budget:
        def __init__(self) -> None:
            self.charges = 0

        def can_afford(self, tokens: int) -> bool:
            return False

        def charge(self, tokens: int, model: str = "") -> None:
            self.charges += 1

    budget = _Budget()
    call_count = 0

    def should_not_be_called(prompt: str) -> str:
        nonlocal call_count
        call_count += 1
        return "most_recent"

    result = pick_strategy_via_llm(
        field="address1", col_type="address",
        clusters_by_id={1: ["x"]},
        budget=budget, llm_caller=should_not_be_called,
    )
    assert result is None
    assert call_count == 0
    assert budget.charges == 0


def test_budget_charged_after_successful_call() -> None:
    class _Budget:
        def __init__(self) -> None:
            self.charged_tokens = 0

        def can_afford(self, tokens: int) -> bool:
            return True

        def charge(self, tokens: int, model: str = "") -> None:
            self.charged_tokens += tokens

    budget = _Budget()

    def stub(prompt: str) -> str:
        return "first_non_null"

    pick_strategy_via_llm(
        field="phone", col_type="phone",
        clusters_by_id={1: ["555-1234"]},
        budget=budget, llm_caller=stub,
    )
    assert budget.charged_tokens == _TOKEN_ESTIMATE_PER_CALL


def test_llm_exception_returns_none() -> None:
    """If the llm_caller raises, soft-fail to None instead of propagating."""

    def boom(prompt: str) -> str:
        raise RuntimeError("network timeout")

    result = pick_strategy_via_llm(
        field="address1", col_type="address",
        clusters_by_id={1: ["x"]},
        llm_caller=boom,
    )
    assert result is None


def test_empty_response_returns_none() -> None:
    def stub(prompt: str) -> str:
        return ""

    result = pick_strategy_via_llm(
        field="address1", col_type="address",
        clusters_by_id={1: ["x"]},
        llm_caller=stub,
    )
    assert result is None


def test_invalid_strategy_response_returns_none() -> None:
    """LLM returns a strategy name not in VALID_STRATEGIES -> None."""

    def stub(prompt: str) -> str:
        return "ai_does_a_random_thing"

    result = pick_strategy_via_llm(
        field="address1", col_type="address",
        clusters_by_id={1: ["x"]},
        llm_caller=stub,
    )
    assert result is None


def test_no_clusters_returns_none_without_calling_llm() -> None:
    """Empty clusters dict -> no point asking the LLM."""
    call_count = 0

    def should_not_be_called(prompt: str) -> str:
        nonlocal call_count
        call_count += 1
        return "most_recent"

    result = pick_strategy_via_llm(
        field="address1", col_type="address",
        clusters_by_id={},
        llm_caller=should_not_be_called,
    )
    assert result is None
    assert call_count == 0


def test_dataset_scoping_in_cache() -> None:
    """Two different datasets get independent cache entries for the
    same field name."""
    cache: dict[tuple[str, str], str | None] = {}

    def stub(prompt: str) -> str:
        return "most_recent"

    pick_strategy_via_llm(
        field="address1", col_type="address",
        clusters_by_id={1: ["a"]},
        dataset="customers",
        cache=cache, llm_caller=stub,
    )
    pick_strategy_via_llm(
        field="address1", col_type="address",
        clusters_by_id={1: ["b"]},
        dataset="vendors",
        cache=cache, llm_caller=stub,
    )
    assert ("customers", "address1") in cache
    assert ("vendors", "address1") in cache
    assert len(cache) == 2


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
