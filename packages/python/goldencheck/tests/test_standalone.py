"""Verify GoldenCheck works completely standalone without Golden Suite."""
from __future__ import annotations

from pathlib import Path

FIXTURE = Path(__file__).parent / "fixtures" / "simple.csv"


def test_scan_standalone():
    from goldencheck import scan_file, apply_confidence_downgrade
    findings, profile = scan_file(FIXTURE)
    findings = apply_confidence_downgrade(findings, llm_boost=False)
    assert isinstance(findings, list)
    assert profile.row_count > 0


def test_handoff_standalone():
    from goldencheck.agent.handoff import generate_handoff
    from goldencheck import scan_file, apply_confidence_downgrade

    findings, profile = scan_file(FIXTURE)
    findings = apply_confidence_downgrade(findings, llm_boost=False)
    result = generate_handoff(
        file_path=str(FIXTURE),
        findings=findings,
        profile=profile,
        pinned_rules=[],
        review_pending=0,
        dismissed=0,
        job_name="standalone-test",
    )
    assert result["source_tool"] == "goldencheck"
    assert result["attestation"] in ("PASS", "PASS_WITH_WARNINGS", "REVIEW_REQUIRED", "FAIL")


def test_evaluate_standalone():
    from goldencheck.engine.evaluate import evaluate_scan
    result = evaluate_scan([], [])
    assert result["f1"] == 1.0


def test_settings_standalone():
    from goldencheck.config.settings import load_settings, DEFAULT_SETTINGS
    s = load_settings(Path("/nonexistent/path.yaml"))
    assert s == DEFAULT_SETTINGS


def test_config_standalone():
    from goldencheck import GoldenCheckConfig, load_config, save_config
    assert GoldenCheckConfig is not None
    assert load_config is not None
    assert save_config is not None


def test_triage_standalone():
    from goldencheck import auto_triage, Finding, Severity
    f = Finding(severity=Severity.WARNING, column="x", check="test", message="m", confidence=0.9)
    result = auto_triage([f])
    assert len(result.pin) == 1
