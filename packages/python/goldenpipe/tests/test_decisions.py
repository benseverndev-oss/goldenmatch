"""Tests for adaptive pipeline decisions -- no tool imports needed."""
from dataclasses import dataclass
from goldenpipe.decisions import decide_flow, decide_match, FlowDecision, MatchDecision


@dataclass
class FakeFinding:
    check: str = "whitespace"
    severity: str = "warning"


@dataclass
class FakeScanResult:
    findings: list = None

    def __post_init__(self):
        if self.findings is None:
            self.findings = []


class TestDecideFlow:
    def test_no_findings_skips(self):
        result = FakeScanResult(findings=[])
        decision = decide_flow(result)
        assert decision.skip is True
        assert "No quality issues" in decision.reason

    def test_fixable_findings_runs(self):
        result = FakeScanResult(findings=[FakeFinding()])
        decision = decide_flow(result)
        assert decision.skip is False
        assert "1 fixable" in decision.reason

    def test_fatal_only_aborts(self):
        result = FakeScanResult(findings=[FakeFinding(severity="critical")])
        decision = decide_flow(result)
        assert decision.skip is True
        assert decision.abort is True

    def test_mixed_runs_fixable(self):
        result = FakeScanResult(findings=[
            FakeFinding(severity="critical"),
            FakeFinding(severity="warning"),
        ])
        decision = decide_flow(result)
        assert decision.skip is False
        assert len(decision.findings) == 1

    def test_none_check_result_skips(self):
        decision = decide_flow(None)
        assert decision.skip is True

    def test_returns_flow_decision(self):
        decision = decide_flow(FakeScanResult())
        assert isinstance(decision, FlowDecision)


class TestDecideMatch:
    def test_auto_by_default(self):
        decision = decide_match(None, 100)
        assert decision.strategy == "auto"
        assert decision.skip is False

    def test_too_few_rows_skips(self):
        decision = decide_match(None, 1)
        assert decision.skip is True
        assert "1 rows" in decision.reason

    def test_zero_rows_skips(self):
        decision = decide_match(None, 0)
        assert decision.skip is True

    def test_strategy_override(self):
        decision = decide_match(None, 100, strategy_override="pprl")
        assert decision.strategy == "pprl"
        assert "User specified" in decision.reason

    def test_pii_detection_routes_pprl(self):
        result = FakeScanResult(findings=[FakeFinding(check="pii_detection")])
        decision = decide_match(result, 100)
        assert decision.strategy == "pprl"

    def test_no_pii_stays_auto(self):
        result = FakeScanResult(findings=[FakeFinding(check="whitespace")])
        decision = decide_match(result, 100)
        assert decision.strategy == "auto"

    def test_returns_match_decision(self):
        decision = decide_match(None, 50)
        assert isinstance(decision, MatchDecision)
