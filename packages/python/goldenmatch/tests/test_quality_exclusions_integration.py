"""Integration tests for #404: auto_configure_df honors the exclusion
list from `detect_autoconfig_exclusions`.

The foundation PR has unit tests for the detectors. This file proves
the user-visible behavior change: poisoned columns are filtered out
of the config auto-config returns.
"""

from __future__ import annotations

import datetime

import polars as pl
import pytest


def _poisoned_person_df(n: int = 100) -> pl.DataFrame:
    """A person-shaped frame with several intentionally poisoned
    columns. Auto-config should pick name/city, not external_id /
    created_at / record_hash etc.
    """
    return pl.DataFrame({
        "first_name": [f"name_{i}" for i in range(n)],
        "last_name": [f"smith_{i % 10}" for i in range(n)],
        "city": ["NYC", "LA", "SF", "Boston", "Seattle"] * (n // 5),
        # ---- poisoned columns below ----
        "external_id": [f"ext_{i:08d}" for i in range(n)],  # foreign_system_id
        "created_at": [
            datetime.datetime(2026, 1, 1) + datetime.timedelta(seconds=i)
            for i in range(n)
        ],  # audit_column
        "record_hash": [f"{i:032x}" for i in range(n)],  # system_hash
    })


def _all_matchkey_field_names(config) -> set[str]:
    """Walk the committed config and collect every column name
    referenced in any matchkey field. Lets us assert "X is not in any
    matchkey" without caring about the matchkey shape."""
    referenced: set[str] = set()
    matchkeys = config.get_matchkeys() if hasattr(config, "get_matchkeys") else []
    for mk in matchkeys:
        if getattr(mk, "field", None):
            referenced.add(mk.field)
        for f in (getattr(mk, "fields", None) or []):
            if getattr(f, "field", None):
                referenced.add(f.field)
    return referenced


def _all_blocking_field_names(config) -> set[str]:
    """Walk blocking config and collect every column name referenced."""
    referenced: set[str] = set()
    if not config.blocking or not config.blocking.keys:
        return referenced
    for key in config.blocking.keys:
        for f in (getattr(key, "fields", None) or []):
            referenced.add(f)
    return referenced


def test_autoconfig_committed_config_skips_poisoned_columns():
    """End-to-end: auto_configure_df on a poisoned person frame returns
    a committed config that does NOT reference external_id, created_at,
    or record_hash in any matchkey or blocking key.

    Pins #404's user-visible behavior. If a future regression makes
    auto-config pick a poisoned column again, this fails loudly.
    """
    from goldenmatch.core.autoconfig import auto_configure_df

    df = _poisoned_person_df(n=200)
    config = auto_configure_df(df, confidence_required=False, _skip_finalize=True)

    matchkey_cols = _all_matchkey_field_names(config)
    blocking_cols = _all_blocking_field_names(config)
    all_referenced = matchkey_cols | blocking_cols

    for poisoned_col in ["external_id", "created_at", "record_hash"]:
        assert poisoned_col not in all_referenced, (
            f"{poisoned_col!r} was excluded but auto-config still "
            f"referenced it. matchkeys={matchkey_cols}, "
            f"blocking={blocking_cols}. See #404."
        )


def test_autoconfig_exclusions_logged_at_info_level(caplog):
    """Every exclusion logs at INFO with the detector + reason so the
    user can see what was filtered without inspecting the postflight
    report."""
    import logging

    from goldenmatch.core.autoconfig import auto_configure_df

    df = _poisoned_person_df(n=100)
    with caplog.at_level(logging.INFO, logger="goldenmatch.core.autoconfig"):
        auto_configure_df(df, confidence_required=False, _skip_finalize=True)

    log_messages = [r.getMessage() for r in caplog.records]
    exclusion_logs = [m for m in log_messages if "Auto-config exclusion" in m]
    # Three poisoned columns -> three exclusion log lines.
    assert len(exclusion_logs) >= 3, (
        f"expected at least 3 exclusion log lines, got {len(exclusion_logs)}: "
        f"{exclusion_logs}"
    )
    # Each detector name should appear in at least one log line.
    joined = "\n".join(exclusion_logs)
    for detector in ["audit_column", "foreign_system_id", "system_hash"]:
        assert detector in joined, f"detector={detector} not in logs:\n{joined}"


def test_autoconfig_exclusions_populates_context_var():
    """PostflightReport reads _LAST_AUTOCONFIG_EXCLUSIONS to render
    the audit trail. Assert it gets populated after a run."""
    from goldenmatch.core.autoconfig import (
        _LAST_AUTOCONFIG_EXCLUSIONS,
        auto_configure_df,
    )

    df = _poisoned_person_df(n=100)
    auto_configure_df(df, confidence_required=False, _skip_finalize=True)

    exclusions = _LAST_AUTOCONFIG_EXCLUSIONS.get()
    assert exclusions is not None
    excluded_cols = {ec.column for ec in exclusions}
    assert "external_id" in excluded_cols
    assert "created_at" in excluded_cols
    assert "record_hash" in excluded_cols


def test_force_include_via_env_var_rescues_column(monkeypatch):
    """`GOLDENMATCH_AUTOCONFIG_FORCE_INCLUDE=record_hash` rescues
    a column from auto-detection. Pattern: legitimate `email_hash` for
    PPRL where the hash IS the identifier."""
    from goldenmatch.core.autoconfig import (
        _LAST_AUTOCONFIG_EXCLUSIONS,
        auto_configure_df,
    )

    monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_FORCE_INCLUDE", "record_hash")

    df = _poisoned_person_df(n=100)
    auto_configure_df(df, confidence_required=False, _skip_finalize=True)

    exclusions = _LAST_AUTOCONFIG_EXCLUSIONS.get() or []
    excluded_cols = {ec.column for ec in exclusions}
    # external_id and created_at still excluded (other detectors fired).
    assert "external_id" in excluded_cols
    assert "created_at" in excluded_cols
    # record_hash was rescued.
    assert "record_hash" not in excluded_cols


def test_force_exclude_via_env_var_adds_extra_column(monkeypatch):
    """`GOLDENMATCH_AUTOCONFIG_FORCE_EXCLUDE=city` excludes a column
    the detectors wouldn't catch on their own."""
    from goldenmatch.core.autoconfig import (
        _LAST_AUTOCONFIG_EXCLUSIONS,
        auto_configure_df,
    )

    monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_FORCE_EXCLUDE", "city")

    df = _poisoned_person_df(n=100)
    auto_configure_df(df, confidence_required=False, _skip_finalize=True)

    exclusions = _LAST_AUTOCONFIG_EXCLUSIONS.get() or []
    excluded = {ec.column: ec.detector for ec in exclusions}
    assert excluded.get("city") == "user_force_exclude"


def test_clean_dataframe_produces_no_exclusions():
    """A clean person frame produces an empty exclusion list."""
    from goldenmatch.core.autoconfig import (
        _LAST_AUTOCONFIG_EXCLUSIONS,
        auto_configure_df,
    )

    df = pl.DataFrame({
        "first_name": ["Alice", "Bob", "Carol", "Dave", "Eve"] * 20,
        "last_name": ["Smith", "Jones", "Doe", "White", "Black"] * 20,
        "city": ["NYC", "LA", "SF", "Boston", "Seattle"] * 20,
    })
    auto_configure_df(df, confidence_required=False, _skip_finalize=True)

    exclusions = _LAST_AUTOCONFIG_EXCLUSIONS.get()
    assert exclusions == []


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
