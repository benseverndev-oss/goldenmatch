import goldenmatch as gm
import polars as pl
from goldenmatch.config.schemas import (
    BlockingConfig,
    BudgetConfig,
    GoldenMatchConfig,
    LLMScorerConfig,
    MatchkeyConfig,
    MatchkeyField,
)

_FAKE = {"total_cost_usd": 0.0042, "total_calls": 3, "total_input_tokens": 1200,
         "total_output_tokens": 90, "budget_remaining_pct": 99.0,
         "budget_exhausted": False, "models_used": {"gpt-4o-mini": 3}}


def _df():
    return pl.DataFrame({"name": ["Acme Inc", "Acme Incorporated", "Globex"]})


def test_llm_cost_none_without_scorer():
    res = gm.dedupe_df(_df(), fuzzy={"name": 0.6})
    assert res.llm_cost is None


def test_llm_cost_surfaced_when_scorer_runs(monkeypatch):
    # Patch llm_score_pairs at its module so the pipeline's local import picks up the fake.
    import goldenmatch.core.llm_scorer as s

    def fake(pairs, df, *a, return_budget=False, **k):
        return (pairs, _FAKE) if return_budget else pairs

    monkeypatch.setattr(s, "llm_score_pairs", fake)
    cfg = GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(
            name="fuzzy", type="weighted", threshold=0.6,
            fields=[MatchkeyField(field="name", scorer="token_sort", weight=1.0,
                                  transforms=["lowercase", "strip"])],
        )],
        blocking=BlockingConfig(keys=[], auto_suggest=True),
        llm_scorer=LLMScorerConfig(enabled=True, budget=BudgetConfig()),
    )
    res = gm.dedupe_df(_df(), config=cfg)
    assert res.llm_cost is not None
    assert res.llm_cost["llm_calls"] == 3
    assert res.llm_cost["llm_usd"] == 0.0042
    assert res.llm_cost["llm_tokens"] == 1290  # input + output
