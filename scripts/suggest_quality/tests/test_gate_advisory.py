"""Gate-verdict tests: an ADVISORY (dataset, metric) is reported but never gates.

Pins the fix for the flaky `ncvr_synthetic/suggester_prec` signal: ncvr_synthetic
commits a RED config under a wall-clock budget, so its suggester_prec flips
1.0<->0.0 run-to-run independent of the code. It must be visible but non-gating.
"""
from scripts.suggest_quality import cli


def _baseline(datasets: dict) -> dict:
    return {"datasets": datasets}


def test_advisory_pair_is_registered() -> None:
    assert ("ncvr_synthetic", "suggester_prec") in cli._GATE_ADVISORY


def test_advisory_metric_does_not_fail_gate(monkeypatch, capsys) -> None:
    # A full 1.0 -> 0.0 regression on the advisory pair must NOT flip the verdict.
    monkeypatch.setattr(
        cli, "_loads_baseline",
        lambda: _baseline({"ncvr_synthetic": {"suggester_prec": 1.0}}),
    )
    results = {"ncvr_synthetic": {"suggester_prec": 0.0}}
    rc = cli._cmd_gate(results, {}, "test", "deadbeef", 0.01)
    assert rc == 0  # PASS despite the advisory regression
    out = capsys.readouterr().out
    assert "ADVISORY" in out  # still reported in the table


def test_non_advisory_regression_still_fails_gate(monkeypatch) -> None:
    # The SAME metric on a non-advisory dataset still gates normally.
    monkeypatch.setattr(
        cli, "_loads_baseline",
        lambda: _baseline({"synthetic": {"suggester_prec": 1.0}}),
    )
    results = {"synthetic": {"suggester_prec": 0.0}}
    rc = cli._cmd_gate(results, {}, "test", "deadbeef", 0.01)
    assert rc == 1  # FAIL — synthetic/suggester_prec is a real gate signal
