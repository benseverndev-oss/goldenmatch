from goldencheck.llm.budget import estimate_cost, check_budget, get_budget_limit, CostReport

def test_estimate_cost_known_model():
    # gpt-4o-mini: input=0.00015/1k, output=0.0006/1k
    cost = estimate_cost(2000, 500, "gpt-4o-mini")
    expected = (2000/1000) * 0.00015 + (500/1000) * 0.0006
    assert abs(cost - expected) < 0.0001

def test_estimate_cost_unknown_model():
    cost = estimate_cost(1000, 1000, "unknown-model")
    assert cost > 0  # uses default rates

def test_check_budget_no_limit(monkeypatch):
    monkeypatch.delenv("GOLDENCHECK_LLM_BUDGET", raising=False)
    assert check_budget(100.0) is True  # no limit = always OK

def test_check_budget_within_limit(monkeypatch):
    monkeypatch.setenv("GOLDENCHECK_LLM_BUDGET", "1.00")
    assert check_budget(0.01) is True

def test_check_budget_exceeds_limit(monkeypatch):
    monkeypatch.setenv("GOLDENCHECK_LLM_BUDGET", "0.005")
    assert check_budget(0.01) is False

def test_cost_report():
    report = CostReport()
    report.record(2000, 500, "gpt-4o-mini")
    summary = report.summary()
    assert summary["model"] == "gpt-4o-mini"
    assert summary["input_tokens"] == 2000
    assert summary["output_tokens"] == 500
    assert summary["cost_usd"] > 0

def test_get_budget_limit_invalid(monkeypatch):
    monkeypatch.setenv("GOLDENCHECK_LLM_BUDGET", "not-a-number")
    assert get_budget_limit() is None
