"""Phase 5: Explainer integration tests for ReviewQueue + Correction why."""
from __future__ import annotations

import polars as pl
from goldenmatch.core.review_queue import ReviewItem, ReviewQueue


def _df():
    return pl.DataFrame({
        "__row_id__": [1, 2, 3],
        "name": ["Acme Corp", "Acme LLC", "Beta Inc"],
        "zip": ["10001", "10001", "20002"],
    })


def test_review_item_has_why_field_default_none():
    """ReviewItem dataclass exposes a `why` attribute, defaulting to None."""
    item = ReviewItem(job_name="j", id_a=1, id_b=2, score=0.85, explanation="x")
    assert hasattr(item, "why")
    assert item.why is None


def test_review_queue_add_populates_why_when_df_provided():
    """ReviewQueue.add() computes a deterministic `why` when df + fields are wired."""
    df = _df()
    rq = ReviewQueue(df=df, matchkey_fields=["name", "zip"])
    rq.add("job", 1, 2, 0.85, explanation="legacy explanation")
    pending = rq.list_pending("job")
    assert len(pending) == 1
    item = pending[0]
    assert isinstance(item.why, str)
    assert item.why  # non-empty
    # Deterministic explainer mentions the field names or values
    assert "name" in item.why or "zip" in item.why or "Acme" in item.why
    assert len(item.why) <= 240  # one short sentence, generous cap


def test_review_queue_add_no_df_leaves_why_none():
    """Without df/matchkey_fields the queue gracefully leaves why=None."""
    rq = ReviewQueue()
    rq.add("job", 1, 2, 0.85, explanation="x")
    item = rq.list_pending("job")[0]
    assert item.why is None


def test_why_for_correction_helper_returns_string():
    """The shared helper produces a non-empty string from a Correction-like input."""
    from goldenmatch.core.review_queue import why_for_correction

    df = _df()
    out = why_for_correction(1, 2, df, ["name", "zip"], score=0.85)
    assert isinstance(out, str)
    assert out
    assert len(out) <= 240


def test_why_for_correction_handles_missing_rows():
    """Helper falls back to a non-empty string even for unknown row IDs."""
    from goldenmatch.core.review_queue import why_for_correction

    df = _df()
    out = why_for_correction(999, 998, df, ["name", "zip"], score=0.5)
    assert isinstance(out, str)
    assert out


def test_why_for_correction_handles_no_df():
    from goldenmatch.core.review_queue import why_for_correction

    out = why_for_correction(1, 2, None, None, score=0.85)
    assert isinstance(out, str)
    assert out  # generic fallback


# ── LLM upgrade path ─────────────────────────────────────────────────────


def test_llm_explain_pair_uses_provider_when_key_set(monkeypatch):
    """When OPENAI_API_KEY is set, llm_explain_pair calls the provider."""
    from goldenmatch.core import llm_scorer

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    captured = {}

    def fake_openai(prompt, api_key, model, max_tokens=100):
        captured["prompt"] = prompt
        captured["model"] = model
        return ("These records share a name and zip code.", 30, 12)

    monkeypatch.setattr(llm_scorer, "_call_openai", fake_openai)

    out = llm_scorer.llm_explain_pair(
        row_a={"name": "Acme Corp", "zip": "10001"},
        row_b={"name": "Acme LLC",  "zip": "10001"},
        score=0.85,
    )
    assert isinstance(out, str)
    assert out
    assert "name" in captured["prompt"].lower() or "entity" in captured["prompt"].lower()
    # Output gets clipped to single short sentence (<= 240 chars)
    assert len(out) <= 240


def test_llm_explain_pair_falls_back_on_error(monkeypatch):
    """Any exception in the LLM path returns a deterministic fallback (never raises)."""
    from goldenmatch.core import llm_scorer

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    def boom(*args, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(llm_scorer, "_call_openai", boom)

    out = llm_scorer.llm_explain_pair(
        row_a={"name": "Acme"}, row_b={"name": "Beta"}, score=0.6,
    )
    assert isinstance(out, str)
    assert out  # fallback string


def test_llm_explain_pair_no_key_returns_deterministic(monkeypatch):
    """No API key -> deterministic path, no network attempt."""
    from goldenmatch.core import llm_scorer

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    # If _call_openai is touched, raise to fail the test.
    def fail(*a, **kw):
        raise AssertionError("LLM should not be called without an API key")

    monkeypatch.setattr(llm_scorer, "_call_openai", fail)

    out = llm_scorer.llm_explain_pair(
        row_a={"name": "Acme"}, row_b={"name": "Beta"}, score=0.6,
    )
    assert isinstance(out, str) and out


def test_review_queue_uses_llm_when_enabled(monkeypatch):
    """ReviewQueue with use_llm_explainer=True routes through llm_explain_pair."""
    from goldenmatch.core import llm_scorer

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(
        llm_scorer, "_call_openai",
        lambda prompt, api_key, model, max_tokens=100: ("LLM_SENTINEL_PROSE", 10, 5),
    )

    df = _df()
    rq = ReviewQueue(
        df=df,
        matchkey_fields=["name", "zip"],
        use_llm_explainer=True,
    )
    rq.add("job", 1, 2, 0.85, explanation="x")
    item = rq.list_pending("job")[0]
    assert "LLM_SENTINEL_PROSE" in item.why
