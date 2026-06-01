"""Regression tests for core/quality.py finding serialization.

`_scan_only` (the ``fix_mode="none"`` path used by the MCP ``scan_quality``
tool and the A2A quality skill) serializes goldencheck ``Finding`` objects
into plain dicts. It previously read ``f.rule_id`` / ``f.rows_affected``,
which don't exist on the dataclass (the fields are ``check`` /
``affected_rows`` — see goldencheck/models/finding.py). The resulting
AttributeError was swallowed by the caller as a "quality-scan warning",
silently dropping every finding from the scan-only output.
"""
from __future__ import annotations

import polars as pl
import pytest
from goldenmatch.core import quality

pytest.importorskip("goldencheck")

from goldencheck.models.finding import Finding, Severity  # noqa: E402


def _fake_finding() -> Finding:
    return Finding(
        severity=Severity.WARNING,
        column="email",
        check="null_check",
        message="2 null values",
        affected_rows=2,
        confidence=0.9,
    )


def test_scan_only_serializes_finding_fields(monkeypatch):
    """`_scan_only` must read `check`/`affected_rows` off the Finding,
    not the non-existent `rule_id`/`rows_affected`."""
    monkeypatch.setattr(
        quality, "_scan_findings", lambda df, domain: [_fake_finding()]
    )
    df = pl.DataFrame({"email": ["a@x.com", None]})

    out_df, issues = quality._scan_only(df, "silent", None)

    assert out_df is df
    assert len(issues) == 1
    issue = issues[0]
    # Serialized dict keys are the MCP scan_quality contract — unchanged.
    assert issue["rule"] == "null_check"
    assert issue["rows_affected"] == 2
    assert issue["column"] == "email"
    assert issue["confidence"] == 0.9
    # Severity is the lowercase enum NAME (string), not the IntEnum value —
    # the web /api/v1/quality router does `severity.lower() == "error"`.
    assert issue["severity"] == "warning"


def test_run_quality_check_scan_only_does_not_swallow(monkeypatch):
    """End-to-end through run_quality_check with fix_mode='none': a finding
    must surface as an issue dict, not vanish behind an AttributeError."""
    monkeypatch.setattr(
        quality, "_scan_findings", lambda df, domain: [_fake_finding()]
    )

    class _Cfg:
        mode = "silent"
        fix_mode = "none"
        domain = None
        enabled = True

    df = pl.DataFrame({"email": ["a@x.com", None]})
    out_df, issues = quality.run_quality_check(df, _Cfg())

    assert out_df is df
    assert [i["rule"] for i in issues] == ["null_check"]
