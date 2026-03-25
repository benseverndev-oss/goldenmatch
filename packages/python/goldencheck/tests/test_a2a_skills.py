"""Tests for the A2A skill dispatch functions.

These tests call dispatch_skill() directly — no aiohttp required.
"""
from __future__ import annotations

import importlib
from pathlib import Path


# Import dispatch_skill directly from the module to avoid a2a/__init__.py's aiohttp import
skills_mod = importlib.import_module("goldencheck.a2a.skills")
dispatch_skill = skills_mod.dispatch_skill

SIMPLE_CSV = str(Path(__file__).resolve().parent / "fixtures" / "simple.csv")


def _make_message(**data: object) -> dict:
    """Build an A2A message with a single data part."""
    return {"role": "user", "parts": [{"type": "data", "data": data}]}


# ── 1. scan ────────────────────────────────────────────────────────────────


def test_dispatch_scan():
    msg = _make_message(file_path=SIMPLE_CSV)
    result = dispatch_skill("scan", msg)

    assert "error" not in result
    assert "findings" in result
    assert isinstance(result["findings"], list)
    assert "health" in result
    assert "grade" in result["health"]
    assert "score" in result["health"]
    assert result["row_count"] > 0
    assert result["column_count"] > 0


# ── 2. validate ────────────────────────────────────────────────────────────


def test_dispatch_validate():
    """validate with no config file should return an error gracefully."""
    msg = _make_message(file_path=SIMPLE_CSV, config_path="nonexistent.yml")
    result = dispatch_skill("validate", msg)

    # No config → returns error dict rather than raising
    assert "error" in result
    assert "not found" in result["error"].lower() or "Config" in result["error"]


# ── 3. analyze_data ───────────────────────────────────────────────────────


def test_dispatch_analyze_data():
    msg = _make_message(file_path=SIMPLE_CSV)
    result = dispatch_skill("analyze_data", msg)

    assert "error" not in result
    assert "domain" in result
    assert "sample_strategy" in result or "profiler_strategy" in result
    assert "alternatives" in result


# ── 4. configure ──────────────────────────────────────────────────────────


def test_dispatch_configure():
    msg = _make_message(file_path=SIMPLE_CSV)
    result = dispatch_skill("configure", msg)

    assert "error" not in result
    assert "config" in result
    assert isinstance(result["config"], dict)
    assert "version" in result["config"]
    assert "columns" in result["config"]
    assert "pinned_count" in result
    assert "review_count" in result
    assert "dismissed_count" in result


# ── 5. explain ─────────────────────────────────────────────────────────────


def test_dispatch_explain():
    """Run a scan first to discover a real finding, then explain it."""
    scan_msg = _make_message(file_path=SIMPLE_CSV)
    scan_result = dispatch_skill("scan", scan_msg)

    # Pick the first finding to explain
    assert scan_result["findings"], "Expected at least one finding from simple.csv"
    first = scan_result["findings"][0]

    explain_msg = _make_message(
        file_path=SIMPLE_CSV,
        column=first["column"],
        check=first["check"],
    )
    result = dispatch_skill("explain", explain_msg)

    # explain_finding returns a dict — should not be an error
    assert "error" not in result


# ── 6. review (empty) ─────────────────────────────────────────────────────


def test_dispatch_review_empty():
    """Listing review items for a non-existent job should return empty list."""
    msg = _make_message(action="list", job_name="no-such-job-xyz")
    result = dispatch_skill("review", msg)

    assert "error" not in result
    assert result["pending"] == []
    assert "stats" in result


# ── 7. compare_domains ────────────────────────────────────────────────────


def test_dispatch_compare_domains():
    msg = _make_message(file_path=SIMPLE_CSV)
    result = dispatch_skill("compare_domains", msg)

    # compare_domains returns a dict with domain results
    assert isinstance(result, dict)
    assert "error" not in result


# ── 8. fix ─────────────────────────────────────────────────────────────────


def test_dispatch_fix():
    msg = _make_message(file_path=SIMPLE_CSV, mode="safe")
    result = dispatch_skill("fix", msg)

    assert "error" not in result
    assert result["mode"] == "safe"
    assert "total_rows_fixed" in result
    assert "fixes" in result
    assert isinstance(result["fixes"], list)


# ── 9. handoff ─────────────────────────────────────────────────────────────


def test_dispatch_handoff():
    msg = _make_message(file_path=SIMPLE_CSV)
    result = dispatch_skill("handoff", msg)

    assert "error" not in result
    assert "attestation" in result or "handoff" in result or isinstance(result, dict)
    # generate_handoff should produce an attestation key
    assert "attestation" in result


# ── 10. unknown skill ─────────────────────────────────────────────────────


def test_dispatch_unknown_skill():
    msg = _make_message(file_path=SIMPLE_CSV)
    result = dispatch_skill("totally_bogus_skill", msg)

    assert "error" in result
    assert "Unknown skill" in result["error"]
